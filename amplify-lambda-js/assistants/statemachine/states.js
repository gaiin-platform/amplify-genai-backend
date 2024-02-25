import {getDefaultLLM} from "../../common/llm.js";
import {ModelID, Models} from "../../models/models.js";
import {ConsoleWritableStream} from "../../local/consoleWriteableStream.js";
import {newStatus} from "../../common/status.js";
import {
    sendDeltaToStream,
    sendStateEventToStream,
    sendStatusEventToStream,
    StatusOutputStream
} from "../../common/streams.js";
import Handlebars from "handlebars";
import yaml from 'js-yaml';
import {getContextMessages, getContextMessagesWithLLM} from "../../common/chat/rag/rag.js";
import {isKilled} from "../../requests/requestState.js";
import {getUser} from "../../common/params.js";
import {getDataSourcesInConversation, translateUserDataSourcesToHashDataSources} from "../../datasource/datasources.js";

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

const diffMaps = (map1, map2) => {
    const diff = {};

    if(!map2){
        return {};
    }
    if(!map1){
        return map2;
    }

    const keys = Object.keys(map2);
    keys.forEach((key) => {
        if (!map1[key] || map1[key] !== map2[key]) {
            diff[key] = map2[key];
        }
    });

    return diff;
}

const formatInformationSources = (sources) => {
    return (sources.length === 0 ? "NONE" :
        sources.map(source => formatInformationSource(source)).join("\n"));
}

const formatContextInformationItem = (source) => {
    if(!source){
        return "";
    }

    try {

        let value = source[1];
        // check if value is a string or convert to json if not
        if (typeof value !== "string") {
            value = JSON.stringify(value);
        }

        return `${source[0]}: ${source ? value.replaceAll(/(\r\n|\n|\r|\u2028|\u2029)/g, '\\n') : ""}`;
    } catch (e) {
        console.error("Error formatting context information item", source);
        console.error(e);
        return "";
    }
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
            if (result) {
                context.data = {...context.data, ...result};
            }
        }
    };
}

export const outputToResponse = (action, prefix = "", suffix = "") => {


    return {
        execute: async (llm, context, dataSources) => {
            const responseLLM = llm.clone();
            responseLLM.responseStream = context.responseStream;

            if(prefix) {
                const start = fillInTemplate(prefix, context.data);
                if(start) {
                    sendDeltaToStream(context.responseStream, "assistant", start);
                }
            }

            await action.execute(responseLLM, context, dataSources);

            if(suffix) {
                const end = fillInTemplate(suffix, context.data);
                if(end) {
                    sendDeltaToStream(context.responseStream, "assistant", suffix);
                }
            }

        }
    };
}

export const outputToStatus = (status, action) => {
    return {
        execute: async (llm, context, dataSources) => {
            const statusLLM = llm.clone();
            status.inProgress = true;

            if (status.summary) {
                status.summary = fillInTemplate(status.summary, context.data);
            }

            statusLLM.responseStream = context.responseStream;
            statusLLM.sendStatus(status);
            statusLLM.forceFlush();

            statusLLM.responseStream = new StatusOutputStream({},
                context.responseStream,
                status);

            try {
                await action.execute(statusLLM, context, dataSources);
            } finally {
                status.inProgress = false;
                statusLLM.responseStream = context.responseStream;
                llm.sendStatus(status);
                llm.forceFlush();
            }
        }
    };
}

export const updateStatus = (id, status) => {

    return {
        execute: async (llm, context, dataSources) => {

            const statusEvent = (context.status[id]) ? context.status[id] : newStatus(status);

            if (status.summary) {
                statusEvent.summary = fillInTemplate(status.summary, context.data);
            }
            if (status.message) {
                statusEvent.message = fillInTemplate(status.message, context.data);
            }

            sendStatusEventToStream(context.responseStream, statusEvent);

            context.status[id] = statusEvent;
        }
    }
}


export const outputAction = (template, src = "assistant") => {
    return {
        execute: (llm, context, dataSources) => {
            const msg = fillInTemplate(template, context.data);
            sendDeltaToStream(llm.responseStream, src, msg);
        }
    };
}


