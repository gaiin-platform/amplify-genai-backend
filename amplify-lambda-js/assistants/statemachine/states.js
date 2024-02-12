import {getDefaultLLM} from "../../common/llm.js";
import {ModelID, Models} from "../../models/models.js";

const formatStateNamesAsEnum = (transitions) => {
    return transitions.map(t => t.to).join("|");
}

const formatInformationSource = (source) => {
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
Documents:
${formatDataSources(dataSources)}

Your next state options are:
${formatTransitions(state.transitions)}

Information you have collected from the documents so far:
${formatDataSources(context.sources)}

Task:
${context.task}


\`\`\`next
`;

    return prompt;
}



export class AssistantState {

    constructor(name, description, statePrompt, entryAction, endState=false) {
        this.name = name;
        this.description = description;
        this.entryAction = entryAction;
        this.statePrompt = statePrompt;
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
            this.entryAction();
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

export class AssistantStateMachine {

    constructor(llm, name, description, statesByName, currentState, config={}) {
        this.llm = llm;
        this.name = name;
        this.description = description;
        this.statesByName = statesByName;
        this.currentState = currentState;
        this.maxTransitions = config.maxTransitions || 100;
    }

    async on(context, dataSources) {

        let transitionsLeft = this.maxTransitions;

        while(!this.currentState.endState && transitionsLeft > 0) {
            transitionsLeft -= 1;
            const nextName = await this.currentState.enter(this.llm, context, dataSources);
            this.currentState = this.statesByName[nextName];
        }
    }

}
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
