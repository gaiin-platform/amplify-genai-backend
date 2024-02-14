import {getDefaultLLM} from "../../common/llm.js";
import {ModelID, Models} from "../../models/models.js";
import {ConsoleWritableStream} from "../../local/consoleWriteableStream.js";
import {newStatus} from "../../common/status.js";
import {sendDeltaToStream} from "../../common/streams.js";

const formatStateNamesAsEnum = (transitions) => {
    return transitions.map(t => t.to).join("|");
}

const formatRagInformationSource = (source) => {
    /**
     * {
     *                                 name:
     *                                 key,
     *                                 type:
     *                                 locations,
     *                                 indexes,
     *                                 charIndex,
     *                                 user,
     *                                 content
     *                             }
     */
    return `From ${source.name} at ${JSON.stringify(source.locations)}: ${source.content.replaceAll.replace(/(\r\n|\n|\r|\u2028|\u2029)/g, '\\n')}`;
}

const formatInformationSources = (sources) => {
    return (sources.length === 0 ? "NONE" :
        sources.map(source => formatInformationSource(source)).join("\n"));
}

const formatContextInformationItem = (source) => {
    return `${source[0]}: ${source ? source[1].replaceAll(/(\r\n|\n|\r|\u2028|\u2029)/g, '\\n') :  ""}`;
}

const formatContextInformation = (sources) => {
    return (sources.length === 0 ? "NONE" :
        sources.map(source => formatContextInformationItem(source)).join("\n"));
}

const formatDataSource = (dataSource) => {
    return `${dataSource.name}: ${dataSource.type}`;
};

const formatDataSources = (dataSources) => {
    return (dataSources.length === 0 ? "NONE" :
        dataSources.map(dataSource => formatDataSource(dataSource)).join("\n"));
}

const formatTransition = (transition) => {
    return `${transition.to}: ${transition.description}`;
};

const formatTransitions = (transitions) => {
    return transitions.map(transition => formatTransition(transition)).join("\n");
};

const buildSystemPrompt = (state) => {
    const systemPrompt = `
Analyze the task or question and output provided to you and figure out what state to transition to.

You output a next state in the format:
\`\`\`next
thought: <INSERT THOUGHT>
state: ${formatStateNamesAsEnum(state.transitions)}
\`\`\`

You MUST provide a next state:

You ALWAYS output a \`\`\`next code block.
`;

    return systemPrompt;
}



const buildStatePrompt = (context, state, dataSources) => {
    const prompt = `
${state.extraInstructions.postInstructions || ""}
    
Documents:
${formatDataSources(dataSources)}

Your next state options are:
${formatTransitions(state.transitions)}

Your current information is:
${formatContextInformation(Object.entries(context.data))}

Task:
${context.task}

${state.extraInstructions.preInstructions || ""}

\`\`\`next
`;

    return prompt;
}

export const llmAction = (fn) => {
    return {
        execute: (llm, context, dataSources) => {
            const result = fn(llm, context, dataSources);
            if(result) {
                context.data = {...context.data, ...result};
            }
        }
    };
}

export const outputAction = (template, src="assistant") => {
    return {
        execute: (llm, context, dataSources) => {
            const msg = fillInTemplate(template, context.data);
            sendDeltaToStream(llm.responseStream, src, msg);
        }
    };
}


export const invokeAction = (fn, keys, outputKey) => {

    // Make sure that the keys are defined and an array
    if(!keys || !Array.isArray(keys)) {
        throw new Error("keys must be defined and an array");
    }
    // Check that the outputKey is a string if it is defined
    if(outputKey && typeof outputKey !== "string") {
        throw new Error("outputKey must be defined and a string");
    }

    return {
        execute: async (llm, context, dataSources) => {
            const args = keys.map(k => context.data[k]);
            const result = await fn(...args);
            if(result && !outputKey) {
                context.data = {...context.data, ...result};
            }
            else if(result && outputKey) {
                context.data[outputKey] = result;
            }
        }
    };
}

