//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { sendStatusEventToStream, sendStateEventToStream, forceFlush } from "../common/streams.js";
import { newStatus } from "../common/status.js";
import { getLogger } from "../common/logging.js";
import { isKilled } from "../requests/requestState.js";
import { logCriticalError } from "../common/criticalLogger.js";
import { callUnifiedLLM } from "../llm/UnifiedLLMClient.js";
import { v4 as uuidv4 } from "uuid";

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
    "The sandbox persists for the entire conversation — files attached in any earlier message " +
    "remain available by their filename in later messages, even without being re-attached. " +
    "Never claim a previously attached file is unavailable; use execute_code to check for it " +
    "(e.g. os.listdir('.')) before saying you cannot access it. " +
    "Always use this tool to run code rather than showing hypothetical output. " +
    "Rules:\n" +
    "1. Do NOT include the Python code or raw sandbox output in your response.\n" +
    "2. Do NOT emit tool-call syntax (e.g. <function_name>, <invoke>) as visible text — call the tool directly.\n" +
    "3. Write a natural, helpful response based on what the code produced — explain findings, insights, or results in your own words.\n" +
    "4. Reference generated files by their filename — do NOT include download links.\n" +
    "5. Do not attach duplicate files with identical content.\n" +
    "6. Always include generated files in your response.";

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

