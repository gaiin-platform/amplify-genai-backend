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
    "3. Trust the sandbox's output as the final answer. Once execute_code returns a successful " +
    "result for the user's request, present that result to the user as-is — do NOT decide on your " +
    "own to run additional code, generate extra plots, or perform further computation the user did " +
    "not ask for. Only call execute_code again if the user's request genuinely requires multiple " +
    "steps (e.g. they asked for several distinct things) or the previous call's result was an error.\n" +
    "4. Write a natural, helpful response based on what the code produced — explain findings, insights, or results in your own words.\n" +
    "5. Reference generated files by their filename — do NOT include download links.\n" +
    "6. Do not attach duplicate files with identical content.\n" +
    "7. Always include generated files in your response.";

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

            // Heuristic: does this failure look like it's caused by a problem with the
            // attached file itself (missing, unreadable, unauthorized, not found in the
            // sandbox, etc.) rather than an infrastructure/session issue? If so, the
            // fallback answer should proactively tell the user to re-upload the file
            // alongside their question, since simply re-asking won't fix a file problem.
            const isFileRelatedError = (msg) => {
                const m = String(msg || "").toLowerCase();
                return (
                    m.includes("file") ||
                    m.includes("filenotfounderror") ||
                    m.includes("not authorized to access the referenced files") ||
                    m.includes("no such file or directory")
                );
            };

            // On any code interpreter failure: show the error status, then let the LLM
            // answer the original question directly as a fallback.
            const fallbackToLLM = async (statusMsg) => {
                let fallbackMessages = messages;
                if (isFileRelatedError(statusMsg)) {
                    const reuploadInstruction = {
                        role: "system",
                        content:
                            "The code interpreter could not access an attached file for this request " +
                            "(it may be missing, unreadable, or the sandbox session was reset). " +
                            "Let the user know this in your response, and ask them to re-upload the file " +
                            "along with their question so it can be processed again."
                    };
                    fallbackMessages = [...messages, reuploadInstruction];
                }

                await assistantBase.handler(params, { ...body, messages: fallbackMessages, options: { ...options, maxTokens: options.maxTokens || 4000 } }, ds, responseStream);
            };

            let codeInterpreterRecordId = options.codeInterpreterRecordId || body.codeInterpreterRecordId || null;

            // File keys come from body.dataSources (ds) and the last message's per-message
            // attachments. `ds` has already passed through translateUserDataSourcesToHashDataSources,
            // which rewrites ds.id to a global RAG key when the file is indexed — the original
            // per-user key is preserved at metadata.userDataSourceId, so prefer that.
            const dsFileKeys = (ds || []).map(d => d.metadata?.userDataSourceId || d.id).filter(Boolean);
            const lastMsgFileKeys = (messages[messages.length - 1]?.data?.dataSources ?? []).map(d => d.metadata?.userDataSourceId || d.id).filter(Boolean);
            let fileKeys = [...new Set([...dsFileKeys, ...lastMsgFileKeys])];

            // Map each file key to its original filename — S3 keys are random UUIDs, not
            // the real filename, so the sandbox needs this to load files under a name the
            // LLM's code can reference (e.g. pd.read_csv('sales_data.csv')).
            const fileKeyToName = {};
            for (const d of [...(ds || []), ...(messages[messages.length - 1]?.data?.dataSources ?? [])]) {
                const key = d.metadata?.userDataSourceId || d.id;
                if (key && d.name) fileKeyToName[key] = d.name;
            }

            // On a follow-up turn with no new attachment, fileKeys/fileKeyToName above are
            // empty — the LLM then has no textual signal about the real filename (e.g.
            // "sales_data.csv") and can hallucinate a generic placeholder like 'data.csv',
            // causing FileNotFoundError even though the file is still in the sandbox.
            // Fall back to the most recently uploaded file across the WHOLE conversation
            // (router.js accumulates this in body.allConversationFileKeys/Names, in
            // message order, so the last entry is the most recent upload) so the file's
            // real name is always resent/known, every turn.
            if (fileKeys.length === 0 && Array.isArray(body.allConversationFileKeys) && body.allConversationFileKeys.length > 0) {
                const mostRecentKey = body.allConversationFileKeys[body.allConversationFileKeys.length - 1];
                if (mostRecentKey) {
                    fileKeys = [mostRecentKey];
                    const mostRecentName = body.allConversationFileNames?.[mostRecentKey];
                    if (mostRecentName) fileKeyToName[mostRecentKey] = mostRecentName;
                }
            }

            if (codeInterpreterRecordId === null) {
                if (await isKilled(account.user, responseStream, body)) return;

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

            // Tell the LLM the real filename(s) available in the sandbox, in text, so it
            // never has to guess a generic placeholder (e.g. 'data.csv') on a follow-up
            // turn where no file was freshly attached. Without this, the model has no
            // textual signal about the actual filename at all.
            const availableFileNames = [...new Set(Object.values(fileKeyToName).filter(Boolean))];
            const fileNamesHint = availableFileNames.length > 0
                ? `\n\nFile(s) available in the sandbox for this request: ${availableFileNames.join(", ")}. Use these exact filenames in your code.`
                : "";

            // Inject system prompt so the LLM uses the tool rather than narrating code.
            let llmMessages;
            if (messages.length > 0 && messages[0].role === "system") {
                llmMessages = [
                    { ...messages[0], content: messages[0].content + "\n\n" + CODE_INTERPRETER_SYSTEM_PROMPT + fileNamesHint },
                    ...messages.slice(1)
                ];
            } else {
                llmMessages = [{ role: "system", content: CODE_INTERPRETER_SYSTEM_PROMPT + fileNamesHint }, ...messages];
            }

            const MAX_TOOL_ITERATIONS = 5;
            let conversationMessages = llmMessages;
            let executedAny = false;
            let sessionWasRenewed = false;
            const allGeneratedFiles = [];

            for (let iteration = 0; iteration < MAX_TOOL_ITERATIONS; iteration++) {
                if (await isKilled(account.user, responseStream, body)) return;

                // Force tool use on the first turn so the model can never skip straight to
                // an empty/no-op response — it must call execute_code at least once. Later
                // turns (after it has already run code) are left on "auto" so it can stop
                // calling the tool and produce its final text answer.
                const toolChoice = iteration === 0 ? "required" : "auto";

                let callResult;
                try {
                    callResult = await callUnifiedLLM(
                        { ...params, options: { ...params.options, model } },
                        conversationMessages,
                        null,
                        {
                            tools: [CODE_INTERPRETER_TOOL_DEFINITION],
                            tool_choice: toolChoice,
                            disableReasoning: true,
                            temperature: options.temperature,
                            max_tokens: options.maxTokens || 4000
                        }
                    );
                } catch (err) {
                    logger.error("Code interpreter LLM call failed: %s", err.message);
                    await fallbackToLLM(err.message);
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
                sendStatusMessage(responseStream, "Executing code...");

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
                    if (executionResponse.sessionRenewed) {
                        sessionWasRenewed = true;
                    }
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

            // If the session was renewed this turn, make sure the final NL answer itself
            // mentions it — the status/state events above are ephemeral stream signals the
            // frontend might not render or might miss on a fast turn, but text appended to
            // conversationMessages here is guaranteed to reach the persisted transcript the
            // user actually reads.
            let finalMessages = conversationMessages;
            if (sessionWasRenewed) {
                finalMessages = [
                    ...conversationMessages,
                    {
                        role: "system",
                        content:
                            "Note: the code interpreter session had expired since your last message in this " +
                            "conversation. A new session was automatically created and your previously uploaded " +
                            "file(s) were reloaded into it. Begin your response with a brief note telling the user " +
                            "this happened before answering their question."
                    }
                ];
            }

            // Once code has already executed, this final call is ONLY meant to produce the
            // natural-language answer from the results already gathered above — it must NOT
            // re-offer execute_code. assistantBase.handler's plain callUnifiedLLM path has no
            // tool-execution loop, so if the model responds with a toolUse block instead of
            // text (which it may do here since nothing constrains it away from the tool),
            // the stream transform emits zero text deltas and the user sees a blank message,
            // even though the sandbox already computed the real answer. Dropping `tools`
            // here removes that possibility entirely.
            await assistantBase.handler(params, {
                ...body,
                messages: finalMessages,
                max_tokens: options.maxTokens || 4000,
                dataSources: executedAny ? [] : body.dataSources,
                imageSources: executedAny ? [] : body.imageSources,
                tools: undefined,
                options: { ...options, disableDataSources: executedAny, disableReasoning: true, maxTokens: options.maxTokens || 4000 }
            }, executedAny ? [] : ds, responseStream);
        }
    };
};
