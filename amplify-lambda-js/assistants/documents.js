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
            "The first time a user asks you to perform a task with a document, " +
            "you should always start with a search. If the user didn't like the results of a " +
            "search and wants a more thorough read of the document, you can read the entireDocument." +
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
    chooseDocs: new AssistantState("chooseDocs",
        "Looking at the documents...",
        new PromptForDataAction(
            "Documents:\n{{yaml dataSources}}\n" +
            "Which document needs to be read to perform the task?" +
            "",
            {
                "thought": "explain your reasoning",
                "documentName": "<fill in>",
                "documentType": "<fill in>"
            },
            (data) => {
                return data.documentName && data.documentType;
            })
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
States.assess.addTransition(States.chooseDocs.name, "Answer by reading the entire document(s)");
States.chooseDocs.addTransition(States.answerWithReadingEntireDocument.name, "Answer by reading the entire document(s)");
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

