//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import {sendDeltaToStream} from "../common/streams.js";
import {newStatus} from "../common/status.js";

const description =  `This assistants  executes Python in a secure sandbox, handling diverse data to craft files and visual graphs.
It tackles complex code and math challenges through iterative problem-solving, refining failed attempts into successful executions.
Use this for complex mathmatical operations and coding tasks that involve the need to run the code in a sandbox environment.
Only to be used when user specifically asks to use code interpreter or the user asks to create / generate png, pdf, or csv files.
Used when user specifically asks to use code interpreter or the user asks to create / generate png, pdf, or csv files.`

const additionalPrompt = `You have access to a sandboxed environment for writing and testing code when code is requested:
                            1. Anytime you write new code display a preview of the code to show your work.
                            2. Run the code to confirm that it runs.
                            3. Show us the input data and output of your code in Markdown
                            Always display code blocks in markdown. comment the code with explanations of what the code is doing for any complex sections of code. 
                            Do not include download links or mock download links! Instead tell me some information about the files you have produced and refer to them by their corresponding file name. For any created files, draw connections between the users prompt and how it relates to your generated files. Always attach and send back generated files.
                            
                            DO NOT FORGET TO INLCUDE YOUR GENERATE FILES IN YOUR RESPONSE !IMPORTANT` 



const reviewPrompt = (userPrompt, response) =>  
    `Review the response provided by another LLM below. Ensure the response meets the following criteria:
        - All code blocks are correctly displayed using Markdown syntax.
        - Complex sections of code are accompanied by comments that explain what the code is doing. (If applicable)
        - The response does not include any reference to download links or mock download links.
        - The response includes detailed explanations of any created files, referring to them by their corresponding file name. (If applicable)
    
    If the response violates any of the above rules or if there are areas for improvement, please edit the response to comply with these criteria and enhance its clarity or accuracy.
    Should the response inadequately address the user's prompt, add in a corrected or more thorough response (without any hallucinations) to ensure it accurately addresses the user's needs.
    Assume any files the user asked to generate have been generated and provided to the user, you personally will not have or see those files so, assume we do have them.

    User asked prompt:
    ${userPrompt}
    _____________________________
    Response to be reviewed:
    ${response}
    _____________________________
    
    Your response will be your refined version of the Response to be reviewed.
    You are NOT permitted to offer any thoughts, reasoning, or explanation about any refinements applied to the Response
    Your top priority is returning the refined Response so it seamlessly fits into the on-going conversation as to hide the fact it has undergone this refinement process. 
    Try to keep as much of the original Response content as possible. In cases where the Response does not make sense given the userPrompt, you may rewrite it with a more appropriate response. 
    `;