export const mapKeysAction = (action, keyPrefix, outputKey=null, extractKey=null) => {

    // Make sure that the keyPrefix is defined and a string
    if(!keyPrefix || typeof keyPrefix !== "string") {
        throw new Error("keyPrefix must be defined and a string");
    }
    // Do the same for outputKey
    if(outputKey && typeof outputKey !== "string") {
        throw new Error("outputKey must be defined and a string");
    }

    return {
        execute: async (llm, context, dataSources) => {
            const allKeys = Object.keys(context.data);
            const keys = allKeys.filter(k => k.startsWith(keyPrefix));
            const args = keys.map(k => context.data[k]);
            const resultList = [];

            for(let i=0; i<args.length; i++) {
                const arg = args[i];
                let newContext = {...context, data:{...context.data, arg, i}};
                await action.execute(llm, newContext, dataSources);

                if(extractKey) {
                    newContext.data = newContext.data[extractKey];
                }

                if(!outputKey) {
                    context.data = {...context.data, [keys[i]]:newContext.data};
                }

                resultList.push(newContext.data);
            }

            if(outputKey) {
                context.data[outputKey] = resultList;
            }
        }
    };
}

export const reduceKeysAction = (action, keyPrefix, outputKey, extractKey=null) => {

    // Make sure that the keyPrefix is defined and a string
    if(!keyPrefix || typeof keyPrefix !== "string") {
        throw new Error("keyPrefix must be defined and a string");
    }
    // Do the same for outputKey
    if(!outputKey || typeof outputKey !== "string") {
        throw new Error("outputKey must be defined and a string");
    }

    return {
        execute: async (llm, context, dataSources) => {

            const allKeys = Object.keys(context.data);
            const keys = allKeys.filter(k => k.startsWith(keyPrefix));
            const args = keys.map(k => context.data[k]);

            const newContext = {...context, data:{...context.data, arg:args}};
            await action.execute(llm, newContext, dataSources);

            if(extractKey){
                newContext.data = newContext.data[extractKey];
            }

            outputKey = outputKey || keyPrefix;

            context.data[outputKey] = newContext.data;
        }
    };
}

export const chainActions = (actions) => {
    return {
        execute: async (llm, context, dataSources) => {
            for(const action of actions) {
                await action.execute(llm, context, dataSources);
            }
        }
    };
}

export const parallelActions = (actions) => {
    return {
        execute: async (llm, context, dataSources) => {
            const results = [];
            for(const action of actions) {
                results.push(action.execute(llm, context, dataSources));
            }
            await Promise.all(results);
        }
    };
}

function fillInTemplate(template, context) {
    for (const entry of Object.entries(context)) {
        template = template.replaceAll(`{{${entry[0]}}}`, entry[1]);
    }
    return template;
}

export class PromptForDataAction {

    constructor(prompt, stateKeys, stateChecker, retries=3) {
        this.prompt = prompt;
        this.stateKeys = stateKeys;
        this.stateChecker = stateChecker || ((result) => Object.keys(stateKeys).every(k => result[k]))
        this.retries = retries;
        this.streamResults = true;
        this.includeThoughts = false;
    }

    async execute(llm, context, dataSources) {

        let promptText = fillInTemplate(this.prompt, context.data);

        const result = await llm.promptForData(
            {messages:[...context.history, {role:"user", content:promptText}]},
            dataSources,
            this.prompt,
            this.stateKeys,
            (this.streamResults) ? llm.responseStream : null,
            this.stateChecker,
            this.retries,
            this.includeThoughts
        );

        if(result) {
            context.data = {...context.data, ...result};
        }
    }
}


export class PromptAction {

    constructor(prompt, outputKey="response", retries=3, streamResults=true) {
        this.prompt = prompt;
        this.outputKey = outputKey || "response";
        this.streamResults = streamResults;
        this.retries = retries;
    }

