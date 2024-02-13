import {setModel} from "../common/params.js";
import {Models} from "../models/models.js";


export const generalDocsAssistant = {
    name: "documents",
    displayName: "Document Search Assistant",
    handlesDataSources: (ds) => {
        return true;
    },
    handlesModel: (model) => {
        return true;
    },
    description: "This assistant is a good default assistant for tasks that are based on processing " +
        "all kinds of documents. If there is a more specific document assistant, it is better to use that one. " +
        "Otherwise, this one works well for most document processing tasks.",
    handler: async (llm, params, body, dataSources, responseStream) => {

        const model = Models[process.env.RAG_ASSISTANT_MODEL_ID];
        // We override the model that is being used for the prompt
        llm.setModel(model);

        const ragPlanPrompt = {
            messages:[
                {role:"system",
                    content:"Act as an expert at searching documents for answers. You analyze the task or question and figure out what to do. You check if there is sufficient information to give a good and accurate answer. You build concise and efficient plans. \n" +
                        "\n" +
                        "You output a plan in the format:\n" +
                        "```plan\n" +
                        "action: search|readEntireDocument\n" +
                        "questions: [\"question 1 ...\", \"question ...\", ...]\n" +
                        "```\n" +
                        "\n" +
                        "You plan must have an action, either \"search\" or \"readEntireDocument\" and a list of questions to answer that will help accomplish the original task. \n" +
                        "\n" +
                        "You ALWAYS output a ```plan code block."
                },
                {role:"user",
                    content:search
                },
                {role:"user",
                    content:"```plan"
                }]
        };

        const planData = await llm.promptForPrefixData(
            ragPlanPrompt,
            ["action","questions"],
            dataSources = [],
            null,
            (result)=>(result.action && result.questions), 3);



    }
}