async function fetchRequest(token, data, url) {
    try {
        const response = await fetch(url, {
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`  //check on this, we may already have it 
                },
                method: 'POST',
                body: JSON.stringify({'data': data})
            }); 
            if (!response.ok) {
                throw new Error('Network response error');
            }
            return await response.json();
    } catch (error) {
        console.error('Error invoking Code interpreter Lambda: ', error);
        return null;
    }
}

const fetchWithTimeout = (llm, token, chat_data, endpoint, timeout = 10000) => {
    return new Promise((resolve, reject) => {
        let timer;

        const sendStatusMessage = () => {
            llm.sendStatus(newStatus(
                {
                    inProgress: true,
                    message: "Code interpreter needs a few more moments...",
                    icon: "assistant",
                    sticky: true
                }));
            timer = setTimeout(sendStatusMessage, timeout);
        };

        timer = setTimeout(sendStatusMessage, timeout);

        fetchRequest(token, chat_data, endpoint)
            .then(response => {
                clearTimeout(timer);
                resolve(response);
            })
            .catch(error => {
                clearTimeout(timer);
                reject(error);
            });
    });
};

export const codeInterpreterAssistant = async (assistantBase) => {
    return {
        name: 'Code Interpreter Assistant',
        displayName: 'Code Interpreter ',
        handlesDataSources: (ds) => {
            return true;
        },
        handlesModel: (model) => {
            return true;
        },
        description: description,

        disclaimer: '',

        handler: async (llm, params, body, ds, responseStream) => {

            let codeInterpreterResponse = '';
        
            const account = params.account;
            const token = account.accessToken;
            const options = body.options;
            const messages = body.messages;
    
            // if we have a codeInterpreterId then we can chat if we dont then we have to create first 
            // The conversation currently does have an assistantID in our database (which contains an assistantID with code_interpreter)
            let assistantId = options.codeInterpreterAssistantId || null;
            const userPrompt = messages.at(-1)['content'];
            // The conversation currently does not have an assistantID in our database 
            if (assistantId === null) {

                const createData = {
                    access_token: token,
                    name: "CodeInterpreter",
                    description: description, 
                    tags: [],
                    instructions:  options.prompt + additionalPrompt,
                    dataSources: [], // unless we make this userdefined assistant compatible then the assistant wont have any data sources. any ds in messages will be added to the openai thread 
                }
    
                const responseData = await fetchRequest(token, createData, process.env.ASSISTANTS_API_BASE_URL + '/assistant/create/codeinterpreter'); 
                
                if (responseData && responseData && responseData.success) {
                    assistantId = responseData.data.assistantId
                    //we need to ensure we send the assistant_id back to be saved in the conversation
                    sendDeltaToStream(responseStream, "codeInterpreter", `codeInterpreterAssistantId=${assistantId}`);
                    llm.sendStatus(newStatus(
                        {
                            inProgress: false,
                            message: "Code interpreter is making progress on your request...",
                            icon: "assistant",
                            sticky: true
                        }));
                    llm.forceFlush();
                } else {
                    console.log(`Error with creating an assistant: ${responseData}`)
                }

            }
            //ensure that assistant_id is not null (in case assistant creation was necessary and failed)
            if (assistantId) {
                // messages.at(-1)['content'];
                const chat_data = {
                    assistantId: assistantId,
                    messages: messages.slice(1),
                    accountId: account.accountId || 'general_account',
                    requestId: options.requestId
                }
    

                    const responseData = await fetchWithTimeout(llm, token, chat_data, process.env.ASSISTANTS_CHAT_CODE_INTERPRETER_ENDPOINT);
                    
                    llm.sendStatus(newStatus(
                        {
                            inProgress: false,
                            message: "Finalizing code interpreter results...",
                            icon: "assistant",
                            sticky: true
                        }));
                    llm.forceFlush();
                    
                    // logger.debug(responseData);
                    // check for successfull response
                    if (responseData && responseData.success && responseData.data) {
                        const response = responseData.data.data.textContent;
                        // console.log(response);
                        codeInterpreterResponse = response;
                        responseData.data.data.textContent = '';
                        sendDeltaToStream(responseStream, "codeInterpreter", `codeInterpreterResponseData=${JSON.stringify(responseData)}`);
                    } 

            } 
            let updatedMessages = messages.slice(0, -1);
            let dataSourceOptions = {disableDataSources: false};
            if (codeInterpreterResponse) {
                dataSourceOptions.disableDataSources = true;
                updatedMessages.push({
                    role: 'user',
                    content: reviewPrompt(userPrompt, codeInterpreterResponse),
                });
                // console.log(updatedMessages.at(-1)['content']);
            } else {
                console.log("Code interpreter was unavailable...");
                updatedMessages.push({
                    role: 'user',
                    content: `User Prompt:\n${userPrompt}\n\n The user expected to reach code interpreter however, I did not here from them. If you can answer the query, please do so. 
                              If you are asked to do something you can not do (ex. generate files) then tell the user that Code Interpreter was not available at this time and to try again. Also, if you can answer any of the query you are effectively able to do`
                });
            }

          
            const updatedBody = {
                ...body,
                messages: updatedMessages,
                max_tokens: 4000,
                options: {
                    ...body.options,
                    ...dataSourceOptions,
                    maxTokens: 4000
                }
            };
        
            // for now we will include the ds in the current message
            // if (assistant.dataSources) updatedBody.imageSources =  [...(updatedBody.imageSources || []), ...assistant.dataSources.filter(ds => isImage(ds))];

            await assistantBase.handler(
                llm,
                params,
                updatedBody,
                ds,
                responseStream);
        }
    }
}

