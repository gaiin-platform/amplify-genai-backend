import {
    AssistantState, chainActions,
    DoneState, invokeAction, 
    PromptAction, StateBasedAssistant
} from "./statemachine/states.js";


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
- The most important goal is that your response is a direct continuation of the artifact and not a new artifact.
- Dont repeat any content from the previous artifact, it must be a perfectly seamless continuation.
- example:
    - Artifact generated so far:
       This is a samp
    - Then your response will continue right where it left off:
       le artifact that was cut off.
       
- You must still operate under the same **Token Limit:** Behavioral Guideline as instructed.
`;

// mode long form 
//if we are working with multiple parts decide whetheryou absolutley only need to see which ones. you many not need all 
// mode fast edit We may need to also automatically line number artifacts when we detect them and stream them back so that the LLM doesn’t have to figure out the line numbers itself.

const determineMode = () => {

}


// prompt for mode and! 

const transitionToMode = () => {
    const mode = context.data['mode'];
    // console.log("Selected Entertainment: ", entertainmentSelected);
    switch (mode) {
        case 'fastEdit':
            States.initialState.addTransition(States.fastEdit.name, "The next random state is fastEdit, go here");
            break;
        case 'longForm ':
            States.initialState.addTransition(States.longForm.name, "The next random state is longForm, go here");
            break;
        case 'truncatedOputput':
            States.initialState.addTransition(States.truncatedOputput.name, "The next random state is truncatedOputput, go here");
            break;
        default:
            console.error("Error with entertainment states");
    }

}


const containsPotentialMarker = (buffer, marker) => {
    const markerStartChar = marker[0];
    //find the first occurence of the marker
    const markerStartIndex = buffer.indexOf(markerStartChar);
    if (markerStartIndex === -1) return false; // effective quick check
    const remainingBuffer = buffer.slice(markerStartIndex);

    return remainingBuffer === marker.slice(0, remainingBuffer.length);
};

const handleTruncatedMode = async (llm, context, dataSources) => {
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
            return "data: "+JSON.stringify({d: chunk}) + '\n\n'
        }
        
        write(chunk) {

            if (chunk.startsWith('data: {"d"')) {
                const jsonString = chunk.slice(6);
                try {
                    const extractedChunk = JSON.parse(jsonString).d;

                    // Add to our buffer
                    this.buffer += extractedChunk;
                    this.llmResponse += extractedChunk;
                    
                    if (containsPotentialMarker(this.buffer, ARTIFACT_COMPLETE_MARKER)) {
                        // Check if we have the completion marker
                        if (this.buffer.includes(ARTIFACT_COMPLETE_MARKER)) {
                            // Extract everything before the marker
                            const parts = this.buffer.split(ARTIFACT_COMPLETE_MARKER);
                            const cleanContent = parts[0];
                            
                            // Send the content without the marker
                            this.originalStream.write(this.transform(cleanContent));
                            this.isComplete = true;
                            console.log("Artifact completed");
                            return true;
                        }

                    } else {
                        // If no marker, just pass through
                        this.originalStream.write(this.transform(this.buffer)); 
                        this.buffer = "";
                    }

                } catch {
                    // continue 
                    console.log("Error parsing chunk: ", chunk, "\ncontinuing...");
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
        // Create custom stream handler
        const streamHandler = new ArtifactStreamHandler(context.responseStream);
        
        // Clone LLM with custom stream
        const customStreamLLM = llm.clone();
        customStreamLLM.responseStream = {
            write: (chunk) => streamHandler.write(chunk),
            end: () => {} // We'll handle the end separately
        };
        
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
        // Execute the prompt
        await invokeAction(prompt, customStreamLLM, context, dataSources);
        
        isComplete = streamHandler.isArtifactComplete();
        
        // Update accumulated response for next iteration if needed
        accumulatedResponse += streamHandler.getAccumulatedResponse();
        retryCount++;
    }
    
    if (isComplete) {
        llm.sendStateEventToStream({artifactCompletion: true});
    } else if (retryCount >= MAX_RETRIES) {
        llm.sendStateEventToStream({artifactCompletion: false});
    }
    
    return isComplete;
}


const States = {
    // outputToResponse

        initialState: new AssistantState("initialState",
            "Update user and set up for creating artifacts",
            chainActions([// possible mode detection
            ]), false,
        ),

        fastEdit: new AssistantState("fastEdit",
            "",
            chainActions([
            ]), false,
        ),

        truncatedOputput: new AssistantState("truncatedOputput",
            "Handling artifact generation with automatic continuation if truncated",
            {
                execute: async (llm, context, dataSources) => {
                    // Use our custom handler instead of the standard PromptAction
                    const isComplete = await handleTruncatedMode(llm, context, dataSources);
                    console.log("Artifact complete: ", isComplete);
                }
            }, false,
            {
                useFullHistory: true,
                failOnError: false,
                omitDocuments: false,
                stream: {target: "response", passThrough: true}
            }
        ),

        longForm: new AssistantState("longForm",
            "",
            chainActions([
            ]), false,
        ),
        done: new DoneState(),
}

// const current = States.initialState;
// States.initialState.addTransition(States.fastEdit.name, "");
// States.initialState.addTransition(States.longForm.name, "The next random state is longForm, go here");
// States.initialState.addTransition(States.truncatedOputput.name, "The next random state is truncatedOputput, go here");
const current = States.truncatedOputput;
// done will be added on the fly 
States.truncatedOputput.addTransition(States.done.name, "We are done");
// We add transitions to the state machine to define the state machine.


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