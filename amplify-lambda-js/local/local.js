//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {ConsoleWritableStream} from "./consoleWriteableStream.js";
import {chatWithDataStateless} from "../common/chatWithData.js";
import {chat} from "../azure/openai.js";
import {getLLMConfig} from "./common/secrets.js";
import {getSecret} from "../common/secrets.js";
import * as fs from "fs";
import {LLM} from "../common/llm.js";
import {workflowSchema} from "../workflow/workflow.js";


async function main() {

    let modelId;
    let model;
    let prompt;
    let dataSources = [];
    let choices = [];
    let jsonschema;
    let functions = [];
    let promptForBoolean = false;
    let promptForWorkflow = false;
    let chatRequest = {
        "messages":[
        ],
        "max_tokens": 1000,
        "temperature": 0.5,
        "top_p": 1,
        "n": 1,
        "stream": true
    };

    // Custom function to handle multiple occurrences of an argument
    const handleMultipleArgs = (array, value) => {
        if (value.startsWith('{') && value.endsWith('}')) {
            array.push(JSON.parse(value));
        } else {
            array.push(value);
        }
    };

    // Iterate over the command line arguments
    for (let i = 2; i < process.argv.length; i++) {
        const arg = process.argv[i];

        // Handle each flag
        switch (arg) {
            case '-f':
                if(i + 1 < process.argv.length){
                    const filePath = process.argv[++i];
                    chatRequest = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
                } else {
                    throw new Error('No file path specified for -f option.');
                }
                break;
            case '-m':
                modelId = process.argv[++i];
                model = {id: modelId}; // most likely missing data attributes since eliminating Models 
                if(!model){
                    console.log("Invalid model: "+modelId);
                    return;
                }
                break;
            case '-p':
                prompt = process.argv[++i];
                chatRequest.messages.push({
                    "role": "user",
                    "content": prompt
                });
                break;
            case '-d':
                if(i + 1 < process.argv.length){
                    handleMultipleArgs(dataSources, process.argv[++i]);
                } else {
                    throw new Error('No data source specified for -d option.');
                }
                break;
            case '-c':
                if(i + 1 < process.argv.length){
                    handleMultipleArgs(choices, process.argv[++i]);
                } else {
                    throw new Error('No choice specified for -c option.');
                }
                break;
            case '-json':
                if(i + 1 < process.argv.length && process.argv[i + 1].startsWith('{')){
                    jsonschema = JSON.parse(process.argv[++i]);
                } else {
                    throw new Error('No JSON schema specified for -json option.');
                }
                break;
            case '-fn':
                if(i + 1 < process.argv.length && process.argv[i + 1].startsWith('{')){
                    handleMultipleArgs(functions, process.argv[++i]);
                } else {
                    throw new Error('No function JSON specified for -fn option.');
                }
                break;
            case '-boolean':
                promptForBoolean = true;
                break;
            case '-workflow':
                promptForWorkflow = true;
                break;
            // Handle non-flag arguments here if necessary.
            default:
                // Implement handling or error message for unexpected arguments
                break;
        }
    }

    dataSources = dataSources.map((s) => ({id:s}));

    const chatFn = async (body, writable, context, options) => {
        return await chat(getLLMConfig, body, writable, context);
    }

    const llm = new LLM(
            chatFn,
            {account: {user: "console"}, model: model},
            new ConsoleWritableStream(true));

    let response;

    if(promptForBoolean){
        response = await llm.promptForBoolean(chatRequest, dataSources);
    }
    else if(promptForWorkflow){
        response = await llm.promptForJson(chatRequest, workflowSchema, dataSources);
    }
    else if(jsonschema){
        response = await llm.promptForJson(chatRequest, jsonschema, dataSources);
    }
    else if(choices.length > 0){
        response = await llm.promptForChoice(chatRequest, choices, dataSources);
    }
    else {
        response = await llm.prompt(chatRequest, dataSources);
    }

    if(response){
        console.log(response);
    }
}


main();