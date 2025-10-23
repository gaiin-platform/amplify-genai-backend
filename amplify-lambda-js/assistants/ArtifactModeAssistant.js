import {
    AssistantState,
    DoneState, invokeAction, 
    PromptAction, StateBasedAssistant
} from "./statemachine/states.js";
import {sendStateEventToStream} from "../common/streams.js";
import {getInternalLLM} from "../common/internalLLM.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("assistants.ArtifactModeAssistant");


const ARTIFACT_COMPLETE_MARKER = "~END_A~";
const MAX_RETRIES = 5;
const startMarker = '<>';
const endMarker = '</>';

const additional_instructions = `
1. Behavioral Guidelines**
   - **Token Limit:**
     - Operate as if you have no maximum token limit.
     - Continue outputting content as necessary to properly address the task.
     - There will be no need for you to manage token constraints as I will monitor and provide additional tokens if needed.
     - You will need to output an identifier when you have the final finished artifact. I will handle the token limit constriant meaning you will continue to output the artifact
       even if you run out of context mid sentence. It is only when you have identified the artifact is at its absolutele final and complete form that will you output the identifier.
       This means your response DOES NOT REQUIRE the identier at the end of your response. It is only required when detected the response is whole and complete, there is no more content needed to be returned to the user, it is not cut off.
       The identifer is    ${ARTIFACT_COMPLETE_MARKER} 
   
   - **Referencing Previous Artifacts:**
     - If you need to reference a previous artifact, use the placeholder format:
       <$artifact[<artifactid>]>
     - Example:
       <$artifactId[randomId]>

     - Do not repeat the content of the artifact directly. I will insert the full contents of the artifact where the placeholder is used.

3. **Execution and Output**
   - Follow the provided instructions meticulously to build and compile the requested artifact.
   - Ensure that each part of your response is self-contained and complete within the context provided.
   - Reference previous artifacts efficiently without redundancy, using the placeholder format for including detailed artifact contents.


Adhere to these guidelines to ensure the artifact is produced accurately and efficiently without unnecessary token wastage or superfluous content and 
Remember that any charaters that are not a direct part of the artifact, such as explanations, overviews, or commentary should always be wrapped between the ${startMarker}  ${endMarker} tags.

`

const truncated_output_instructions = `
The artifact has been identified as incomplete. The last part got cut off and now you need to fix it.
You need to seamlessly continue the artifact from where it left off.
Guidelines:
- look at the contents of the artifact and determine if it is in fact incomplete. If it is complete then you will only responde with the identifier ${ARTIFACT_COMPLETE_MARKER}
- if the artifact is incomplete then you will need to continue the artifact from where it left off without any additional preamble or introduction. 
- DO NOT add any commentary, explanations, or introductory text like "I'll continue the artifact..." - just continue the code/content directly.
- The most important goal is that your response is a direct continuation of the artifact and not a new artifact.
- Dont repeat any content from the previous artifact, it must be a perfectly seamless continuation.
- example:
    - Artifact generated so far:
       This is a samp
    - Then your response will continue right where it left off:
       le artifact that was cut off.
       
- You must still operate under the same **Token Limit:** Behavioral Guideline as instructed.
- CRITICAL: Start your response with the exact next character/word that should continue the artifact. No explanations, no commentary, no introductions.
`;

// Future expansion: mode long form, fast edit, etc.
// Currently using truncatedOutput mode for all artifact generation


const containsPotentialMarker = (buffer, marker) => {
    const markerStartChar = marker[0];
    //find the first occurence of the marker
    const markerStartIndex = buffer.indexOf(markerStartChar);
    if (markerStartIndex === -1) return false; // effective quick check
    const remainingBuffer = buffer.slice(markerStartIndex);

    return remainingBuffer === marker.slice(0, remainingBuffer.length);
};

