/**
 * Read a JSON-RPC message out of a Server-Sent Events body.
 *
 * Streamable-HTTP MCP servers may answer a POST with `text/event-stream` instead of plain JSON:
 *
 *     event: message
 *     data: {"jsonrpc":"2.0","id":"req_1","result":{...}}
 *
 * Pure and import-free so it can be unit tested (see sseJsonRpc.test.js).
 */

/** Split an SSE body into events; each event is the joined text of its `data:` lines. */
export function sseDataEvents(text) {
    if (typeof text !== 'string' || text === '') return [];
    const events = [];
    let data = [];
    const flush = () => {
        if (data.length) events.push(data.join('\n'));
        data = [];
    };
    for (const rawLine of text.split(/\r\n|\n|\r/)) {
        if (rawLine === '') {
            flush(); // a blank line ends the event
        } else if (rawLine.startsWith(':')) {
            continue; // comment / keep-alive
        } else if (rawLine.startsWith('data:')) {
            const value = rawLine.slice(5);
            data.push(value.startsWith(' ') ? value.slice(1) : value);
        }
        // `event:`, `id:` and `retry:` lines carry nothing we need.
    }
    flush();
    return events;
}

/**
 * The JSON-RPC response for `requestId` in an SSE body. A server may stream notifications or
 * requests first, so prefer the message whose `id` matches, then any response (`result`/`error`),
 * and ignore events that are not JSON. Returns null when there is none.
 */
export function parseSseJsonRpc(text, requestId) {
    let firstResponse = null;
    for (const raw of sseDataEvents(text)) {
        let msg;
        try {
            msg = JSON.parse(raw);
        } catch {
            continue;
        }
        if (!msg || typeof msg !== 'object') continue;
        const isResponse = 'result' in msg || 'error' in msg;
        if (isResponse && requestId !== undefined && msg.id === requestId) return msg;
        if (isResponse && firstResponse === null) firstResponse = msg;
    }
    return firstResponse;
}
