//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import {executeWorkflow} from "../workflow/workflow.js";


export const mapReduceAssistant = {
    name: "mapReduce",
    displayName: "Large Document Assistant",
    handlesDataSources: (ds) => {
        return true;
    },
    handlesModel: (model) => {
        return true;
    },
    description: "An assistant that can handle requests that are larger than the model's context window.",
    handler: async (params, body, dataSources, responseStream) => {
        // 🚀 BREAKTHROUGH: No longer need LLM parameter - create our own LiteLLM chatFn

        const task = body.messages.slice(-1)[0].content;

        const workflow = {
            resultKey: "answer",
            steps: [
                {
                    prompt:"__use_body__",
                    input: dataSources.map(ds => ds.id),
                    outputTo: "parts"
                },
                {
                    statusMessage: "Condensing my answer...",
                    input: ["parts"],
                    reduce:
`Above are the parts of the response to the task below. 
--------------
${task}
--------------             
Combine these parts into one cohesive answer.
Try to preserve the formatting from the best part. 
Make sure and preserve as much information as possible while still making the answer cohesive.

If the user refers to documents, information, data sources, etc., the parts above are your 
access to that information and you should use them to provide the best answer possible.
`,
                    outputTo: "answer"
                },
            ]
        }

        console.log("Starting local workflow....");

        // ✅ PHASE 1.3: Use model directly for workflow engine
        await executeWorkflow(
            {
                workflow,
                body,
                params,
                model: params.model,
                chatRequest:body,
                dataSources,
                responseStream,
                initialState:{}
            });

        console.log("Local workflow finished.");

        responseStream.end();
        //return llm.prompt(body, dataSources);
    }
};

