//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {
    AssistantState, chainActions,
    DoneState,
    HintState, invokeAction, llmAction, mapKeysAction, outputAction, outputContext, outputToResponse,
    PromptAction, PromptForDataAction, ragAction,
    reduceKeysAction,
    StateBasedAssistant, updateStatus
} from "./statemachine/states.js";

// This is a simple report writing assistant using the StateBasedAssistant framework.
// Each assistant is based on a state machine. Each state can have an entry action that
// is executed when the state is entered. When a state is entered, it is passed a context
// that has the current state. The context.data is information that the assistant states's
// have stored, such as the outline for the report to write. Each time a state is entered,
// the assistant will execute the entry action for that state. The entry action can create
// and store new data in the context.data, update the status (e.g., the messages shown to
// the user about what the assistant is doing), prompt the LLM for some output, send output
// to the assistant response that the user sees, or run an arbitrary function. After the
// entry action is executed, the assistant will build a special prompt to determine the
// next state to transition to and prompt the LLM to choose the next state. The prompt
// includes the context.data as information in that prompt. Once the LLM chooses a next
// state, the assistant will transition to that state and execute its entry action. This
// process continues until an endState is reached. Each AssistantState can be provided
// a configuration that includes pre/post information to include in the next state prompt
// to help the LLM choose the next state. These are passed in the config parameter as
// {extraInstructions:{postInstructions:"will come at the end fo the prompt", preInstructions:"..."}}.

// The state machine is defined by adding transitions to each state with the addTransition
// method. The first parameter is the name of the state to transition to and the second
// parameter is the description of the state to show in the prompt to the LLM to help it
// choose the next state. Once the state machine is defined, it is passed to the StateBasedAssistant
// constructor along with the current state to start in. The StateBasedAssistant will handle
// receive requests from the UI and pass them to the state machine to be processed. The
// assistant needs to be exposed in assistants.js

// This is a simple action that will prompt the LLM to write a section of the report
// and pass it the section name as the arg. It will store the result in the sectionOfReport
// key in the context.data.
const writeSection =
    // This is the prompt for the LLM.
    new PromptAction("Write a section about: {{arg.title}}\n" +
        "Please include the following points:\n" +
        "-----------------------------------\n" +
        "{{contextWith arg pattern='point[0-9]'}}" + // This is a special helper that will pull all
        // keys that match the pattern and format them into a string with "key:\"value\"", one per line.
        // The first argument is the object to pull the keys from. The pattern is the regex to match the keys.

        "{{#contextList arg pattern='point[0-9]*'}}" + // This does roughly the same thing as the prior line,
        // but it will format the keys and values into a list of objects that can be used to
        // populate a template. This is useful for more complex formatting of the context. This
        // uses the #each block to iterate over the contextItems and format them into a list of objects
        // with the current index and the key and value of each item.
        "    {{#each contextItems}}" +
        "        {{@index}}. {{key}}:{{value}}\n" +
        "    {{/each}}" +
        "{{/contextList}}",
        // This is the key that the result will be stored under in the context.data.
        "sectionOfReport");

// A mapKeysAction takes a prefix for a set of keys in the context and
// passes the value of each key it finds to the provided map function.
// The provided map function receives the value of the key in the arg
// key in the context.data. You can refer to the arg like {{arg}} in the
// templates for prompts and status messages.
const writeSections = mapKeysAction(
    // This indicates that the output of this action should be sent to the assistant response, which
    // is the assistants response in the message panel.
    outputToResponse(
        // This is a chain of actions that will be executed in order.
        chainActions([
            // This is a helper action that updates the status, which is the message boxes that are seen
            // at the top of the assistant response indicating what is happening.
            updateStatus("writeSection", {summary: "Writing Section: {{arg.title}}", inProgress: true}),
            // This is the actual map function that will be applied to the value of each key with the specified
            // prefix. It will be passed the value of the key in the arg key in context.data.
            writeSection,
            updateStatus("writeSection", {summary: "Done writing.", inProgress: false}),
        ]),
        // This is a prefix that will be added to the assistant output. It can be a template that
        // refers to items in context.data.
        "## {{arg.title}}"
    ),
    // This is the prefix for the keys that will be processed by map. All keys that start with this
    // prefix will be identified and their values turned into a list. Each item in the list will be
    // set as the arg key in context.data and the map function will be applied to it.
    "section",
    // You can optionally have all of the values of the keys with the specified prefix combined into a single
    // list and saved under new key in context.data. This is the name of that key.
    null,
    // All actions update the context.data. By default, mapKeysAction will return a map representing the
    // result of the map function (E.g., new keys it added to context.data) and then assign this map to the
    // key that was mapped. Each map operation will result in a new map that is bound to the key that was mapped
    // with whatever keys were added to context.data by the map function. If, however, you just want a single
    // key to become the result, you specify it like this.
    "sectionOfReport"
)

