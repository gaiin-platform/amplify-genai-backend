//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {chat} from "../azure/openai.js";
import {executeWorkflow} from "../workflow/workflow.js";
import {ConsoleWritableStream} from "./consoleWriteableStream.js";
import {getLLMConfig} from "../common/secrets.js";

async function main() {
    const modelId = process.argv.slice(2, 3)[0];
    const prompt = process.argv.slice(3, 4)[0];
    const dataSources = []
        .map((s) => ({id: s}));


    const chatFn = async (body, writable, context) => {
        return await chat(getLLMConfig, body, writable, context);
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

    const model = {id: modelId}; // most likely missing data attributes since eliminating Models 
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