//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import {sendDeltaToStream, sendStatusEventToStream, sendStateEventToStream, forceFlush} from "../common/streams.js";
import {newStatus} from "../common/status.js";
import {getLogger} from "../common/logging.js";
import {isKilled} from "../requests/requestState.js";
import {logCriticalError} from "../common/criticalLogger.js";

const logger = getLogger("Code-Interpreter");


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
                            Do not attach "DUPLICATES" OF THE SAME files with IDENTICAL content, regardless of their extensions. ONLY ATTACH IT ONCE. Ensure each file is unique in content before attaching to your response.
                            DO NOT FORGET TO INCLUDE YOUR GENERATED FILES IN YOUR RESPONSE !IMPORTANT ALWAYS ATTACH YOUR GENERATED FILES IN YOUR RESPONSE!` 



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
        logger.error(`Error invoking Code interpreter Lambda: ${error}`);
        return null;
    }
}

const fetchWithTimeout = (responseStream, token, chat_data, endpoint, timeout = 12000) => {
    return new Promise((resolve, reject) => {
        let timer;
        let statusCount = 0;
        const maxStatusMessages = 20; // 🚨 LAMBDA SAFETY: Prevent infinite status loop

        const handleSendStatusMessage = () => {
            statusCount++;
            
            // 🚨 CRITICAL: Prevent infinite timer chain in Lambda
            if (statusCount > maxStatusMessages) {
                clearTimeout(timer);
                reject(new Error("Code interpreter timeout - too many status messages"));
                return;
            }
            
            sendStatusMessage(responseStream, "Code interpreter needs a few more moments...");
            timer = setTimeout(handleSendStatusMessage, timeout);
        };
        timer = setTimeout(handleSendStatusMessage, timeout);

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

const sendStatusMessage = (responseStream, message, inProgress=true, summary='', ) => {
    sendStatusEventToStream(responseStream, newStatus(
        {
            inProgress: inProgress,
            message: message,
            summary: summary,
            icon: "assistant",
            sticky: true
        }));
    forceFlush(responseStream); // 🚨 CRITICAL: Force flush for real-time status updates
}

const handleUserErrorMessage = (responseStream, responseErrorMessage, account, options, codeInterpreterRecordId) => {
    if (responseErrorMessage) {
        sendStatusMessage(responseStream, String(responseErrorMessage), false, "Code interpreter response failed. View Error:");
        logger.debug(`Code interpreter Response was unsuccessful:  ${responseErrorMessage}`);
        const error = responseErrorMessage.includes("session_expired") ? 'session' : responseErrorMessage;
        sendStateEventToStream(responseStream, { codeInterpreter: { error: error } });
    } else {
        sendStateEventToStream(responseStream, { codeInterpreter: { error: "Unknown Error - Internal Server Error" } });
    }

    // CRITICAL: Code interpreter execution failed - user blocked from executing code (fire-and-forget)
    logCriticalError({
        functionName: 'codeInterpreter_executionFailure',
        errorType: 'CodeInterpreterExecutionFailure',
        errorMessage: `Code interpreter execution failed: ${responseErrorMessage || "Unknown error"}`,
        currentUser: account?.user || 'unknown',
        severity: 'HIGH',
        stackTrace: '',
        context: {
            requestId: options?.requestId || 'unknown',
            codeInterpreterRecordId: codeInterpreterRecordId || 'N/A',
            hasRecordId: !!codeInterpreterRecordId,
            errorDetails: responseErrorMessage || 'No error details',
            accountId: account?.accountId || 'general_account'
        }
    }).catch(err => logger.error('Failed to log critical error:', err));

    sendStatusMessage(responseStream, "Amplify Assistant is responding...");
}

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

        handler: async (params, body, ds, responseStream) => {
            // Code interpreter handles external API calls then delegates to base assistant

            let codeInterpreterResponse = '';
        
            const account = params.account;
            const token = account.accessToken;
            const options = body.options;
            const messages = body.messages;
    
            let codeInterpreterRecordId = options.codeInterpreterRecordId || null;
            const userPrompt = messages.at(-1)['content'];
            // The conversation currently does not have a codeInterpreterRecordId in our database
            if (codeInterpreterRecordId === null) {

                // Check killswitch before long-running assistant creation
                if (await isKilled(account.user, responseStream, body)) return;

                const createData = {
                    dataSources: [],
                }

                const responseData = await fetchRequest(token, createData, process.env.API_BASE_URL + '/assistant/create/codeinterpreter');

                if (responseData && responseData.success && responseData.data) {
                    codeInterpreterRecordId = responseData.data.codeInterpreterRecordId;
                    // send the record id back to be saved in the conversation options
                    sendStateEventToStream(responseStream, { codeInterpreterRecordId: codeInterpreterRecordId });
                    sendStatusMessage(responseStream, "Code interpreter is making progress on your request...");
                    logger.debug("Code Interpreter record created...");
                } else {
                    handleUserErrorMessage(responseStream, String(responseData && responseData.error), account, options, null);
                }

            }
            // ensure that codeInterpreterRecordId is not null (in case record creation was necessary and failed)
            if (codeInterpreterRecordId) {

                // Check killswitch before long-running chat execution
                if (await isKilled(account.user, responseStream, body)) return;

                const chat_data = {
                    codeInterpreterRecordId: codeInterpreterRecordId,
                    messages: messages.slice(1),
                    accountId: account.accountId || 'general_account',
                    requestId: options.requestId
                }

                const responseData = await fetchWithTimeout(responseStream, token, chat_data, process.env.API_BASE_URL + '/assistant/chat/codeinterpreter');
                // check for successfull response
                if (responseData && responseData.success && responseData.data) {
                    sendStatusMessage(responseStream, "Finalizing code interpreter results...");
                    const { textContent, ...messageData } = responseData.data.data;
                    codeInterpreterResponse = textContent;
                    sendStateEventToStream(responseStream, { codeInterpreter: messageData });
                } else {
                    // If the record was not found (e.g. existing conversation from the old OpenAI
                    // backend, or a DynamoDB record that was manually deleted), treat it the same
                    // as a missing record — clear the stale ID and create a fresh one.
                    const errorMsg = String(responseData && responseData.error);
                    if (errorMsg.includes('not found') || errorMsg.includes('Assistant not found')) {
                        logger.info('Record not found for codeInterpreterRecordId %s — creating fresh record', codeInterpreterRecordId);
                        codeInterpreterRecordId = null;

                        if (await isKilled(account.user, responseStream, body)) return;

                        const createData = { dataSources: [] };
                        const createResponse = await fetchRequest(token, createData, process.env.API_BASE_URL + '/assistant/create/codeinterpreter');

                        if (createResponse && createResponse.success && createResponse.data) {
                            codeInterpreterRecordId = createResponse.data.codeInterpreterRecordId;
                            sendStateEventToStream(responseStream, { codeInterpreterRecordId: codeInterpreterRecordId });
                            sendStatusMessage(responseStream, "Code interpreter is making progress on your request...");

                            if (await isKilled(account.user, responseStream, body)) return;

                            const retryData = {
                                codeInterpreterRecordId: codeInterpreterRecordId,
                                messages: messages.slice(1),
                                accountId: account.accountId || 'general_account',
                                requestId: options.requestId
                            };
                            const retryResponse = await fetchWithTimeout(responseStream, token, retryData, process.env.API_BASE_URL + '/assistant/chat/codeinterpreter');
                            if (retryResponse && retryResponse.success && retryResponse.data) {
                                sendStatusMessage(responseStream, "Finalizing code interpreter results...");
                                const { textContent, ...messageData } = retryResponse.data.data;
                                codeInterpreterResponse = textContent;
                                sendStateEventToStream(responseStream, { codeInterpreter: messageData });
                            } else {
                                handleUserErrorMessage(responseStream, String(retryResponse && retryResponse.error), account, options, codeInterpreterRecordId);
                            }
                        } else {
                            handleUserErrorMessage(responseStream, String(createResponse && createResponse.error), account, options, null);
                        }
                    } else {
                        handleUserErrorMessage(responseStream, errorMsg, account, options, codeInterpreterRecordId);
                    }
                }

            }
            let updatedMessages = messages.slice(0, -1);
            let dataSourceOptions = {disableDataSources: false};
            if (codeInterpreterResponse) {
                dataSourceOptions.disableDataSources = true;
                ds = [];
                if (!body?.options?.model?.supportsImages) {
                    updatedMessages.map(m => {
                    if (m.data && m.data.dataSources) {
                        m.data.dataSources = []
                    }
                    });
                    body.imageSources = [];
                }
                updatedMessages.push({
                    role: 'user',
                    content: reviewPrompt(userPrompt, codeInterpreterResponse),
                });
            } else {
                logger.debug("Code interpreter was unavailable...");
                updatedMessages.push({
                    role: 'user',
                    content: `User Prompt:\n${userPrompt}\n\n The user expected to reach code interpreter however, we did not recieve a code interpreter response. If you can answer the query, please do so. 
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
            // unless we support with user defined assistants, we dont need this for now
            // for now we will include the ds in the current message
            // if (assistant.dataSources) updatedBody.imageSources =  [...(updatedBody.imageSources || []), ...assistant.dataSources.filter(ds => isImage(ds))];

            // Check killswitch before final base handler call
            if (await isKilled(account.user, responseStream, body)) return;

            await assistantBase.handler(
                params,
                updatedBody,
                ds,
                responseStream);
        }
    }
}

