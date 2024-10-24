//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {
    AssistantState, chainActions,
    DoneState,
    HintState, invokeAction, llmAction, mapKeysAction, outputAction, outputContext, outputToResponse, outputToStatus,
    PromptAction, PromptForDataAction, ragAction,
    reduceKeysAction,
    StateBasedAssistant, updateStatus, USER_INPUT_STATE, UserInputState
} from "./statemachine/states.js";


// This is the set of states that will be in the state machine.
const States = {
    // A HintState just adds additional instructions for the LLM when choosing the next
    // state to execute. There is a default prompt that is used to select the next state and
    // this just appends to that prompt.
    init: new AssistantState("init",
        "Working on your request...",
        updateStatus("init", {summary: "Working on your request...", inProgress: true})
    ),
    assess: new HintState("assess",
        "Analyzing your task...",
        "The first time a user asks you to perform a task with a document, " +
        "you should always start with a search. If the user didn't like the results of a " +
        "search and wants a more thorough read of the document, you can read the entireDocument." +
        "",
        false,
        {useFullHistory:false}
    ),
    queryCreation: new AssistantState("queryCreation",
        "Determining additional information that is needed...",
        new PromptForDataAction(
            "Imagine we have a FAQ at our disposal to accomplish this task. What" +
            " question should we look for in the FAQ based on the current information that we have" +
            " and what we still need in order to perform the task>",
            // These keys will be added to context.data and available for the next
            // state in the state machine.
            {
                "thought": "explain your reasoning",
                "question": "<fill in>",
            },
            // This function checks that we got all of the keys we need to proceed.
            // Otherwise, we will automatically prompt again up to the maximum number of
            // tries, which can be configured as a config param.
            (data) => {
                return data.question;
            })
    ),
    search: new AssistantState("search",
        "I am collecting information...",
        chainActions([
            updateStatus("search",
                {summary: "Searching: {{statusSummary question}}",
                    message: "Searching: {{question}}",
                    inProgress: true}),
            ragAction(
                {
                    "query": "{{question}}",
                }
            ),
            updateStatus("search", {summary: "Searching: {{statusSummary question}}", inProgress: false}),
        ]),
    ),
    enoughInformation: new HintState("enoughInformation",
        "Checking the information I have so far...",
        "Do we have enough information to complete the task yet? If not, you" +
        " should go back to the search state. If so, you should go to the answerWithSearch state."
    ),
    answerWithSearch: new AssistantState("answerWithSearch",
        "I am going to search for relevant information and respond.",
        chainActions([
            // This updates the message that the user sees in Amplify to show what the LLM is doing.
            updateStatus("answer", {summary: "I am searching for relevant information.", inProgress: true}),
            // This is a special wrapper action that will stream the results of any LLM action to the response. Otherwise,
            // the LLM will not show the user the intermediate outputs.
            outputToResponse(
                new PromptAction(
                    [                        {
                        role: "system",
                        content: "In your answer, quote the relevant sentences one at a time and explain each, " +
                            "unless told otherwise. If there is a page number, paragraph number, etc., list it " +
                            " like [page 2] after the sentence."
                    }], // This will cause no new messages to be added and the assistant to respond to the conversation as a whole
                    "response", 3, true,
                    {skipRag: false, ragOnly: true, appendMessages: true})
            ),
            updateStatus("answer", {summary: "I am searching for relevant information.", inProgress: false})
        ])
    ),
    chooseDocs: new AssistantState("chooseDocs",
        "Looking at the documents...",
        new PromptForDataAction(
            "Documents:\n{{yaml conversationDataSources}}\n" +
            "Which document from the list above needs be read to perform the task?" +
            "",
            // These keys will be added to context.data and available for the next
            // state in the state machine.
            {
                "thought": "explain your reasoning",
                "documentName": "<fill in>",
                "documentType": "<fill in>"
            },
            // This function checks that we got all of the keys we need to proceed.
            // Otherwise, we will automatically prompt again up to the maximum number of
            // tries, which can be configured as a config param.
            (data) => {
                return data.documentName && data.documentType;
            })
    ),
    answerWithReadingEntireDocument: new AssistantState("answerWithReadingEntireDocument",
        "I am going to read the provided documents and respond.",
        chainActions([
            updateStatus("answer", {summary: "I am reading the document(s).", inProgress: true}),
            (llm, context, dataSources) => {
                // The chooseDocs state should have populated the context.data with a documentName key that
                // holds the name of the document the LLM wants to use to answer the question. We need to
                // find the document in the conversationDataSources and set it as the active data source.
                // The actvieDataSources are the ones that the next PromptAction will use if not dataSources
                // are specified by the user.

                // 'dataSources' are only the ones directly attached by the user to the current message.
                // 'conversationDataSources' are all the data sources that have been attached to the conversation
                // anywhere in the conversation, so we search them for the document name that the LLM decided
                // was the one to read.
                const document = context.conversationDataSources.find((d) => d.name === context.data.documentName);
                if (document) {
                    context.activeDataSources = [document];
                }
            },
            // This is an alternate way to do the same thing as the previous action.
            // invokeFn((context, conversationDataSources, documentName) => {
            //     const document = conversationDataSources.find((d) => d.name === documentName);
            //     if (document) {
            //         context.activeDataSources = [document];
            //     }
            //     return "";
            // }, ["context", "conversationDataSources", "documentName"], "documentData"),
            outputToResponse(
                new PromptAction(
                    [
                        {
                            role: "system",
                            content: "In your answer, quote the relevant sentences one at a time and explain each, " +
                                "unless told otherwise. If there is a page number, paragraph number, etc., list it " +
                                " like [page 2] after the sentence."
                        }
                    ], // this will be added to the user's original message history and then conversation
                    // will be sent to the LLM as a prompt
                    "response",
                    // This disables RAG so that we just use the selected documents to answer the question
                    // by directly inserting them into the prompt.
                    {skipRag: true, ragOnly: false, appendMessages: true})
            ),
            updateStatus("answer", {summary: "I am reading the document(s).", inProgress: false}),
        ])
    ),
    clarifyTask: new UserInputState("clarifyTask",
     "Clarifying your request...",
      "Ask follow up questions to clarify the request and make sure that you can perform it correctly.",
        "If we have enough information, we can move to assessing the task."
    ),

    // This is the end state.
    done: new DoneState(),
};

// We start in the outline state.
const current = States.init;

// We add transitions to the state machine to define the state machine.
States.init.addTransition(States.assess.name, "Start");
States.init.addTransition(USER_INPUT_STATE, "If we need some more inf");
States.init.addTransition(States.assess.name, "If we have enough information to perform the task, go here");
States.assess.addTransition(States.queryCreation.name, "Answer by searching for specific information");
States.queryCreation.addTransition(States.search.name, "Search for the information");
States.search.addTransition(States.queryCreation.name, "Find more information");
States.search.addTransition(States.answerWithSearch.name, "Answer the question");
States.assess.addTransition(States.chooseDocs.name, "Answer by reading the entire document(s)");
States.chooseDocs.addTransition(States.answerWithReadingEntireDocument.name, "Answer by reading the entire document(s)");
States.answerWithSearch.addTransition(States.done.name, "Done");
States.answerWithReadingEntireDocument.addTransition(States.done.name, "Done");

// We create the assistant with the state machine and the current state.
export const documentAssistant = new StateBasedAssistant(
    "Document Assistant",
    "Document",
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

