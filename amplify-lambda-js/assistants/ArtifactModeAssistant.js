import {
    AssistantState,
    DoneState, invokeAction,
    PromptAction, StateBasedAssistant
} from "./statemachine/states.js";
import {sendStateEventToStream} from "../common/streams.js";
import {getInternalLLM} from "../llm/InternalLLM.js";
import {getLogger} from "../common/logging.js";
import {isKilled} from "../requests/requestState.js";
import {ARTIFACT_TYPE_RULES} from "./artifactTypeRules.js";

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
       This means your response DOES NOT REQUIRE the identier at the end of your response WHEN YOU KNOW YOU ARE NOT DONE/IT IS NOT COMPLETE. It is only required when detected the artifact is whole and complete, there is no more content needed to be returned to the user, it is not cut off.
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
And REMEBER TO MARK AN ARTIFACT AS COMPLETE WITH THE IDENTIFIER ${ARTIFACT_COMPLETE_MARKER} WHEN YOU ARE DONE and not when it is truncated or incomplete.

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
- REMEMBER: If it looks complete, just respond with ${ARTIFACT_COMPLETE_MARKER}
`;

// Used when the client resumes an artifact it knows was cut off (for example by a time limit).
// Unlike the instructions above, the model is not allowed to decide the artifact is already complete.
const resume_instructions = `
The artifact below was cut off before it finished because the request ran out of time. It is definitely INCOMPLETE. Do not decide that it is complete.
Continue it from exactly where it stopped, even if it stopped in the middle of a word, a line of code, a string or a tag.
Guidelines:
- Start your response with the exact next character that should follow the text below. No preamble, no commentary, and do not repeat anything that is already written.
- Match the existing format, structure and escaping exactly. Keep continuing the same structure instead of starting a new one.
- Write the entire missing remainder, including any closing tags, brackets, quotes and sections that are still missing.
- Do NOT respond with only the identifier ${ARTIFACT_COMPLETE_MARKER}. Only after you have written the missing remainder, end your response with ${ARTIFACT_COMPLETE_MARKER}.
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
    
    logger.info("🚀 ArtifactMode using InternalLLM for high-speed generation");
    let retryCount = 0;
    let isComplete = false;
    // When the client resumes a cut-off artifact it sends the text it already has,
    // so generation continues from there instead of starting over.
    // Read from the request context: the state machine clones the LLM and the clone drops its params.
    const requestedContinueFrom = context.body?.options?.continueFrom;
    const continueFrom = typeof requestedContinueFrom === 'string' ? requestedContinueFrom : "";
    let accumulatedResponse = continueFrom;
    // Tells the client this request is an artifact stream, so a stream that ends
    // without an artifactCompletion event can be shown as cut off.
    sendStateEventToStream(context.responseStream, {artifactMode: true});
    let emptyRounds = 0;
    let stopReason = null;
    // A resumed request starts from text the client knows is cut off, so until a round writes
    // real text, the model must continue it and may not just declare it complete.
    let resumeNeedsText = !!continueFrom;
    let claimedDone = 0;

    // Custom stream handler to buffer output and check for completion marker
    class ArtifactStreamHandler {
        constructor(originalStream) {
            this.originalStream = originalStream;
            this.llmResponse = "";
            this.buffer = "";
            this.isComplete = false;
            this.lastChunkAt = Date.now();
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
            this.lastChunkAt = Date.now();
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
        // Check killswitch before each retry
        if (await isKilled(llm.params?.account?.user, context.responseStream, llm.params)) {
            logger.info("🛑 Killswitch activated, stopping artifact generation");
            sendStateEventToStream(context.responseStream, {artifactCompletion: false, reason: "cancelled"});
            return false;
        }
        
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
            // Only the instructions for writing the artifact itself. The deployment's "artifacts" feature prompt
            // tells the chat model to emit an autoArtifacts JSON block, which made this round wrap the page in JSON
            // (and made resuming a cut-off artifact impossible, since the model restarted the wrapper).
            // The per-type rules (CSV / code / visualization / document) live here, not in the browser request:
            // they contain literal HTML/SVG that the WAF in front of the web app rejects as XSS.
            content: `${ARTIFACT_TYPE_RULES}\n${additional_instructions}`
        }];
        if (retryCount > 0 || continueFrom) {
            // Calculate max chars based on model's context window
            // Formula: (contextWindow - outputLimit) * 4 chars/token, with 20% safety margin
            const model = llm.model || originalLLM.params.options.model;
            const contextWindow = model?.inputContextWindow || 128000;
            const outputLimit = model?.outputTokenLimit || 16000;
            const availableTokens = contextWindow - outputLimit;
            const safetyMargin = 0.8; // 20% buffer for system prompt, instructions, etc.
            const maxAccumulatedChars = Math.floor(availableTokens * 4 * safetyMargin);

            // Safeguard: Truncate accumulated response to prevent context overflow
            // Keep the END of the response (most recent content) since that's what needs continuation
            let truncatedAccumulated = accumulatedResponse;
            if (accumulatedResponse.length > maxAccumulatedChars) {
                truncatedAccumulated = accumulatedResponse.slice(-maxAccumulatedChars);
                logger.warn(`⚠️ Truncating accumulated response from ${accumulatedResponse.length} to ${maxAccumulatedChars} chars (model: ${contextWindow - outputLimit} available tokens) to prevent overflow`);
            }
            messages.push({
                role: "user",
                content: `${resumeNeedsText ? resume_instructions : truncated_output_instructions}\n\nThis is whats been generated so far: \n\n${truncatedAccumulated}`
            });
        }
        const prompt = new PromptAction(
            messages,
            "response",
            // Extended thinking adds minutes before the first artifact token and artifact
            // generation has to fit in the request time limit, so keep reasoning off.
            // retries: 0 because a retry would restart the stream and repeat text the user has already
            // received. A failed round keeps what it wrote and the next round continues from it.
            {skipRag: true, ragOnly: false, appendMessages: true, streamResults: true, retries: 0, options: {disableReasoning: true}}
        );

        // Execute the prompt and wait for stream completion
        await invokeAction(prompt, customStreamLLM, context, dataSources);

        // The call has returned, so the stream is finished. Only wait for any chunk still being
        // handled, instead of a fixed 10 seconds when the round ended without the end marker.
        let waitCount = 0;
        while (!streamHandler.isArtifactComplete() && waitCount < 100 && Date.now() - streamHandler.lastChunkAt < 300) {
            await new Promise(resolve => setTimeout(resolve, 100)); // Wait 100ms
            waitCount++;
        }

        isComplete = streamHandler.isArtifactComplete();
        const currentResponse = streamHandler.getAccumulatedResponse();

        // A bare end marker on a resumed request is not a real completion: the client knows the text is
        // cut off. Reject it (twice at most) instead of reporting a truncated artifact as complete.
        let rejectedClaim = false;
        if (resumeNeedsText) {
            if (currentResponse.replace(ARTIFACT_COMPLETE_MARKER, '').trim().length === 0) {
                rejectedClaim = true;
                isComplete = false;
                claimedDone++;
                logger.warn(`⚠️ Model answered a resumed artifact with only the end marker (${claimedDone}/2), not accepting it`);
            } else {
                resumeNeedsText = false;
            }
        }

        // FAILSAFE: In retry mode, check if LLM just responded with marker (meaning it detected completion)
        if (!isComplete && !rejectedClaim && (retryCount > 0 || continueFrom) && currentResponse.trim() === ARTIFACT_COMPLETE_MARKER) {
            logger.info("✅ LLM confirmed artifact was already complete in retry");
            isComplete = true;
        }

        if (isComplete) {
            logger.info("✅ Artifact generation completed successfully");
        } else if (retryCount + 1 < MAX_RETRIES) {
            logger.warn(`⚠️ Artifact incomplete, will retry (attempt ${retryCount + 2}/${MAX_RETRIES})`);
        }

        // Update accumulated response for next iteration if needed
        accumulatedResponse += rejectedClaim ? "" : currentResponse;
        retryCount++;

        if (isComplete) break;

        if (rejectedClaim && claimedDone >= 2) {
            stopReason = "model_claimed_complete";
            break;
        }

        // Two rounds in a row that wrote nothing means the model call is failing, not progressing.
        emptyRounds = currentResponse.length === 0 ? emptyRounds + 1 : 0;
        if (emptyRounds >= 2) {
            stopReason = "no_progress";
            logger.warn("⚠️ Two artifact rounds produced no output, stopping");
            break;
        }
    }

    if (isComplete) {
        sendStateEventToStream(context.responseStream, {artifactCompletion: true});
    } else {
        sendStateEventToStream(context.responseStream, {artifactCompletion: false, reason: stopReason || "round_limit"});
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