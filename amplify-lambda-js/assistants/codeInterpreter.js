//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { sendStatusEventToStream, sendStateEventToStream, forceFlush } from "../common/streams.js";
import { newStatus } from "../common/status.js";
import { getLogger } from "../common/logging.js";
import { isKilled } from "../requests/requestState.js";
import { logCriticalError } from "../common/criticalLogger.js";
import { callUnifiedLLM } from "../llm/UnifiedLLMClient.js";

const logger = getLogger("Code-Interpreter");

// Tool definition presented to the LLM — follows the same shape as WEB_SEARCH_TOOL_DEFINITION.
export const CODE_INTERPRETER_TOOL_DEFINITION = {
    type: "function",
    function: {
        name: "execute_code",
        description:
            "Execute Python code in a secure sandbox environment. " +
            "Use this tool whenever the user asks you to run code, perform calculations, " +
            "generate files (CSV, PNG, PDF), create charts or visualisations, or analyse data. " +
            "Attached files are already loaded into the sandbox by their original filename — " +
            "reference them directly in your code (e.g. pd.read_csv('data.csv')). " +
            "The tool returns stdout/stderr output and any generated files.",
        parameters: {
            type: "object",
            properties: {
                code: {
                    type: "string",
                    description: "Valid Python code to execute in the sandbox."
                }
            },
            required: ["code"]
        }
    }
};

const CODE_INTERPRETER_SYSTEM_PROMPT =
    "You have access to a secure Python sandbox via the `execute_code` tool. " +
    "Always use this tool to run code rather than showing hypothetical output. " +
    "Rules:\n" +
    "1. Display a preview of the code you intend to run before calling the tool.\n" +
    "2. After execution, show the output in Markdown.\n" +
    "3. Reference generated files by their filename — do NOT include download links.\n" +
    "4. Do not attach duplicate files with identical content.\n" +
    "5. Always include generated files in your response.";

const description =
    "Executes Python in a secure sandbox, handling diverse data to craft files and visual graphs. " +
    "Use for complex mathematical operations, coding tasks, and generating PNG, PDF, or CSV files.";