// Structured tool result fed back to the LLM — includes stdout and generated file metadata
// (never the file bytes themselves, to keep the LLM's context small).
function buildToolResultContent(responseData) {
    const inner = responseData?.data?.data ?? {};
    const result = { output: inner.textContent || "" };
    if (inner.content && inner.content.length > 0) {
        result.files = inner.content.map(f => {
            const fileKey = f.values?.file_key || "";
            const fnMatch = fileKey.match(/-FN-([^/]+)$/);
            const fileName = fnMatch ? fnMatch[1] : fileKey.split("/").pop() || "generated_file";
            return {
                type: f.type,
                file_name: fileName,
                file_size: f.values?.file_size
            };
        });
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

            let codeInterpreterRecordId = options.codeInterpreterRecordId || body.codeInterpreterRecordId || null;

            // File keys come from body.dataSources (ds) and the last message's per-message
            // attachments. `ds` has already passed through translateUserDataSourcesToHashDataSources,
            // which rewrites ds.id to a global RAG key when the file is indexed — the original
            // per-user key is preserved at metadata.userDataSourceId, so prefer that.
            const dsFileKeys = (ds || []).map(d => d.metadata?.userDataSourceId || d.id).filter(Boolean);
            const lastMsgFileKeys = (messages[messages.length - 1]?.data?.dataSources ?? []).map(d => d.metadata?.userDataSourceId || d.id).filter(Boolean);
            const fileKeys = [...new Set([...dsFileKeys, ...lastMsgFileKeys])];

            // Map each file key to its original filename — S3 keys are random UUIDs, not
            // the real filename, so the sandbox needs this to load files under a name the
            // LLM's code can reference (e.g. pd.read_csv('sales_data.csv')).
            const fileKeyToName = {};
            for (const d of [...(ds || []), ...(messages[messages.length - 1]?.data?.dataSources ?? [])]) {
                const key = d.metadata?.userDataSourceId || d.id;
                if (key && d.name) fileKeyToName[key] = d.name;
            }

            if (codeInterpreterRecordId === null) {
                if (await isKilled(account.user, responseStream, body)) return;

                const statusMsg = fileKeys.length > 0
                    ? "Preparing code interpreter session with your files..."
                    : "Starting code interpreter session...";
                sendStatusMessage(responseStream, statusMsg);

                const createResponse = await fetchRequest(
                    token, { dataSources: fileKeys, fileNames: fileKeyToName },
                    process.env.API_BASE_URL + "/assistant/create/codeinterpreter"
                );

                if (createResponse?.success && createResponse.data) {
                    codeInterpreterRecordId = createResponse.data.codeInterpreterRecordId;
                    // Nest under "codeInterpreter" so the frontend's deepMerge accumulates
                    // it at currentState.codeInterpreter.codeInterpreterRecordId, which is
                    // where useChatSendService reads it and persists it on the conversation.
                    sendStateEventToStream(responseStream, { codeInterpreter: { codeInterpreterRecordId } });
                } else {
                    const errMsg = String(createResponse?.error || "Failed to create session");
                    logger.error("Failed to create code interpreter session: %s", errMsg);
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

            const MAX_TOOL_ITERATIONS = 5;
            let conversationMessages = llmMessages;
            let executedAny = false;
            const allGeneratedFiles = [];

            for (let iteration = 0; iteration < MAX_TOOL_ITERATIONS; iteration++) {
                if (await isKilled(account.user, responseStream, body)) return;
                sendStatusMessage(responseStream, "Code interpreter is analysing your request...");

                let callResult;
                try {
                    callResult = await callUnifiedLLM(
                        { ...params, options: { ...params.options, model } },
                        conversationMessages,
                        null,
                        {
                            tools: [CODE_INTERPRETER_TOOL_DEFINITION],
                            tool_choice: "auto",
                            disableReasoning: true,
                            temperature: options.temperature,
                            max_tokens: options.maxTokens || 4000
                        }
                    );
                } catch (err) {
                    logger.error("Code interpreter LLM call failed: %s", err.message);
                    sendStatusMessage(responseStream, String(err.message), false, "Code interpreter LLM call failed.");
                    return;
                }

                const toolCalls = extractToolCalls(callResult);
                if (!toolCalls || toolCalls.length === 0) {
                    break;
                }

                const toolCall = toolCalls[0];
                if (!toolCall.id) {
                    toolCall.id = uuidv4();
                }
                let args = {};
                try {
                    args = toolCall.function?.arguments ? JSON.parse(toolCall.function.arguments) : {};
                } catch (e) {
                    logger.warn("Failed to parse tool call arguments: %s", e.message);
                }
                const code = args.code || "";

                if (await isKilled(account.user, responseStream, body)) return;
                sendStatusMessage(responseStream, "Code interpreter is executing your code...");

                const executionResponse = await fetchRequest(
                    token,
                    {
                        codeInterpreterRecordId,
                        messages: [{ role: "user", content: code }],
                        file_keys: fileKeys,
                        file_names: fileKeyToName,
                        all_conversation_file_keys: body.allConversationFileKeys || [],
                        all_conversation_file_names: body.allConversationFileNames || {},
                        accountId: account.accountId || "general_account",
                        requestId: options.requestId
                    },
                    process.env.API_BASE_URL + "/assistant/chat/codeinterpreter"
                );

                let toolResultContent;

                if (executionResponse?.success && executionResponse.data) {
                    sendStatusMessage(responseStream, "Code execution complete — generating response...");
                    const { textContent, content, ...messageData } = executionResponse.data.data;
                    if (content && content.length > 0) {
                        allGeneratedFiles.push(...content);
                    }
                    const ciStateData = {
                        ...messageData,
                        content: allGeneratedFiles,
                        ...(executionResponse.sessionRenewed ? { sessionRenewed: true } : {})
                    };
                    sendStateEventToStream(responseStream, { codeInterpreter: ciStateData });
                    forceFlush(responseStream);
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

                executedAny = true;
                conversationMessages = [
                    ...conversationMessages,
                    {
                        role: "assistant",
                        content: callResult.content || "",
                        tool_calls: [{ id: toolCall.id, type: toolCall.type || "function", function: toolCall.function }]
                    },
                    {
                        role: "tool",
                        tool_call_id: toolCall.id,
                        content: toolResultContent
                    }
                ];
            }

            if (await isKilled(account.user, responseStream, body)) return;
            sendStatusMessage(responseStream, "Amplify Assistant is responding...", true);

            await assistantBase.handler(params, {
                ...body,
                messages: conversationMessages,
                max_tokens: options.maxTokens || 4000,
                dataSources: executedAny ? [] : body.dataSources,
                imageSources: executedAny ? [] : body.imageSources,
                tools: executedAny ? [CODE_INTERPRETER_TOOL_DEFINITION] : undefined,
                options: { ...options, disableDataSources: executedAny, disableReasoning: true, maxTokens: options.maxTokens || 4000 }
            }, executedAny ? [] : ds, responseStream);
        }
    };
};
