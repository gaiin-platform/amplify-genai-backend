import {chat} from "../azure/openai.js";
import {Models} from "../models/models.js";
import {executeWorkflow} from "../workflow/workflow.js";
import {ConsoleWritableStream} from "./consoleWriteableStream.js";
import {getSecret} from "../common/secrets.js";

const secretData = await getSecret(process.env.SECRETS_NAME);
const apiKey = JSON.parse(secretData).OPENAI_API_KEY;

async function main() {
    const modelId = process.argv.slice(2, 3)[0];
    const prompt = process.argv.slice(3, 4)[0];
    const dataSources = ["s3://jules.white@vanderbilt.edu/2024-01-11/9927b362-609c-4b7d-90ec-4a09024ee27c.json"]
        .map((s) => ({id: s}));


    const chatFn = async (body, writable, context) => {
        return await chat(apiKey, body, writable, context);
    }


    const chatRequest = {
        "messages": [],
        "max_tokens": 1000,
        "temperature": 0.5,
        "top_p": 1,
        "n": 1,
        "stream": true,
        options: {
            model: {id: modelId}
        }
    };

    const model = Models[modelId];
    if (!model) {
        console.log("Invalid model: " + modelId);
        return;
    }

    const workflow = {
        resultKey: "answer",
        steps: [
            {
                statusMessage: "Reading the document to find relevant information...",
                input: [dataSources[0].id],
                prompt: `Provide as many sentences as you can from the source material and include page numbers relevant to 
                answering the question: 
                ------------------
                What distinguishes Homolopsis tuberculata from other species?
                ------------------
                Do not reword anything. Provide verbatim excerpts. 
                If there is no relevant information, respond with "nothing relevant on page XYZ".`
                ,
                outputTo: "p1"
            },
            {
                statusMessage: "Condensing my notes...",
                input: ["p1"],
                reduce: `Provide as many sentences as you can from the source material and include page numbers relevant to 
                answering the question: 
                ------------------
                What distinguishes Homolopsis tuberculata from other species?
                ------------------
                Do not reword anything. Provide verbatim excerpts. 
                If there is no relevant information, respond with "nothing relevant on page XYZ".`,
                outputTo: "combined"
            },
            {
                statusMessage: "Answering the question...",
                input: ["combined"],
                reduce: `
                What distinguishes Homolopsis tuberculata from other species? 
                `,
                outputTo: "answer"
            },
        ]
    }

    console.log("Starting local workflow....");

    const response = await executeWorkflow(
        {
            workflow,
            body: chatRequest,
            params: {account: {user: "console"}, model: model},
            chatFn: chatFn,
            chatRequest,
            dataSources,
            responseStream: new ConsoleWritableStream(true)
        });

    if (response) {
        // This indicates an error
        console.log(response);
    }
}


main();