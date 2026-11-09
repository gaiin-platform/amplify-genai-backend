/**
 * Pull diagnosable details out of a Bedrock SDK error.
 *
 * Two places can hold the raw HTTP response:
 *  1. `error.$response` - set by the SDK as a hidden (non-enumerable) property, so
 *     JSON.stringify(error) and default logging never include it.
 *  2. `error.rawResponseStatus` / `error.rawResponseHeaders` - copied on by
 *     `captureRawResponseMiddleware` below. Needed because for stream exceptions the SDK throws a
 *     plain object and its retry layer rewraps it with `Object.assign(new Error(), thrown)`,
 *     which copies ONLY enumerable properties - silently dropping `$response`.
 * Streaming bodies are NOT consumed - only reported as "[stream]".
 * Header values are the service's own response headers (request ids, content-type, error type);
 * request headers / credentials are never touched.
 */
export function extractBedrockResponseDiagnostics(error) {
    const response = error?.$response;
    const statusCode = response?.statusCode ?? error?.rawResponseStatus;
    const headers = response?.headers ?? error?.rawResponseHeaders;
    if (!response && statusCode === undefined && headers === undefined) {
        return { hasResponse: false, statusCode: 'N/A', headers: 'N/A', bodyPreview: 'N/A' };
    }
    let bodyPreview = 'N/A';
    try {
        const body = response?.body;
        if (typeof body === 'string') bodyPreview = body.substring(0, 500);
        else if (body instanceof Uint8Array) bodyPreview = Buffer.from(body).toString('utf8', 0, 500);
        else if (body && typeof body.pipe === 'function') bodyPreview = '[stream - not consumed]';
        else if (body !== undefined && body !== null) bodyPreview = JSON.stringify(body).substring(0, 500);
    } catch (e) {
        bodyPreview = 'Unable to read response body';
    }
    return {
        hasResponse: true,
        statusCode: statusCode ?? 'N/A',
        headers: headers ?? 'N/A',
        bodyPreview,
    };
}

/**
 * AWS SDK middleware (deserialize step, high priority = just OUTSIDE the deserializer) that copies
 * the hidden `$response` status/headers onto the error as ENUMERABLE properties so they survive
 * the SDK's retry-layer rewrap. Never alters or swallows the error.
 */
export const captureRawResponseMiddleware = (next) => async (args) => {
    try {
        return await next(args);
    } catch (error) {
        try {
            const response = error?.$response;
            if (response && error && typeof error === 'object') {
                error.rawResponseStatus = response.statusCode;
                error.rawResponseHeaders = response.headers;
            }
        } catch (_) { /* diagnostics must never mask the real error */ }
        throw error;
    }
};

export const captureRawResponseMiddlewareOptions = {
    step: 'deserialize',
    priority: 'high',
    name: 'captureRawResponseOnError',
};
