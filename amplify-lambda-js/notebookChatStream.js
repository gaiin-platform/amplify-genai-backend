// Copyright (c) 2024 Vanderbilt University
//
// Streaming proxy for Notebook chat and ask.
//
// The regular notebook API calls hop browser -> /api/requestOp -> API Gateway ->
// notebook_proxy Lambda -> Open Notebook, and every layer buffers the full
// response (and API Gateway caps at 29s). That makes token streaming impossible
// on that path, and it also kills the multi-step "ask" RAG pipeline, which runs
// several sequential model calls and routinely exceeds 29s. This function
// mirrors the main chat transport instead: a Lambda Function URL with invokeMode
// RESPONSE_STREAM, VPC-attached so it can reach the internal Open Notebook
// service, that forwards the request to Open Notebook's SSE endpoint and pipes
// the body straight back to the browser.
//
// One Function URL serves both transports; the upstream endpoint is chosen from
// the request path (allowlisted, so this stays a dedicated Open Notebook proxy
// rather than an open one):
//   - bare path        -> POST {OPEN_NOTEBOOK_INTERNAL_URL}/api/chat/execute/stream
//   - path ending /ask -> POST {OPEN_NOTEBOOK_INTERNAL_URL}/api/search/ask
//
// The browser calls this Function URL directly (NOTEBOOK_CHAT_STREAM_ENDPOINT in
// the frontend; ask appends "/ask"), passing the Cognito access token as a
// Bearer header. Auth is enforced by Open Notebook's JWT middleware upstream;
// this proxy forwards the Authorization header unchanged.

// 15 min cap, just under the Lambda 900s timeout, so we end cleanly rather than
// getting killed mid-stream.
const UPSTREAM_TIMEOUT_MS = 890_000;

// Open Notebook in dev sits behind a self-signed cert / IP host. Skip TLS
// verification there (mirrors the Python proxy's _ssl_context()). This is
// process-global, which is fine for a dedicated proxy that only talks to Open
// Notebook; it stays on (verified) in staging/prod.
if (
    (process.env.DISABLE_SSL_VERIFY || "").toLowerCase() === "true" ||
    process.env.STAGE === "dev"
) {
    process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
}

const sse = (obj) => `data: ${JSON.stringify(obj)}\n\n`;

// Map the incoming request path to an allowlisted Open Notebook SSE endpoint.
// Anything that isn't explicitly an "ask" request is treated as chat, preserving
// the original single-purpose behavior for the bare Function URL.
const upstreamPathFor = (event) => {
    const reqPath =
        event.rawPath || event.requestContext?.http?.path || event.path || "/";
    if (reqPath.replace(/\/+$/, "").endsWith("/ask")) {
        return "/api/search/ask";
    }
    return "/api/chat/execute/stream";
};

export const handler = awslambda.streamifyResponse(
    async (event, responseStream, _context) => {
        // Function URL CORS (url.cors in serverless.yml) adds the CORS response
        // headers; we only set the streaming content type here so we don't emit
        // a duplicate Access-Control-Allow-Origin.
        responseStream = awslambda.HttpResponseStream.from(responseStream, {
            statusCode: 200,
            headers: {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
            },
        });

        const fail = (message) => {
            try {
                responseStream.write(sse({ type: "error", message }));
            } catch (_) {
                /* stream may already be torn down */
            }
            responseStream.end();
        };

        const base = (process.env.OPEN_NOTEBOOK_INTERNAL_URL || "").replace(/\/$/, "");
        if (!base) {
            return fail("OPEN_NOTEBOOK_INTERNAL_URL is not configured.");
        }

        const headers = event.headers || {};
        const auth = headers.authorization || headers.Authorization || "";
        const body = event.isBase64Encoded
            ? Buffer.from(event.body || "", "base64").toString("utf-8")
            : event.body || "";

        const upstreamHeaders = {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            Authorization: auth,
        };
        // IP-based routing through the OpenShift router needs an explicit Host.
        if (process.env.OPEN_NOTEBOOK_HOST) {
            upstreamHeaders["Host"] = process.env.OPEN_NOTEBOOK_HOST;
        }

        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);

        const upstreamPath = upstreamPathFor(event);

        try {
            const upstream = await fetch(`${base}${upstreamPath}`, {
                method: "POST",
                headers: upstreamHeaders,
                body,
                signal: controller.signal,
            });

            if (!upstream.ok || !upstream.body) {
                let detail = "";
                try {
                    detail = (await upstream.text()).slice(0, 500);
                } catch (_) {
                    /* ignore */
                }
                return fail(`Upstream error ${upstream.status}. ${detail}`);
            }

            // Pipe the upstream SSE bytes straight through, unmodified.
            for await (const chunk of upstream.body) {
                responseStream.write(Buffer.from(chunk));
            }
            responseStream.end();
        } catch (e) {
            const msg =
                e?.name === "AbortError"
                    ? "Notebook stream timed out."
                    : `Notebook stream proxy error: ${e?.message || e}`;
            fail(msg);
        } finally {
            clearTimeout(timeout);
        }
    },
);