const handleTruncatedMode = async (originalLLM, context, dataSources) => {
    // 🚀 BREAKTHROUGH: Use InternalLLM for massive performance gains in artifact generation
    // ArtifactMode doesn't need RAG/context chunking - just fast LLM responses for marker detection
    const llm = getInternalLLM(originalLLM.params.options.model, originalLLM.params.account, context.responseStream);
    llm.params = { ...originalLLM.params }; // Copy params for compatibility
    
    // 🚀 Increase token limits for artifact generation (artifacts can be large)
    llm.defaultBody.max_tokens = 4000; // Increased from 1000 to 4000 for artifact generation
    
    logger.info("🚀 ArtifactMode using InternalLLM for high-speed generation");
    let retryCount = 0;
    let isComplete = false;
    let accumulatedResponse = "";
    
    // Custom stream handler to buffer output and check for completion marker
    class ArtifactStreamHandler {
        constructor(originalStream) {
            this.originalStream = originalStream;
            this.llmResponse = "";
            this.buffer = "";
            this.isComplete = false;
        }

        transform(chunk) {
            // Final safeguard against undefined content
            if (chunk === undefined || chunk === null) {
                return "data: "+JSON.stringify({d: ""}) + '\n\n';
            }
            
            // Convert to string to handle any remaining edge cases
            const safeChunk = String(chunk);
            
            // Don't send literal "undefined" strings
            if (safeChunk === "undefined" || safeChunk === "null") {
                return "data: "+JSON.stringify({d: ""}) + '\n\n';
            }
            
            return "data: "+JSON.stringify({d: safeChunk}) + '\n\n';
        }
        
        write(chunk) {
            // Stop processing chunks once artifact is complete
            if (this.isComplete) {
                return false;
            }

            // Fix format check - LiteLLM actually sends {"s":0,"d":"content"} not {"d":"content"}
            if (chunk.startsWith('data: {"s"')) {
                const jsonString = chunk.slice(6);
                try {
                    const parsedData = JSON.parse(jsonString);
                    const extractedChunk = parsedData.d; // Extract content from {"s":0,"d":"content"}

                    // Don't add undefined to buffer to prevent frontend corruption
                    if (extractedChunk === undefined || extractedChunk === null) {
                        return false;
                    }

                    // Add to our buffer
                    this.buffer += extractedChunk;
                    this.llmResponse += extractedChunk;

                    if (containsPotentialMarker(this.buffer, ARTIFACT_COMPLETE_MARKER)) {
                        // Check if we have the completion marker
                        if (this.buffer.includes(ARTIFACT_COMPLETE_MARKER)) {
                            logger.info("✅ ARTIFACT_COMPLETE_MARKER found! Artifact generation complete.");
                            
                            // Extract everything before the marker
                            const markerIndex = this.buffer.indexOf(ARTIFACT_COMPLETE_MARKER);
                            const cleanContent = this.buffer.substring(0, markerIndex);
                            
                            // Only send content if it's valid
                            if (cleanContent !== undefined && cleanContent !== null) {
                                const transformedContent = this.transform(cleanContent);
                                this.originalStream.write(transformedContent);
                            }
                            
                            this.isComplete = true;
                            logger.info("✅ Artifact completed");
                            return true;
                        } else {
                            // Potential marker found but not complete - wait for more chunks
                            return false;
                        }

                    } else {
                        // If no marker, just pass through
                        if (this.buffer !== undefined && this.buffer !== null && this.buffer !== "undefined") {
                            this.originalStream.write(this.transform(this.buffer)); 
                        }
                        this.buffer = "";
                    }

                } catch (error) {
                    logger.debug("Error parsing chunk:", chunk, "continuing...");
                }
        
            } else {
                // allow meta data and other non-content to pass through
                this.originalStream.write(chunk);
            }

            return false;
        }
        
        getAccumulatedResponse() {
            return this.llmResponse;
        }
        
        isArtifactComplete() {
            return this.isComplete;
        }
    }
    
    while (!isComplete && retryCount < MAX_RETRIES) {
        logger.info(`🚀 Artifact generation attempt ${retryCount + 1}/${MAX_RETRIES}`);
        
        // Create custom stream handler
        const streamHandler = new ArtifactStreamHandler(context.responseStream);
        
        // Clone InternalLLM with custom stream for marker detection
        const customStreamLLM = llm.clone();
        customStreamLLM.responseStream = {
            write: (chunk) => streamHandler.write(chunk),
            end: () => {},
            destroyed: false,
            writable: true,
            writableEnded: false
        };
        
        customStreamLLM.params = { ...llm.params };
        
        // Build the appropriate prompt based on retry count
        const messages = [{
            role: "system",
            content: additional_instructions
        }];
        if (retryCount > 0) {
            messages.push({
                role: "user",
                content: `${truncated_output_instructions}\n\nThis is whats been generated so far: \n\n${accumulatedResponse}`
            });
        }
        const prompt = new PromptAction(
            messages,
            "response",
            {skipRag: true, ragOnly: false, appendMessages: true, streamResults: true}
        );
        
        // Execute the prompt and wait for stream completion
        await invokeAction(prompt, customStreamLLM, context, dataSources);
        
        // Wait for stream to actually complete (invokeAction returns when call starts, not when stream ends)
        let waitCount = 0;
        while (!streamHandler.isArtifactComplete() && waitCount < 100) {
            await new Promise(resolve => setTimeout(resolve, 100)); // Wait 100ms
            waitCount++;
        }
        
        isComplete = streamHandler.isArtifactComplete();
        const currentResponse = streamHandler.getAccumulatedResponse();
        
        if (isComplete) {
            logger.info("✅ Artifact generation completed successfully");
        } else if (retryCount + 1 < MAX_RETRIES) {
            logger.warn(`⚠️ Artifact incomplete, will retry (attempt ${retryCount + 2}/${MAX_RETRIES})`);
        }
        
        // Update accumulated response for next iteration if needed
        accumulatedResponse += currentResponse;
        retryCount++;
    }
    
    if (isComplete) {
        sendStateEventToStream(context.responseStream, {artifactCompletion: true});
    } else if (retryCount >= MAX_RETRIES) {
        sendStateEventToStream(context.responseStream, {artifactCompletion: false});
    }
    
    return isComplete;
}


const States = {
    truncatedOutput: new AssistantState("truncatedOutput",
        "Handling artifact generation with automatic continuation if truncated",
        {
            execute: async (llm, context, dataSources) => {
                // Use our custom handler instead of the standard PromptAction
                const isComplete = await handleTruncatedMode(llm, context, dataSources);
                logger.info("Artifact complete:", isComplete);
            }
        }, false,
        {
            useFullHistory: true,
            failOnError: false,
            omitDocuments: false,
            stream: {target: "response", passThrough: true}
        }
    ),
    done: new DoneState(),
}

const current = States.truncatedOutput;
States.truncatedOutput.addTransition(States.done.name, "Artifact generation complete");


export const ArtifactModeAssistant = new StateBasedAssistant(
    "Artifacts Assistant",
    "Artifacts",
    "An assistant that helps create and manage artifacts with support for handling truncated outputs",
    (m) => {
        return true
    },
    (m) => {
        return true 
    },
    // This is the state machine that the assistant will use to process requests.
    States,
    // This is the current state that the assistant will start in.
    current
);