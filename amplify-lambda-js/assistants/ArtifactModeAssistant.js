import {
    AssistantState, chainActions,
    DoneState, outputToStatus, invokeAction, 
    PromptAction, StateBasedAssistant, updateStatus, outputToResponse
} from "./statemachine/states.js";


const additional_instructions = `
1. Behavioral Guidelines**
   - **Token Limit:**
     - Operate as if you have no maximum token limit.
     - Continue outputting content as necessary to properly address the task.
     - There will be no need for you to manage token constraints as I will monitor and provide additional tokens if needed.
   
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

Adhere to these guidelines to ensure the artifact is produced accurately and efficiently without unnecessary token wastage or superfluous content.

Implement these instructions rigorously to guarantee a coherent and systematic approach in creating the required artifacts while adhering to the set behavioral constraints.

`

// mode long firm 
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
            logger.debug("Error with entertainment states");
    }

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
            "",
            chainActions([
                // This is a special wrapper action that will stream the results of any LLM action to the response. Otherwise,
                // the LLM will not show the user the intermediate outputs.
                outputToResponse(
                    new PromptAction(
                        [                        {
                            role: "system",
                            content: additional_instructions
                        }], // This will cause no new messages to be added and the assistant to respond to the conversation as a whole
                        "response", 2, true,
                        {skipRag: true, ragOnly: false, appendMessages: true, streamResults: true,})
                ),
            ]), false,
        ),

        longForm: new AssistantState("longForm",
            "",
            chainActions([
            ]), false,
        ),
        done: new DoneState(),
}
// parts instructions 1, 2, 3, each part has a prompt to complete it all 
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
    "description", 
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