export const invokeAction = (fn, keys, outputKey) => {

    // Make sure that the keys are defined and an array
    if (!keys || !Array.isArray(keys)) {
        throw new Error("keys must be defined and an array");
    }
    // Check that the outputKey is a string if it is defined
    if (outputKey && typeof outputKey !== "string") {
        throw new Error("outputKey must be defined and a string");
    }

    return {
        execute: async (llm, context, dataSources) => {
            const args = keys.map(k => {
                if(k === "context"){
                    return context;
                }
                else if(k === "dataSources"){
                    return dataSources;
                }
                else if(k === "conversationDataSources"){
                    return context.conversationDataSources;
                }
                else if(k === "history"){
                    return context.history;
                }
                else if(k === "options"){
                    return context.options;
                }
                else {
                    return context.data[k]
                }
            });
            const result = await fn(...args);
            if (result && !outputKey) {
                context.data = {...context.data, ...result};
            } else if (result && outputKey) {
                context.data[outputKey] = result;
            }
        }
    };
}

export const mapKeysAction = (action, keyPrefix, outputKey = null, extractKey = null) => {

    // Make sure that the keyPrefix is defined and a string
    if (!keyPrefix || typeof keyPrefix !== "string") {
        throw new Error("keyPrefix must be defined and a string");
    }
    // Do the same for outputKey
    if (outputKey && typeof outputKey !== "string") {
        throw new Error("outputKey must be defined and a string");
    }

    return {
        execute: async (llm, context, dataSources) => {
            const allKeys = Object.keys(context.data);
            const keys = allKeys.filter(k => k.startsWith(keyPrefix));
            const args = keys.map(k => context.data[k]);
            const resultList = [];

            for (let i = 0; i < args.length; i++) {

                if(await isAssistantKilled(context)){
                    return;
                }

                const arg = args[i];
                let newContext = {...context, data: {...context.data, arg, i}};
                await action.execute(llm, newContext, dataSources);

                if (extractKey) {
                    newContext.data = newContext.data[extractKey];
                } else {
                    newContext.data = diffMaps(context.data, newContext.data);
                }

                if (!outputKey) {
                    context.data = {...context.data, [keys[i]]: newContext.data};
                }

                resultList.push(newContext.data);
            }

            if (outputKey) {
                context.data[outputKey] = resultList;
            }
        }
    };
}

export const reduceKeysAction = (action, keyPrefix, outputKey, extractKey = null) => {

    // Make sure that the keyPrefix is defined and a string
    if (!keyPrefix || typeof keyPrefix !== "string") {
        throw new Error("keyPrefix must be defined and a string");
    }
    // Do the same for outputKey
    if (!outputKey || typeof outputKey !== "string") {
        throw new Error("outputKey must be defined and a string");
    }

    return {
        execute: async (llm, context, dataSources) => {

            const allKeys = Object.keys(context.data);
            const keys = allKeys.filter(k => k.startsWith(keyPrefix));
            const args = keys.map(k => context.data[k]);

            const newContext = {...context, data: {...context.data, arg: args}};
            await action.execute(llm, newContext, dataSources);

            if (extractKey) {
                newContext.data = newContext.data[extractKey];
            } else {
                newContext.data = diffMaps(context.data, newContext.data);
            }

            outputKey = outputKey || keyPrefix;

            context.data[outputKey] = newContext.data;
        }
    };
}

export const ragAction = (config={addToHistory:true, addToContext:true}) => {
    return {
        execute: async (llm, context, dataSources) => {

            const dataSourcesInConversation = [...dataSources, ...context.history
                .filter( m => {
                    return m.data && m.data.dataSources
                }).flatMap(m => m.data.dataSources)];

            const dataSourceDetailsLookup = {};
            dataSourcesInConversation.forEach(ds => {
                dataSourceDetailsLookup[ds.id] = ds;
            });

            const selectedDataSources = context.dataSources;

            const messages = (config.query) ?
                [{role:"user", content:fillInTemplate(config.query, context.data)}] :
                context.history;

            const ragLLM = llm.clone();
            ragLLM.params = {
                ...llm.params,
                messages,
                options: {
                    ...llm.defaultBody,
                    model: Models[process.env.RAG_ASSISTANT_MODEL_ID],
                    skipRag: true
                }
            };

            const result = await getContextMessagesWithLLM(
                ragLLM,
                ragLLM.params,
                {...ragLLM.defaultBody, messages: context.history},
                selectedDataSources)

            if(config.addToHistory || config.addToHistory === undefined){
                context.history = [
                    ...context.history.slice(0,-1),
                    ...result.messages,
                    ...context.history.slice(-1)
                ];
            }
            if(config.addToContext || config.addToContext === undefined){
                context.data = {...context.data, possiblyRelevantInformation: result.sources};
            }
        }
    };
}