    async execute(llm, context, dataSources) {

        let promptText = fillInTemplate(this.prompt, context.data);

        const result = await llm.promptForString(
            {messages:[...context.history, {role:"user", content:promptText}]},
            dataSources,
            this.prompt,
            (this.streamResults) ? llm.responseStream : null,
            this.retries
        );

        if(result) {
            context.data = {...context.data, [this.outputKey]: result};
        }
    }
}



export class AssistantState {

    constructor(name, description, entryAction=null, endState=false,
                extraInstructions={preInstructions:"", postInstructions:""}) {
        this.name = name;
        this.description = description;
        this.entryAction = entryAction;
        this.extraInstructions = extraInstructions || {};
        this.transitions = [];
        this.endState = endState;
    }

    addTransition(toStateName, description) {
        this.transitions.push({to:toStateName, description:description});
    }

    buildPrompt(context, dataSources) {
        return [
            {role:"system", content:buildSystemPrompt(this)},
            {role:"user", content:buildStatePrompt(context, this, dataSources)},
        ]
    }

    async enter(llm, context, dataSources) {
        if (this.entryAction) {
            try {
                await this.entryAction.execute(llm, context, dataSources);
            } catch (e) {
                console.error(e);
            }
        }

        const messages = this.buildPrompt(context, dataSources);

        const chatRequest = {messages};
        const prefixes = ["thought","state"];
        const checkResult = (result) => {
            // Verify we got a valid next state and that it is in the list of transitions
           return result.state && this.transitions.map(t => t.to).includes(result.state);
        }
        const maxAttempts = 3;

        if(this.transitions.length === 0) {
            return this.name;
        }
        else if(this.transitions.length === 1) {
            return this.transitions[0].to;
        }

        const result = await llm.promptForPrefixData(
            chatRequest,
            prefixes,
            dataSources,
            null,
            checkResult,
            maxAttempts);

        return result.state;
    }
}

export class HintState extends AssistantState {
    constructor(name, description, hint, endState = false) {
        super(name, description, null, endState, {postInstructions: hint});
    }
}


export class DoneState extends AssistantState {
    constructor() {
        super("done", "Done", null, true);
    }
}

export class AssistantStateMachine {

    constructor(name, description, statesByName, currentState, config={}) {
        this.name = name;
        this.description = description;
        this.statesByName = statesByName;
        this.currentState = currentState;
        this.maxTransitions = config.maxTransitions || 100;
    }

    async on(llm, context, dataSources) {

        if(!context.data){
            context.data = {};
        }

        let transitionsLeft = this.maxTransitions;

        const status = newStatus(
            {
                inProgress: true,
                message: "",
                icon: "bolt",
            });

        const updateStatusForState = (state) => {
            status.message = state.description;
            llm.sendStatus(status);
            llm.forceFlush();
        }

        while(!this.currentState.endState && transitionsLeft > 0) {

            updateStatusForState(this.currentState);

            transitionsLeft -= 1;
            const nextName = await this.currentState.enter(llm, context, dataSources);
            this.currentState = this.statesByName[nextName];

            if(this.currentState === undefined) {
                break;
            }
        }
    }

}

export class StateBasedAssistant {

    constructor(name, displayName, description, handlesDataSources, handlesModel, states, initialState) {
        this.name = name;
        this.displayName = displayName;
        this.handlesDataSources = handlesDataSources;
        this.handlesModel = handlesModel;
        this.description = description;
        this.states = states;
        this.initialState = initialState;
    }

    createAssistantStateMachine() {
        return new AssistantStateMachine(
            this.name,
            this.description,
            this.states,
            this.initialState);
    }

    async handler(llm, params, body, dataSources, responseStream) {
        const context = {
            data:{},
            history:body.messages,
        };

        const stateMachine = this.createAssistantStateMachine();

        llm.enablePassThrough();

        await stateMachine.on(llm, context, dataSources);

        responseStream.end();
    }
}