async function fetchRequest(token, data, url) {
    try {
        const response = await fetch(url, {
            headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${token}`
            },
            method: "POST",
            body: JSON.stringify({ data })
        });
        if (!response.ok) throw new Error("Network response error");
        return await response.json();
    } catch (error) {
        logger.error(`Error invoking Code Interpreter Lambda: ${error}`);
        return null;
    }
}

const sendStatusMessage = (responseStream, message, inProgress = true, summary = "") => {
    sendStatusEventToStream(responseStream, newStatus({
        inProgress,
        message,
        summary,
        icon: "assistant",
        sticky: true
    }));
    forceFlush(responseStream);
};

// Mirrors extractToolCalls in toolLoop.js to handle all provider response shapes.
function extractToolCalls(result) {
    if (result.tool_calls && result.tool_calls.length > 0) return result.tool_calls;
    if (result.content && typeof result.content === "string") {
        const match = result.content.match(/\{"tool_calls":\s*\[([\s\S]*?)\]\}/);
        if (match) {
            try {
                return JSON.parse(match[0]).tool_calls || [];
            } catch (e) {
                return [];
            }
        }
    }
    return [];
}

// Structured tool result fed back to the LLM — includes stdout and generated file metadata.
function buildToolResultContent(responseData) {
    const inner = responseData?.data?.data ?? {};
    const result = { output: inner.textContent || "" };
    if (inner.content && inner.content.length > 0) {
        result.files = inner.content.map(f => ({
            type: f.type,
            file_key: f.values?.file_key,
            presigned_url: f.values?.presigned_url,
            file_key_low_res: f.values?.file_key_low_res,
            presigned_url_low_res: f.values?.presigned_url_low_res,
            file_size: f.values?.file_size
        }));
    }
    return JSON.stringify(result);
}

export const codeInterpreterAssistant = async (assistantBase) => {
    return {
        name: "Code Interpreter Assistant",
        displayName: "Code Interpreter",
        handlesDataSources: () => true,
        handlesModel: () => true,
        description,
        disclaimer: "",

        handler: async (params, body, ds, responseStream) => {
            const account = params.account;
            const token = account.accessToken;
            const options = body.options;
            const messages = body.messages;
            const model = options.model || body.model;

            // On any code interpreter failure: show the error status, then let the LLM
            // answer the original question directly as a fallback.
            const fallbackToLLM = async (statusMsg) => {
                sendStatusMessage(responseStream, statusMsg, false, "Code interpreter failed — falling back to assistant.");
                sendStatusMessage(responseStream, "Amplify Assistant is responding...", true);
                await assistantBase.handler(params, { ...body, messages, options: { ...options, maxTokens: options.maxTokens || 4000 } }, ds, responseStream);
            };

            let codeInterpreterRecordId = options.codeInterpreterRecordId || null;

            // If files are attached, create the session now so they are loaded into the
            // sandbox before the LLM call — the LLM can then reference them by filename.
            const fileKeys = messages.flatMap(m => m.data?.dataSources ?? []).map(d => d.id);

            if (fileKeys.length > 0 && codeInterpreterRecordId === null) {
                if (await isKilled(account.user, responseStream, body)) return;

                sendStatusMessage(responseStream, "Preparing code interpreter session with your files...");
                const createResponse = await fetchRequest(
                    token, { dataSources: fileKeys },
                    process.env.API_BASE_URL + "/assistant/create/codeinterpreter"
                );

                if (createResponse?.success && createResponse.data) {
                    codeInterpreterRecordId = createResponse.data.codeInterpreterRecordId;
                    sendStateEventToStream(responseStream, { codeInterpreterRecordId });
                } else {
                    const errMsg = String(createResponse?.error);
                    logger.error("Failed to create session for file upload: %s", errMsg);
                    logCriticalError({
                        functionName: "codeInterpreter_sessionCreation",
                        errorType: "SessionCreationFailure",
                        errorMessage: errMsg,
                        currentUser: account?.user || "unknown",
                        severity: "HIGH",
                        stackTrace: "",
                        context: { requestId: options?.requestId || "unknown" }
                    }).catch(err => logger.error("Failed to log critical error:", err));
                    await fallbackToLLM(errMsg);
                    return;
                }
            }

            // Inject system prompt so the LLM uses the tool rather than narrating code.
            let llmMessages;
            if (messages.length > 0 && messages[0].role === "system") {
                llmMessages = [
                    { ...messages[0], content: messages[0].content + "\n\n" + CODE_INTERPRETER_SYSTEM_PROMPT },
                    ...messages.slice(1)
                ];
            } else {
                llmMessages = [{ role: "system", content: CODE_INTERPRETER_SYSTEM_PROMPT }, ...messages];
            }

            // First LLM call: tool selection. keepStreamOpen so we can continue after.
            if (await isKilled(account.user, responseStream, body)) return;
            sendStatusMessage(responseStream, "Code interpreter is analysing your request...");

            let firstCallResult;
            try {
                firstCallResult = await callUnifiedLLM(
                    { ...params, options: { ...params.options, model } },
                    llmMessages,
                    responseStream,
                    {
                        tools: [CODE_INTERPRETER_TOOL_DEFINITION],
                        tool_choice: "required",
                        keepStreamOpen: true,
                        temperature: options.temperature,
                        max_tokens: options.maxTokens || 4000
                    }
                );
            } catch (err) {
                logger.error("First LLM call failed: %s", err.message);
                sendStatusMessage(responseStream, String(err.message), false, "Code interpreter LLM call failed.");
                return;
            }

            const toolCalls = extractToolCalls(firstCallResult);

            // LLM decided no code execution is needed — stream response directly.
            if (!toolCalls || toolCalls.length === 0) {
                logger.info("LLM did not call execute_code — routing to base assistant.");
                sendStatusMessage(responseStream, "Amplify Assistant is responding...", true);
                await assistantBase.handler(params, { ...body, messages: llmMessages, options: { ...options, maxTokens: options.maxTokens || 4000 } }, ds, responseStream);
                return;
            }

            const toolCall = toolCalls[0];
            let args = {};
            try {
                args = toolCall.function?.arguments ? JSON.parse(toolCall.function.arguments) : {};
            } catch (e) {
                logger.warn("Failed to parse tool call arguments: %s", e.message);
            }
            const code = args.code || "";

            // Lazy session creation — only reached when no files were attached.
            if (codeInterpreterRecordId === null) {
                if (await isKilled(account.user, responseStream, body)) return;

                sendStatusMessage(responseStream, "Starting code interpreter session...");
                const createResponse = await fetchRequest(
                    token, { dataSources: [] },
                    process.env.API_BASE_URL + "/assistant/create/codeinterpreter"
                );

                if (createResponse?.success && createResponse.data) {
                    codeInterpreterRecordId = createResponse.data.codeInterpreterRecordId;
                    sendStateEventToStream(responseStream, { codeInterpreterRecordId });
                } else {
                    const errMsg = String(createResponse?.error);
                    logger.error("Failed to create session: %s", errMsg);
                    logCriticalError({
                        functionName: "codeInterpreter_sessionCreation",
                        errorType: "SessionCreationFailure",
                        errorMessage: errMsg,
                        currentUser: account?.user || "unknown",
                        severity: "HIGH",
                        stackTrace: "",
                        context: { requestId: options?.requestId || "unknown", codeInterpreterRecordId: "N/A" }
                    }).catch(err => logger.error("Failed to log critical error:", err));
                    await fallbackToLLM(errMsg);
                    return;
                }
            }

            // Execute the LLM-written code via the Python lambda.
            if (await isKilled(account.user, responseStream, body)) return;
            sendStatusMessage(responseStream, "Code interpreter is executing your code...");

            const executionResponse = await fetchRequest(
                token,
                { codeInterpreterRecordId, messages: [{ role: "user", content: code }], accountId: account.accountId || "general_account", requestId: options.requestId },
                process.env.API_BASE_URL + "/assistant/chat/codeinterpreter"
            );

            let toolResultContent;
            let ciStateData = null;

            if (executionResponse?.success && executionResponse.data) {
                sendStatusMessage(responseStream, "Code execution complete — generating response...");
                const { textContent, ...messageData } = executionResponse.data.data;
                ciStateData = messageData;
                toolResultContent = buildToolResultContent(executionResponse);
            } else {
                const errMsg = String(executionResponse?.error || "Unknown execution error");
                logger.error("Code execution failed: %s", errMsg);
                sendStateEventToStream(responseStream, {
                    codeInterpreter: { error: errMsg.includes("session_expired") ? "session" : errMsg }
                });
                logCriticalError({
                    functionName: "codeInterpreter_executionFailure",
                    errorType: "CodeInterpreterExecutionFailure",
                    errorMessage: `Code execution failed: ${errMsg}`,
                    currentUser: account?.user || "unknown",
                    severity: "HIGH",
                    stackTrace: "",
                    context: {
                        requestId: options?.requestId || "unknown",
                        codeInterpreterRecordId: codeInterpreterRecordId || "N/A",
                        hasRecordId: !!codeInterpreterRecordId,
                        errorDetails: errMsg,
                        accountId: account?.accountId || "general_account"
                    }
                }).catch(err => logger.error("Failed to log critical error:", err));
                await fallbackToLLM(errMsg);
                return;
            }

            if (ciStateData) {
                sendStateEventToStream(responseStream, { codeInterpreter: ciStateData });
            }

            // Second LLM call: format and stream the final response with the tool result in context.
            if (await isKilled(account.user, responseStream, body)) return;
            sendStatusMessage(responseStream, "Amplify Assistant is responding...", true);

            const messagesWithToolResult = [
                ...llmMessages,
                {
                    role: "assistant",
                    content: firstCallResult.content || "",
                    tool_calls: [{ id: toolCall.id, type: toolCall.type || "function", function: toolCall.function }]
                },
                {
                    role: "tool",
                    tool_call_id: toolCall.id,
                    content: toolResultContent
                }
            ];

            await assistantBase.handler(params, {
                ...body,
                messages: messagesWithToolResult,
                max_tokens: options.maxTokens || 4000,
                dataSources: [],
                imageSources: [],
                options: { ...options, disableDataSources: true, maxTokens: options.maxTokens || 4000 }
            }, [], responseStream);
        }
    };
};
