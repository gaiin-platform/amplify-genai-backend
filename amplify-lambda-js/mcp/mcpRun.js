// Copyright (c) 2024 Vanderbilt University
//
// The per-call record the server-side MCP path streams to the browser so the UI can show
// what a tool was sent and what came back ("Search issues (GitHub)", expandable). Pure and
// import-free so it can be unit-tested without the MCP client.
//
// Sent as `mcpRuns: [record]`. The frontend's deepMerge appends array items de-duplicated by
// JSON equality, so each finished call is sent once and accumulates.
//
// Everything here is persisted on the message, so it is capped and never carries binary data.

export const MCP_RUN_LIMITS = Object.freeze({
    tool: 120,
    serverName: 80,
    serverId: 80,
    argKeys: 20,
    argKey: 80,
    argValue: 500,
    result: 4000,
    error: 2000,
    media: 5,
    mediaField: 200,
});

/** Trim to `max` characters; report whether anything was cut. */
function clip(value, max) {
    const text = typeof value === "string" ? value : "";
    return text.length > max ? { text: text.slice(0, max), truncated: true } : { text, truncated: false };
}

/** A short plain string, or "" when absent/unusable. */
function shortText(value, max) {
    return typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, max) : "";
}

/**
 * The call arguments as a flat, JSON-safe object: primitives stay as they are, strings are
 * clipped, anything nested becomes a clipped JSON string. Bounded in keys and value size.
 * @returns {{ args: object, truncated: boolean }}
 */
export function clipArgs(args) {
    const out = {};
    let truncated = false;
    if (!args || typeof args !== "object" || Array.isArray(args)) return { args: out, truncated };

    const entries = Object.entries(args);
    if (entries.length > MCP_RUN_LIMITS.argKeys) truncated = true;

    for (const [rawKey, value] of entries.slice(0, MCP_RUN_LIMITS.argKeys)) {
        const key = rawKey.slice(0, MCP_RUN_LIMITS.argKey);
        if (value === null || typeof value === "number" || typeof value === "boolean") {
            out[key] = Number.isFinite(value) || typeof value !== "number" ? value : null;
            continue;
        }
        let text;
        if (typeof value === "string") {
            text = value;
        } else {
            try {
                text = JSON.stringify(value);
            } catch {
                text = undefined;
            }
        }
        if (text === undefined) continue; // undefined / functions / cycles: nothing to show
        const c = clip(text, MCP_RUN_LIMITS.argValue);
        if (c.truncated) truncated = true;
        out[key] = c.text;
    }
    return { args: out, truncated };
}

/**
 * Non-text parts of the tool result described WITHOUT their payload: an image is its mime
 * type, a resource its uri. The base64 `data` of an image is never read.
 */
export function describeMedia(rawContent) {
    if (!Array.isArray(rawContent)) return [];
    const media = [];
    for (const item of rawContent) {
        if (!item || typeof item !== "object" || item.type === "text") continue;
        if (media.length >= MCP_RUN_LIMITS.media) break;
        const mimeType = shortText(item.mimeType, MCP_RUN_LIMITS.mediaField);
        if (item.type === "image") {
            media.push({ kind: "image", ...(mimeType ? { mimeType } : {}) });
        } else if (item.type === "resource") {
            const uri = shortText(item.uri ?? item.resource?.uri, MCP_RUN_LIMITS.mediaField);
            media.push({ kind: "resource", ...(uri ? { uri } : {}), ...(mimeType ? { mimeType } : {}) });
        } else {
            media.push({ kind: "other" });
        }
    }
    return media;
}

/**
 * @param {object} run
 * @param {string} run.id            tool-call id (unique per call)
 * @param {string} run.tool          the server's own tool name (no `mcp_<server>_` prefix)
 * @param {string} [run.serverName]  display name of the MCP server
 * @param {string} [run.serverId]
 * @param {object} [run.args]        the parsed tool arguments
 * @param {string} [run.result]      the tool's text output
 * @param {string} [run.error]       failure text, when the call failed
 * @param {Array}  [run.rawContent]  the MCP `content` items (only read for their type/mime/uri)
 * @param {number} [run.ms]          how long the call took
 * @param {boolean} run.ok
 * @returns {object} a JSON-safe record; empty fields are omitted
 */
export function buildMcpRunRecord({ id, tool, serverName, serverId, args, result = "", error = "", rawContent, ms, ok }) {
    const clippedArgs = clipArgs(args);
    const res = clip(result, MCP_RUN_LIMITS.result);
    const err = clip(error, MCP_RUN_LIMITS.error);
    const media = describeMedia(rawContent);
    const server = shortText(serverName, MCP_RUN_LIMITS.serverName);
    const sid = shortText(serverId, MCP_RUN_LIMITS.serverId);

    const truncated = {
        ...(clippedArgs.truncated ? { args: true } : {}),
        ...(res.truncated ? { result: true } : {}),
        ...(err.truncated ? { error: true } : {}),
    };

    return {
        id: String(id ?? ""),
        ok: ok === true,
        tool: shortText(tool, MCP_RUN_LIMITS.tool),
        ...(server ? { serverName: server } : {}),
        ...(sid ? { serverId: sid } : {}),
        ...(Object.keys(clippedArgs.args).length ? { args: clippedArgs.args } : {}),
        ...(res.text.trim() ? { result: res.text } : {}),
        ...(err.text.trim() ? { error: err.text } : {}),
        ...(media.length ? { media } : {}),
        ...(Number.isFinite(ms) && ms >= 0 ? { ms: Math.round(ms) } : {}),
        ...(Object.keys(truncated).length ? { truncated } : {}),
    };
}