export const chainActions = (actions) => {
    return {
        execute: async (llm, context, dataSources) => {
            for (const action of actions) {
                await action.execute(llm, context, dataSources);
            }
        }
    };
}

export const outputContext = (template) => {
    return {
        execute: async (llm, context, dataSources) => {
            const result = fillInTemplate(template, context.data);
            sendDeltaToStream(context.responseStream, "assistant", result);
        }
    };
}

export const parallelActions = (actions) => {
    return {
        execute: async (llm, context, dataSources) => {
            const results = [];
            for (const action of actions) {
                results.push(action.execute(llm, context, dataSources));
            }
            await Promise.all(results);
        }
    };
}

function renderMarkdownOutline(data, depth = 0, omit=["arg","i"]) {
    const indent = '  '.repeat(depth);
    const bullet = depth > 0 ? '*' : ''; // Use bullets only for nested items

    if (Array.isArray(data)) {
        // Handling arrays
        return "\n"+data.map((item, index) => {
            const content = renderMarkdownOutline(item, depth + 1);
            return `${indent} ${index + 1}. ${content}\n`; // Enumerate with 1., 2., etc.
        }).join("");
    } else if (typeof data === 'object' && data !== null) {
        // Handling objects
        return "\n"+ Object.entries(data).map(([key, value]) => {
            if(!omit.includes(key)) {
                const content = renderMarkdownOutline(value, depth + 1);
                return `${indent}${bullet} **${key}**: ${content}\n`; // Make key bold
            } else {
                return "";
            };
        }).join("");
    } else {
        // Handling primitives (strings, numbers, etc.)
        return `${data}`;
    }
}

function matchKeys(context, keyRegex) {
    try {
        const pattern = new RegExp(keyRegex, "i");
        // Filter the entries in the context and collect a list of all entries
        // with keys that match the pattern
        const matches = Object.entries(context).filter(([key, val]) => {
            return pattern.test(key);
        });
        return matches;
    } catch (e) {
        console.error(e);
        return [];
    }
}

function matchKeysStr(context, keyRegex) {
    return matchKeys(context, keyRegex).map(e => `${e[0]}: ${JSON.stringify(e[1])}`).join("\n");
}

function fillInTemplate(templateStr, contextData) {

    let result = templateStr;
    try {
        Handlebars.registerHelper('json', function (context) {
            return JSON.stringify(contextData);
        });

        Handlebars.registerHelper('yaml', function (context) {
            return yaml.dump(contextData);
        });

        Handlebars.registerHelper("contextOutline",function (conf) {
            let obj = this;

            if(conf.hash.pattern){
                obj = matchKeys(obj, conf.hash.pattern || ".*").map((key,value)=>{
                    return {key:key, value:value};
                });
            }

            const outline = renderMarkdownOutline(obj);
            return outline;
        })

        Handlebars.registerHelper("contextOutlineWith",function (obj, options) {
            const pattern = options.hash.pattern;
            let withObj = obj;

            if(pattern){
                withObj = matchKeys(obj, pattern || ".*").map((key,value)=>{
                    return {key:key, value:value};
                });
            }

            return renderMarkdownOutline(withObj);
        })

        Handlebars.registerHelper("contextList", function(context, options) {
            const pattern = options.hash.pattern;
            const newContext = matchKeys(context, pattern || ".*").map(
                e => {return {key:e[0], value:e[1]}}
            );

            return options.fn({contextItems:newContext});
        });

        Handlebars.registerHelper('contextWith', function (obj, options) {
            const pattern = options.hash.pattern;
            return matchKeysStr(obj, pattern || ".*");
        })

        Handlebars.registerHelper('context', function (pattern) {
            return matchKeysStr(contextData, pattern || ".*");
        })

        const template = Handlebars.compile(templateStr);
        result = template(contextData);

    } catch (e) {
        console.error(e);
    }

    return result;
}

export class PromptForDataAction {

    constructor(prompt, stateKeys, stateChecker, retries = 3,
                config={skipRag: true, ragOnly: false, includeThoughts:false, streamResult:true}) {
        this.prompt = prompt;
        this.stateKeys = stateKeys;
        this.stateChecker = stateChecker || ((result) => Object.keys(stateKeys).every(k => result[k]))
        this.retries = retries;
        this.streamResults = true;
        this.includeThoughts = config.includeThoughts;
        this.config = config;
    }

