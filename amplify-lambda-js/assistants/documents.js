import {
    AssistantState, chainActions,
    DoneState,
    HintState, invokeAction, llmAction, mapKeysAction, outputAction, outputContext, outputToResponse, outputToStatus,
    PromptAction, PromptForDataAction, ragAction,
    reduceKeysAction,
    StateBasedAssistant, updateStatus
} from "./statemachine/states.js";


// This is the set of states that will be in the state machine.
const States = {
    assess: new AssistantState("assess",
        "I am planning how to best accomplish this task.",
        new PromptForDataAction(
            "Always do a search!!!",
            // "Look at the task that the user has ask to be performed. We need to determine" +
            // " if the documents in the conversation are needed to perform the task. Also, if we need the " +
            // "documents, we need to determine if we need to read the entire document or perform targeted searches. " +
            // "If the document is less than 2,000 tokens, we should just read the whole thing always." +
            // "If the task is very specific and easy to search for the needed information, then search. " +
            // "If you are unsure, just search. " +
            "",
            {
                "thought": "explain your reasoning",
                "useDocuments": "yes|no",
                "readingStrategy": "search|entireDocument"
            },
            (data) => {
                return data.useDocuments && data.readingStrategy;
            })
    ),
    answerWithSearch: new AssistantState("answerWithSearch",
        "I am going to search for relevant information and respond.",
        chainActions([
            updateStatus("answer", {summary: "I am searching for relevant information.", inProgress: true}),
            outputToResponse(
                new PromptAction(
                    null, // This will cause no new messages to be added and the assistant to respond to the conversation as a whole
                    "response", 3, true, {skipRag: false, ragOnly: true})
            ),
            updateStatus("answer", {summary: "I am searching for relevant information.", inProgress: false})
        ])
    ),
    answerWithReadingEntireDocument: new AssistantState("answerWithReadingEntireDocument",
        "I am going to read the provided documents and respond.",
        chainActions([
            updateStatus("answer", {summary: "I am reading the document(s).", inProgress: true}),
            outputToResponse(
                new PromptAction(
                    null, // This will cause no new messages to be added and the assistant to respond to the conversation as a whole
                    "response", 3, true, {skipRag: true, ragOnly: false})
            ),
            updateStatus("answer", {summary: "I am reading the document(s).", inProgress: false}),
        ])
    ),
    // This is the end state.
    done: new DoneState(),
};

// We start in the outline state.
const current = States.assess;

// We add transitions to the state machine to define the state machine.
States.assess.addTransition(States.answerWithSearch.name, "Answer by searching for specific information");
States.assess.addTransition(States.answerWithReadingEntireDocument.name, "Answer by reading the entire document(s)");
States.answerWithSearch.addTransition(States.done.name, "Done");
States.answerWithReadingEntireDocument.addTransition(States.done.name, "Done");

// We create the assistant with the state machine and the current state.
export const documentAssistant = new StateBasedAssistant(
    "Document Assistant",
    "Document Assistant",
    "This assistant helps determine the best way to accomplish a task that involves documents.",
    (m) => {
        return true
    },
    (m) => {
        return true
    },
    States,
    current
);