// This will apply a reduce operation to all keys with a given prefix in the context.data in
// order to produce a new single value. The new value will be stored as a key in the context.data.
const combineSections = reduceKeysAction(
    // This is the function that will be applied to the list of the values to convert it into
    // a new list.
    // An invokeAction takes a normal Javascript function and specifies keys "['arg']" from the context.data
    // that should be turned into a list of arguments and passed to the function. The result of the
    // function will be stored in the context.data under the specified outputKey, which is "report".
    invokeAction((sections) => sections.join("\n\n"), ["arg"], "report"),
    "section",
    // This specifies the new key that will be stored in the context.data with the result of the reduce
    // operation.
    "report",
    // By default, reduce will diff the original context.data and the resulting context.data from the
    // reduce operation and use the map that is produced by the diff as the result. This tells the reduce to
    // extract a specific key from the invokeAction's context.data and use that key's value as the
    // result of the reduce.
    "report");

// This is the set of states that will be in the state machine.
const States = {
    // We start in the outline state and produce an outline of the report that we are going to write.
    // The outline will be a list of sections that we will write.
    search: new AssistantState("search",
        "Posing questions to answer with the documents",
        // PromptForDataAction is a special action that allows us to reliably produce key/value pairs and extract
        // them. This action will automatically convert the specified key/value pairs into a dictionary and store
        // each key/value pair in the context.data.
        new PromptForDataAction(

            // This is the prompt that will be sent to the LLM
            "Available Documents:\n" +
            "-------------------------" +
            "{{yaml dataSources}}\n" +
            "-------------------------" +
            "\n" +
            "Look at any included documents in the conversation. Think of any questions that you would like" +
            " to search the documents for answers for to perform the task described in the conversation." +
            " What questions do you need answered to write an exceptional report on the topic using these sources?",

            // This is the set of key/value pairs that we want to extract. The key is the key that the
            // LLM should produce and the value is the description that will be provided to the LLM in the
            // prompt so that it knows what to do. If you want one of a small set of values, you can do
            // something like "key":"yes|no|maybe" and the LLM will usually only output one of the values.
            {
                "thought":"explain your reasoning...",
                "question1": "the first question",
                "question2": "the second question",
                "question3": "etc.."
            },

            // This is a function that will be called after the data is extracted and turned into a dictionary.
            // The function can decide if the LLM output is OK or if the LLM should be prompted again to produce
            // a different value. The function has a configurable "retries" parameter that defaults to 3 and
            // determines how many times the LLM will be prompted to produce a new value.
            (m) => {
                return m.question1;
            }
        ),
        false,
        {omitDocuments:true}
    ),
    rag: new AssistantState("rag",
        "Using RAG to find relevant information",
        mapKeysAction(
            ragAction({query:"{{arg}}"}),
            "question"
        ),
        false,
        {omitDocuments:true}
    ),
    outline: new AssistantState("outline",
        "Creating an outline",
        // PromptForDataAction is a special action that allows us to reliably produce key/value pairs and extract
        // them. This action will automatically convert the specified key/value pairs into a dictionary and store
        // each key/value pair in the context.data.
        new PromptForDataAction(
            // This is the prompt that will be sent to the LLM
            "Possibly Relevant Information:\n" +
            "----------------------------\n" +
            "{{yaml possiblyRelevantInformation}}\n" +
            "----------------------------\n" +
            "Write an outline for the report and output each section on a new " +
            "line with the section prefix as shown. Refer to the possiblyRelevantInformation" +
            "to help determine appropriate sections.",
            // This is the set of key/value pairs that we want to extract. The key is the key that the
            // LLM should produce and the value is the description that will be provided to the LLM in the
            // prompt so that it knows what to do. If you want one of a small set of values, you can do
            // something like "key":"yes|no|maybe" and the LLM will usually only output one of the values.
            {
                "section1": "the first section",
                "section2": "the second section",
                "section3": "the third section",
                "section4": "the fourth section",
                "section5": "the fifth section"
            },
            // This is a function that will be called after the data is extracted and turned into a dictionary.
            // The function can decide if the LLM output is OK or if the LLM should be prompted again to produce
            // a different value. The function has a configurable "retries" parameter that defaults to 3 and
            // determines how many times the LLM will be prompted to produce a new value.
            (m) => {
                return m.section1 && m.section2;
            }
        ),
    ),
    outlineSection: new AssistantState("outlineSection",
        "Outline what should go in the section",
        // PromptForDataAction is a special action that allows us to reliably produce key/value pairs and extract
        // them. This action will automatically convert the specified key/value pairs into a dictionary and store
        // each key/value pair in the context.data.
        chainActions([
                mapKeysAction(
                    new PromptForDataAction(
                        // This is the prompt that will be sent to the LLM.
                        // The templates use Handlebars https://handlebarsjs.com/guide/
                        // The context.data is passed to the template to render it.
                        // There are a few special functions that are registered in Handlebars, as
                        // shown below "matchKeys" takes a regular expression and fetches all matching
                        // keys from the context.data and then formats them into a string with "key:\"value\"",
                        // one per line. It automatically does JSON.stringify(value), so it will still generate
                        // a string if the value is an object or array.
                        "" +
                        "The sections before this section are:\n" +
                        "-------------------------------------\n" +
                        "{{context 'section[0-9]*'}}\n" + // This is a special helper that will pull all
                        // keys in context.data that match the pattern and format them into a string with
                        // "key:\"value\"", one per line. It automatically calls JSON.stringify on the value.
                        "-------------------------------------\n" +
                        "Take into account the sections that came before to avoid duplication and create a list of bullet " +
                        "points that should go in the section about: \n" +
                        "{{arg}}. ",
                        // This is the set of key/value pairs that we want to extract. The key is the key that the
                        // LLM should produce and the value is the description that will be provided to the LLM in the
                        // prompt so that it knows what to do. If you want one of a small set of values, you can do
                        // something like "key":"yes|no|maybe" and the LLM will usually only output one of the values.
                        {
                            "title": "the title of the section",
                            "point1": "the first point to cover",
                            "point2": "the second point to cover",
                            "point3": "the third point to cover",
                            "point4": "the fourth point to cover",
                            "point5": "the fifth point to cover"
                        },
                        // This is a function that will be called after the data is extracted and turned into a dictionary.
                        // The function can decide if the LLM output is OK or if the LLM should be prompted again to produce
                        // a different value. The function has a configurable "retries" parameter that defaults to 3 and
                        // determines how many times the LLM will be prompted to produce a new value.
                        (m) => {
                            return m.title && m.point1 && m.point2;
                        }
                    ),
                    "section"),
                outputContext(
                    "## Outline of Report\n\n" +
                    "{{contextOutline}}")
            ]
        ),
    ),
    // Once we produce an outline of the report to write, we need to write the sections of the report
    // one by one. This allows us to write a much longer report than would normally be possible in a single
    // prompt. We will write each section and store the result in the context.data.
    writeSections: new AssistantState("writeSections",
        "Writing the sections",
        // This is the action that maps each section of the outline to a prompt for the LLM to write
        // the section.
        writeSections
    ),
    // This is the end state.
    done: new DoneState(),
};

// We start in the outline state.
const current = States.search;

// We add transitions to the state machine to define the state machine.
States.search.addTransition(States.rag.name, "Outline each section of the report");
States.rag.addTransition(States.outline.name, "Use RAG to find relevant document chunks");
States.outline.addTransition(States.outlineSection.name, "Outline each section of the report");
States.outlineSection.addTransition(States.writeSections.name, "Write the report");
States.writeSections.addTransition(States.done.name, "Done");

// We create the assistant with the state machine and the current state.
export const reportWriterAssistant = new StateBasedAssistant(
    "Report Writer Assistant",
    "Report Writer",
    "This assistant creates an outline and then drafts a report that is longer than can be " +
    "written in a single prompt. It is designed to help you write a report.",
    // Each assistant has a function to decide if it can support the dataSources that were attached to the
    // prompt that the user sent. An assistant should not respond if a message includes a dataSource type that
    // the assistant does not support. This function should return true if the assistant can support the
    // dataSources and false otherwise.
    (m) => {
        return true
    },
    // This function determines if the assistant will work with the given model, such as GPT-3.5, etc.
    (m) => {
        return true
    },
    // This is the state machine that the assistant will use to process requests.
    States,
    // This is the current state that the assistant will start in.
    current
);