const States = {
    initial: new HintState("initial",
        "Initial State",
        "Should we try to read the entire document or is the question specific enough that we can " +
        "do a targeted search? " +
        "If the question or task is general, such as 'what are all the policies', you will need to read the document. " +
        "If the question or task is specific, such as 'what are the AI policies', you can try a targeted search. " +
        "If you aren't sure, just read the document.",
        ),
    targetedSearch: new AssistantState("targetedSearch",
        "Targeted Search",
        new PromptAction("Create a 2-3 detailed questions to look for answers for in the document to help accomplish the task.",
            {
                "question1":"a specific question",
                "question2":"another specific question",
                "question3":"another specific question"
            },
            (result) => {
                return result.question1;
            }
        )),
    read: new AssistantState("read",
        "Targeted Search",
        new PromptAction("What page in the document should we read next? If you aren't sure, just output '1'",
            {
                "page":"a number specifying the page"
            }
        )),
    plan: new AssistantState("plan",
        "PLan State",
        new PromptAction("Create a 5-10 step plan to accomplish the task requested by the user",
            {"plan":"a step by step plan"}
        )),
    gather: new AssistantState("gather",
        "Information Gathering",
        new PromptAction("Create a 5-10 step plan to accomplish the task requested by the user",
            {"plan":"a step by step plan"}
        )),
    done: new DoneState(),
};



const current = States.initial;

States.initial.addTransition(States.targetedSearch.name, "Targeted Search");
States.initial.addTransition(States.read.name, "Read Document");
States.targetedSearch.addTransition(States.done.name, "Done");
States.read.addTransition(States.done.name, "Done");

export const documentSearchAssistant = new StateBasedAssistant(
    "Document Search Assistant",
    "Document Search Assistant",
    "Document Search Assistant",
    (m) => {return true},
    (m) => {return true},
    States,
    current
);


//
// const context = {
//     data:[],
//     history:[
//         {role:"user", content:"What should a student do if they miss the initial registration deadline?"}
//     ],
// };
//
// const llm = await getDefaultLLM(Models[ModelID.GPT_3_5_AZ]);
//
// const stream = new ConsoleWritableStream(true);
// llm.responseStream = stream;
// llm.enablePassThrough();
//
// const stateMachine = new AssistantStateMachine(
//
//     "Document Search Assistant",
//     "Document Search Assistant",
//     States,
//     current);
//
// await stateMachine.on(llm, context, []);

// Example Usage:
//
// export const States = {
//     initial: new AssistantState("initial", "Initial State", "Initial State", null, null),
//     search: new AssistantState("search", "Search State", "Search State", null, null),
//     readEntireDocument: new AssistantState("readEntireDocument", "Read Entire Document State", "Read Entire Document State", null, null),
//     analyze: new AssistantState("analyze", "Analyze State", "Analyze State", null, null),
//     plan: new AssistantState("plan", "Plan State", "Plan State", null, null),
//     execute: new AssistantState("execute", "Execute State", "Execute State", null, null),
//     complete: new AssistantState("complete", "Complete State", "Complete State", null, null),
//     error: new AssistantState("error", "Error State", "Error State", null, null),
//     end: new AssistantState("end", "End State", "End State", null, null),
// };
//
// States.initial.addTransition(States.search.name, "Search for information");
// States.initial.addTransition(States.readEntireDocument.name, "Read entire document");
// States.initial.addTransition(States.analyze.name, "Analyze the task or question");
// States.initial.addTransition(States.plan.name, "Build a plan");
// States.search.addTransition(States.analyze.name, "Analyze the task or question");
// States.search.addTransition(States.plan.name, "Build a plan");
//
// const current = States.initial;
// const context = {sources:[], task:"Find all of the policies"};
// const llm = await getDefaultLLM(Models[ModelID.GPT_3_5_AZ]);
//
// const stateMachine = new AssistantStateMachine(
//     llm,
//     "Document Search Assistant",
//     "Document Search Assistant",
//     States,
//     current);
//
// stateMachine.on(context, []);