    async execute(ollm, context, dataSources) {

        const llm = ollm.clone();
        configureLLM(this.config, llm);

        let promptText = fillInTemplate(this.prompt, context.data);

        const result = await llm.promptForData(
            {messages: [...context.history, {role: "user", content: promptText}]},
            dataSources,
            this.prompt,
            this.stateKeys,
            (this.streamResults) ? llm.responseStream : null,
            this.stateChecker,
            this.retries,
            this.includeThoughts
        );

        if (result) {
            context.data = {...context.data, ...result};
        }
    }
}

const getParam = (config, key, defaultValue) => {
    if(config[key] === undefined){
        return defaultValue;
    }
    return config[key] || defaultValue;
}

const configureLLM = (config, llm) => {
    llm.params.options = {
        ...llm.params.options,
        skipRag: (config.skipRag !== undefined) ? config.skipRag : true,
        ragOnly: (config.ragOnly !== undefined) ? config.ragOnly : false,
    }

    if (config.params !== undefined) {
        llm.params = {...llm.params, ...config.params};
    }

    if (config.options !== undefined) {
        llm.params.options = {...llm.params.options, ...config.options};
    }
}

export class PromptAction {

    constructor(prompt, outputKey = "response", retries = 3, streamResults = true,
                config={skipRag: true, ragOnly: false}) {
        this.prompt = prompt;
        this.outputKey = outputKey || "response";
        this.streamResults = streamResults;
        this.retries = retries;
        this.config = config;
    }

    async execute(ollm, context, dataSources) {

        const llm = ollm.clone();
        configureLLM(this.config, llm);

        if(this.config.dataSources !== undefined){
            dataSources = this.config.dataSources;
        }
        else if((!dataSources || dataSources.length === 0) && context.dataSources.length > 0){
            dataSources = context.dataSources;
        }


        const promptText = (this.prompt != null)? fillInTemplate(this.prompt, context.data) : null;

        let newMessages = (promptText != null) ?
            [...context.history, {role: "user", content:promptText}] :
            [...context.history.slice(0,-1)];

        const result = await llm.promptForString(
            {messages: newMessages},
            dataSources,
            promptText || context.history.slice(-1)[0].content,
            (this.streamResults) ? llm.responseStream : null,
            this.retries
        );

        if (result) {
            context.data = {...context.data, [this.outputKey]: result};
        }
    }
}


const STATUS_STREAM = "status";
const RESPONSE_STREAM = "response";

export class AssistantState {

    constructor(name, description, entryAction = null, endState = false,
                config = {
                    failOnError:false,
                    omitDocuments: false,
                    extraInstructions: {preInstructions: "", postInstructions: ""},
                    stream: {target: STATUS_STREAM, passThrough: true}
                }) {
        this.name = name;
        this.description = description;
        this.entryAction = entryAction;
        this.extraInstructions = config.extraInstructions || {};
        this.transitions = [];
        this.endState = endState;
        this.stream = config.stream || {target: STATUS_STREAM, passThrough: true};
        this.config = config;

        // If insertDocuments is not defined, default to true
        this.omitDocuments = config.omitDocuments;
    }

    addTransition(toStateName, description) {
        this.transitions.push({to: toStateName, description: description});
    }

    buildPrompt(context, dataSources) {
        return [
            {role: "system", content: buildSystemPrompt(this)},
            {role: "user", content: buildStatePrompt(context, this, dataSources)},
        ]
    }

    async getNextState(llm, context, dataSources) {
        const messages = this.buildPrompt(context, dataSources);
        const chatRequest = {messages};
        const prefixes = ["thought", "state"];
        const checkResult = (result) => {
            // Verify we got a valid next state and that it is in the list of transitions
            return result.state && this.transitions.map(t => t.to).includes(result.state);
        }
        const maxAttempts = 3;

        const result = await llm.promptForPrefixData(
            chatRequest,
            prefixes,
            [],
            null,
            checkResult,
            maxAttempts);

        return result;
    }

