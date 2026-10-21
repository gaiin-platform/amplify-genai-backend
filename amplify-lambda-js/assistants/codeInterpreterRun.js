// Copyright (c) 2024 Vanderbilt University
//
// The per-execution record the code interpreter streams to the browser so the UI can show
// what ran ("Calculated monthly revenue · 2 files created", with the code and its output
// underneath). Pure and import-free so it can be unit-tested without the LLM client.
//
// Sent as `codeInterpreter.runs: [record]`. The frontend's deepMerge appends array items
// de-duplicated by JSON equality, so each finished run is sent once and accumulates.

export const RUN_LIMITS = Object.freeze({
    code: 6000,
    stdout: 4000,
    error: 2000,
    description: 120,
});

/** Trim to `max` characters; report whether anything was cut. */
function clip(value, max) {
    const text = typeof value === "string" ? value : "";
    return text.length > max ? { text: text.slice(0, max), truncated: true } : { text, truncated: false };
}

/** Original filename for a generated file: `<user>/<id>-FN-<name>` -> `<name>`, else the key's basename. */
export function generatedFileName(file) {
    const fileKey = file?.values?.file_key || "";
    const fnMatch = fileKey.match(/-FN-([^/]+)$/);
    return fnMatch ? fnMatch[1] : fileKey.split("/").pop() || "generated_file";
}

/** The model's own one-line summary of the code, cleaned up; "" when absent or unusable. */
function cleanDescription(raw) {
    if (typeof raw !== "string") return "";
    const oneLine = raw.replace(/\s+/g, " ").trim().replace(/[.\s]+$/, "");
    return oneLine.slice(0, RUN_LIMITS.description);
}

/**
 * @param {object} run
 * @param {string} run.id          tool-call id (unique per execution)
 * @param {object} [run.args]      the parsed tool arguments ({ code, description })
 * @param {string} [run.stdout]    the sandbox's printed output
 * @param {string} [run.error]     the Python error / failure text, when the run failed
 * @param {Array}  [run.files]     files THIS execution produced (not the cumulative list)
 * @param {boolean} run.ok
 * @returns {object} a JSON-safe record; empty fields are omitted
 */
export function buildRunRecord({ id, args = {}, stdout = "", error = "", files = [], ok }) {
    const code = clip(args.code, RUN_LIMITS.code);
    const out = clip(stdout, RUN_LIMITS.stdout);
    const err = clip(error, RUN_LIMITS.error);
    const description = cleanDescription(args.description);
    const names = (Array.isArray(files) ? files : []).map(f => ({ name: generatedFileName(f), type: f?.type }));

    const truncated = {
        ...(code.truncated ? { code: true } : {}),
        ...(out.truncated ? { stdout: true } : {}),
        ...(err.truncated ? { error: true } : {}),
    };

    return {
        id,
        ok: ok === true,
        code: code.text,
        ...(description ? { description } : {}),
        ...(out.text.trim() ? { stdout: out.text } : {}),
        ...(err.text.trim() ? { error: err.text } : {}),
        ...(names.length ? { files: names } : {}),
        ...(Object.keys(truncated).length ? { truncated } : {}),
    };
}

/** A failure message from the execution API that is the user's code failing (vs. infrastructure). */
const CODE_FAILURE_PREFIX = /^Code execution (?:error|timed out)/i;

export const isCodeFailure = (message) => CODE_FAILURE_PREFIX.test(String(message || ""));

/** "Code execution error: Traceback…" -> "Traceback…" (the prefix is noise inside the error block). */
export const stripCodeFailurePrefix = (message) =>
    String(message || "").replace(/^Code execution error:\s*/i, "");
