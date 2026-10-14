// Helpers that let the code interpreter loop recover from a bad script instead of
// giving up: errors raised by the sandbox (or a truncated / unparsable tool call) are
// handed back to the model as a tool result so it can fix its own code.

export const MAX_CODE_RETRIES = 3;
const MAX_ERROR_CHARS = 4000;

// The assistants API reports a Python failure as "Code execution error: <stderr>".
// Infrastructure problems (session expired, timeout, cancelled, invoke failed) use other
// messages and must NOT be retried by re-prompting the model.
export function isRetryableCodeError(message) {
    return typeof message === "string" && message.startsWith("Code execution error:");
}

// Keep the END of long tracebacks: the final lines name the actual exception.
export function truncateErrorForModel(message) {
    const text = String(message || "");
    return text.length > MAX_ERROR_CHARS ? "..." + text.slice(-MAX_ERROR_CHARS) : text;
}

export function parseToolCode(toolCall) {
    const raw = toolCall?.function?.arguments;
    if (!raw) return { code: "", error: "The execute_code call had no arguments." };
    let args;
    try {
        args = typeof raw === "string" ? JSON.parse(raw) : raw;
    } catch (e) {
        return {
            code: "",
            error:
                "The execute_code arguments were not valid JSON, most likely because the response was cut off " +
                "before the code finished. Write shorter, more compact code (fewer helper functions, less " +
                "repetition) and call execute_code again."
        };
    }
    const code = typeof args?.code === "string" ? args.code : "";
    if (!code.trim()) return { code: "", error: "The execute_code call contained no code." };
    return { code, error: null };
}

// Assistant tool call + tool result turn pair carrying an error back to the model.
// `safeArguments` replaces unparsable arguments so providers don't reject the history.
export function buildToolErrorTurn({ assistantContent, toolCall, errorMessage, safeArguments }) {
    return [
        {
            role: "assistant",
            content: assistantContent || "",
            tool_calls: [{
                id: toolCall.id,
                type: toolCall.type || "function",
                function: {
                    ...toolCall.function,
                    arguments: safeArguments !== undefined ? safeArguments : toolCall.function?.arguments
                }
            }]
        },
        {
            role: "tool",
            tool_call_id: toolCall.id,
            content: JSON.stringify({ error: truncateErrorForModel(errorMessage) })
        }
    ];
}

// Output files are detected by comparing sandbox paths before/after a run. A failed attempt may
// already have saved a file (e.g. it errored after prs.save()), so the fixed script must save
// under a NEW name or its file would be mistaken for one that already existed.
export const RETRY_FILE_HINT =
    "\n\nIf your script already saved an output file before failing, save the corrected file under a " +
    "different filename (for example add _v2) and do not rely on the earlier one.";

// Decides whether the fallback answer should tell the user to re-upload an attached file.
// Python tracebacks contain `File "...", line N`, so a bare "file" match would flag every
// script error; for sandbox code errors only genuine missing/unreadable-file errors count.
export function isFileRelatedError(msg) {
    const m = String(msg || "").toLowerCase();
    if (
        m.includes("filenotfounderror") ||
        m.includes("no such file or directory") ||
        m.includes("not authorized to access the referenced files")
    ) return true;
    if (m.startsWith("code execution error:")) return false;
    return m.includes("file");
}