    async invokeEntryAction(llm, context, dataSources) {
        const actionLLM = llm.clone();

        if(this.omitDocuments){
            actionLLM.params = {
                ...actionLLM.params,
                options:{...actionLLM.params.options, skipRag:true, ragOnly:true}
            };
        }

        const status = newStatus({sticky:true, inProgress: true, summary: this.description, message: "", icon: "bolt"})

        if (this.stream.target === STATUS_STREAM) {
            actionLLM.responseStream = new StatusOutputStream({},
                llm.responseStream,
                status);
        }

        if (this.stream.passThrough) {
            actionLLM.enablePassThrough();
        }

        if (this.entryAction) {
            try {
                await this.entryAction.execute(actionLLM, context, dataSources);
            } catch (e) {
                console.error("Error invoking entry action in state: " + this.name);
                console.error(e);

                if(this.failOnError){
                    throw e;
                }
            }
        }

        if (this.stream.target === STATUS_STREAM) {
            status.inProgress = false;
            actionLLM.responseStream = context.responseStream;
            actionLLM.sendStatus(status);
            actionLLM.forceFlush();
        }
    }

    async enter(llm, context, dataSources) {
        if(await isAssistantKilled(context)){
            return null;
        }

        await this.invokeEntryAction(llm, context, dataSources);

        if (this.transitions.length === 0) {
            return this.name;
        } else if (this.transitions.length === 1) {
            return this.transitions[0].to;
        }

        if(await isAssistantKilled(context)){
            return null;
        }

        const result = await this.getNextState(llm, context, dataSources);

        return result.state;
    }

}

export class HintState extends AssistantState {
    constructor(name, description, hint, endState = false) {
        super(name, description, null, endState, {extraInstructions: {postInstructions: hint}});
    }
}


export class DoneState extends AssistantState {
    constructor() {
        super("done", "Done", null, true);
    }
}

export const isAssistantKilled = async (context) => {
    try {
        if(context.assistantKilled){
            return true;
        }

        const user = getUser(context.params);
        const body = context.body;
        const killed = await isKilled(user, context.responseStream, body);
        if(killed){
            context.assistantKilled = true;
        }
        return killed;
    } catch (e){
        return true;
    }
}

export class AssistantStateMachine {

    constructor(name, description, statesByName, currentState, config = {}) {
        this.name = name;
        this.description = description;
        this.statesByName = statesByName;
        this.currentState = currentState;
        this.maxTransitions = config.maxTransitions || 100;
    }

    async on(llm, context, dataSources) {

        if (!context.data) {
            context.data = {};
        }

        let transitionsLeft = this.maxTransitions;

        while (!this.currentState.endState && transitionsLeft > 0) {

            if(await isAssistantKilled(context)){
                return;
            }

            transitionsLeft -= 1;
            const nextName = await this.currentState.enter(llm, context, dataSources);
            this.currentState = this.statesByName[nextName];

            if (this.currentState === undefined) {
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
        this.includeUserName = true;
        this.includeUserEmail = true;
    }

    createAssistantStateMachine() {
        return new AssistantStateMachine(
            this.name,
            this.description,
            this.states,
            this.initialState);
    }

    async handler(llm, params, body, dataSources, responseStream) {

        const user = getUser(params).split("@")[0];
        const niceUserName = user.split(".").map(s => s.charAt(0).toUpperCase() + s.slice(1)).join(" ");

        const convoDataSources = await translateUserDataSourcesToHashDataSources(
            getDataSourcesInConversation(body, true)
        );

        const context = {
            data: {
                dataSources: dataSources.map((ds,i) => {
                    return {id:i, type:ds.type, name:ds.name, metadata:ds.metadata};
                }),
                conversationDataSources: convoDataSources,
                userName: niceUserName,
                userEmail: getUser(params),
            },
            dataSources,
            conversationDataSources: convoDataSources,
            status: {},
            responseStream,
            params,
            body,
            history: body.messages,
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
                "question1": "a specific question",
                "question2": "another specific question",
                "question3": "another specific question"
            },
            (result) => {
                return result.question1;
            }
        )),
    read: new AssistantState("read",
        "Targeted Search",
        new PromptAction("What page in the document should we read next? If you aren't sure, just output '1'",
            {
                "page": "a number specifying the page"
            }
        )),
    plan: new AssistantState("plan",
        "PLan State",
        new PromptAction("Create a 5-10 step plan to accomplish the task requested by the user",
            {"plan": "a step by step plan"}
        )),
    gather: new AssistantState("gather",
        "Information Gathering",
        new PromptAction("Create a 5-10 step plan to accomplish the task requested by the user",
            {"plan": "a step by step plan"}
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
    (m) => {
        return true
    },
    (m) => {
        return true
    },
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
