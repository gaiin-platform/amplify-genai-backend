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
    "Always use this tool to run code rather than showing hypothetical output. " +
    "Rules:\n" +
    "1. Do NOT include the Python code or raw sandbox output in your response.\n" +
    "2. Write a natural, helpful response based on what the code produced — explain findings, insights, or results in your own words.\n" +
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
// Note: only metadata (name/size/type) is included here, never the base64 file data itself —
// that would needlessly bloat the LLM's context with content it doesn't need to reason about.
function buildToolResultContent(responseData) {
    const inner = responseData?.data?.data ?? {};
    const result = { output: inner.textContent || "" };
    if (inner.content && inner.content.length > 0) {
        result.files = inner.content.map(f => ({
            type: f.type,
            file_name: f.values?.file_name || "generated_file",
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

            let codeInterpreterRecordId = options.codeInterpreterRecordId || body.codeInterpreterRecordId || null;

            // Create session eagerly on every first message (no existing session).
            // If files are attached they are loaded into the sandbox now so the LLM
            // can reference them by filename. If no files, we still create the session
            // up front so it exists before the first LLM call.
            //
            // File keys come from two sources:
            //   1. `ds` — the resolved top-level body.dataSources (files attached to this request)
            //   2. The last message's data.dataSources — per-message attachments
            // We union both to ensure nothing is missed.
            //
            // IMPORTANT: `ds` has already passed through resolveDataSources() ->
            // translateUserDataSourcesToHashDataSources(), which REWRITES ds.id to the
            // global, deduplicated RAG text-location key (e.g. "s3://bucket/global/<hash>")
            // whenever the file has already been indexed for RAG. That global key does not
            // contain the user's email and points at extracted RAG text, not the original
            // file bytes — the Python backend's file_keys_to_s3_bytes/create_new_session
            // both expect the ORIGINAL per-user key (e.g. "user@email.com/2024-05-08/hash.ext")
            // and reject anything else as unauthorized. When translation happens, the
            // original id is preserved on metadata.userDataSourceId, so prefer that.
            const dsFileKeys = (ds || []).map(d => d.metadata?.userDataSourceId || d.id).filter(Boolean);
            const lastMsgFileKeys = (messages[messages.length - 1]?.data?.dataSources ?? []).map(d => d.metadata?.userDataSourceId || d.id).filter(Boolean);
            const allCurrentFileKeys = [...new Set([...dsFileKeys, ...lastMsgFileKeys])];
            const fileKeys = allCurrentFileKeys;

            // TEMP DIAGNOSTIC (see investigation notes): dumps the raw dataSources arrays and
            // the resolved fileKeys so we can confirm whether top-level image/video dataSources
            // (filtered out of `ds` upstream by resolveDataSources' isImage/isVideo split) are
            // ever silently missing from what gets sent to the code interpreter, and whether
            // metadata.userDataSourceId is actually present when RAG hash-translation kicks in.
            // Remove once the file-handling issue is diagnosed.
            logger.info("codeInterpreter file key resolution diagnostic: %s", JSON.stringify({
                account: account.user,
                ds_raw: (ds || []).map(d => ({ id: d.id, type: d.type, metadata: d.metadata })),
                lastMsgDataSources_raw: (messages[messages.length - 1]?.data?.dataSources ?? []).map(d => ({ id: d.id, type: d.type, metadata: d.metadata })),
                imageSources: (body.imageSources || []).map(d => ({ id: d.id, type: d.type })),
                videoSources: (body.videoSources || []).map(d => ({ id: d.id, type: d.type })),
                dsFileKeys,
                lastMsgFileKeys,
                fileKeys
            }));

            if (codeInterpreterRecordId === null) {
                if (await isKilled(account.user, responseStream, body)) return;

                const statusMsg = fileKeys.length > 0
                    ? "Preparing code interpreter session with your files..."
                    : "Starting code interpreter session...";
                sendStatusMessage(responseStream, statusMsg);

                const createResponse = await fetchRequest(
                    token, { dataSources: fileKeys },
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

            // First LLM call: tool selection. keepStreamOpen so we can continue after.
            if (await isKilled(account.user, responseStream, body)) return;
            sendStatusMessage(responseStream, "Code interpreter is analysing your request...");

            let firstCallResult;
            try {
                // Pass null stream — the first call captures tool arguments only and
                // must NOT stream raw LLM output (including tool call JSON) to the user.
                firstCallResult = await callUnifiedLLM(
                    { ...params, options: { ...params.options, model } },
                    llmMessages,
                    null,
                    {
                        tools: [CODE_INTERPRETER_TOOL_DEFINITION],
                        tool_choice: "required",
                        disableReasoning: true,
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
            // Ensure tool call has a stable id — Bedrock may omit toolUseId in some events.
            if (!toolCall.id) {
                toolCall.id = uuidv4();
                logger.warn("toolCall.id was missing — generated fallback id: %s", toolCall.id);
            }
            let args = {};
            try {
                args = toolCall.function?.arguments ? JSON.parse(toolCall.function.arguments) : {};
            } catch (e) {
                logger.warn("Failed to parse tool call arguments: %s", e.message);
            }
            const code = args.code || "";

            // Execute the LLM-written code via the Python lambda.
            if (await isKilled(account.user, responseStream, body)) return;
            sendStatusMessage(responseStream, "Code interpreter is executing your code...");

            const executionResponse = await fetchRequest(
                token,
                {
                    codeInterpreterRecordId,
                    messages: [{ role: "user", content: code }],
                    file_keys: fileKeys,
                    // All file keys collected from the full conversation history before
                    // smart-messages may have pruned body.messages.  Used by the Python
                    // backend's renew_session so it can reload every file ever uploaded
                    // into a freshly created replacement session.
                    all_conversation_file_keys: body.allConversationFileKeys || [],
                    accountId: account.accountId || "general_account",
                    requestId: options.requestId
                },
                process.env.API_BASE_URL + "/assistant/chat/codeinterpreter"
            );

            let toolResultContent;
            let ciStateData = null;

            if (executionResponse?.success && executionResponse.data) {
                sendStatusMessage(responseStream, "Code execution complete — generating response...");
                const { textContent, ...messageData } = executionResponse.data.data;
                // sessionRenewed is returned at the top level of the response (not nested under data.data)
                ciStateData = { ...messageData, ...(executionResponse.sessionRenewed ? { sessionRenewed: true } : {}) };
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
                // Force flush so the file state reaches the frontend before the second LLM call
                // starts streaming text (which the frontend needs to render the files block).
                forceFlush(responseStream);
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
                tools: [CODE_INTERPRETER_TOOL_DEFINITION],
                options: { ...options, disableDataSources: true, disableReasoning: true, maxTokens: options.maxTokens || 4000 }
            }, [], responseStream);
        }
    };
};
