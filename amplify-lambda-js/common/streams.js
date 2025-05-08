//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { Writable } from "stream";
import { TextDecoder } from "util";
import { newStatus } from "./status.js";

export class TraceStream extends Writable {
    constructor(options, targetStream) {
        super(options);
        // Initialize a TextDecoder to decode UTF-8 text by default
        this.decoder = new TextDecoder('utf-8');
        this.trace = "";
        this.targetStream = targetStream;
    }

    handleChunk(textChunk) {
        try {
            if (textChunk.trim().length > 0) {
                const json = textChunk.slice(6);
                try {

                    const delta = JSON.parse(json);

                    if (delta.s === "meta" && delta.st) {
                        // We do this to remove the hacky flushes to make
                        // AWS Lambda streaming work
                        if (delta.message && delta.message.trim().length > 0 ||
                            delta.summary && delta.summary.trim().length > 0) {
                            this.trace += textChunk + "\n\n";
                        }
                    }
                    else {
                        this.trace += textChunk + "\n\n";
                    }
                } catch (e) {
                    this.trace += textChunk + "\n\n";
                }
            }
        }
        catch (e) {
            console.log(e);
        }
    }

    _write(chunk, encoding, callback) {
        // Convert the chunk (which must be a Buffer) to a string
        const textChunks = this.decoder.decode(chunk, { stream: true });

        for (const textChunk of textChunks.split('\n')) {
            this.handleChunk(textChunk);
        }

        // Indicate that the chunk has been processed successfully
        this.targetStream.write(chunk);

        callback();
    }

    _final(callback) {
        // Flush any remaining text from the TextDecoder, but since
        // we've been streaming, there shouldn't be anything left to flush unless the stream ended unexpectedly.
        const remaining = this.decoder.decode();
        if (remaining) {
            this.handleChunk(remaining);
        }

        this.targetStream.end();

        if (typeof this.targetStream._final === 'function') {
            this.targetStream._final((error) => {
                // Handle any errors in the _final method of the targetStream
                if (error) {
                    callback(error);
                } else {
                    callback();
                }
            });
        } else {
            callback();
        }
    }

}


export class StatusOutputStream extends Writable {
    constructor(options, statusStream, status) {
        super(options);
        // Initialize a TextDecoder to decode UTF-8 text by default
        this.decoder = new TextDecoder('utf-8');
        this.transformers = [];
        this.outputStreams = [];
        this.statusStream = statusStream;
        this.status = status;
        this.message = "";
    }

    addOutputStream(outputStream) {
        this.outputStreams.push(outputStream);
    }

    addTransformer(transformer) {
        this.transformers.push(transformer);
    }

    handleChunk(textChunk) {
        if (textChunk.trim().length > 0) {

            const json = textChunk.slice(6);
            try {

                const delta = JSON.parse(json);

                if (delta && delta.s && delta.s === "meta" && delta.d) {
                    this.meta = delta.d;
                } else if (delta && delta.d) {
                    if (this.meta && this.meta.sources) {
                        delta.s = this.meta.sources[delta.s];
                    }
                    if (typeof delta.d !== "string") {
                        delta.d = JSON.stringify(delta.d);
                    }

                    const value = this.transformers.reduce((acc, transformer) => {
                        return transformer(acc);
                    }, delta.d);

                    if (delta.d) {
                        this.status.message += delta.d;
                        sendStatusEventToStream(this.statusStream, this.status);
                    }

                    this.outputStreams.forEach((outputStream) => {
                        outputStream.write(textChunk + "\n\n");
                    });
                }
            } catch (e) {
                console.log(e);
            }

        }
    }

    _write(chunk, encoding, callback) {
        // Convert the chunk (which must be a Buffer) to a string
        const textChunks = this.decoder.decode(chunk, { stream: true });

        for (const textChunk of textChunks.split('\n')) {
            this.handleChunk(textChunk);
        }

        // Indicate that the chunk has been processed successfully
        callback();
    }

    _final(callback) {
        // Flush any remaining text from the TextDecoder, but since
        // we've been streaming, there shouldn't be anything left to flush unless the stream ended unexpectedly.
        const remaining = this.decoder.decode();
        if (remaining) {
            this.handleChunk(remaining);
        }

        callback();
    }
}

export class StreamResultCollector extends Writable {
    constructor(options) {
        super(options);
        // Initialize a TextDecoder to decode UTF-8 text by default
        this.decoder = new TextDecoder('utf-8');
        this.meta = {};
        this.result = {};
        this.transformers = [];
        this.outputStreams = [];
        this.statusStreams = [];
        this.fullResponse = '';
    }

    addOutputStream(outputStream) {
        this.outputStreams.push(outputStream);
    }

    addTransformer(transformer) {
        this.transformers.push(transformer);
    }

    addStatusStream(statusStream) {
        this.statusStreams.push(statusStream);
    }

    handleChunk(textChunk) {
        if (textChunk.trim().length > 0) {

            const json = textChunk.slice(6);
            try {

                const delta = JSON.parse(json);

                // If set, we send the status to the output streams directly as well
                if (delta && delta.s === "meta" && delta.st && this.statusStreams.length > 0) {
                    for (const sst of this.statusStreams) {
                        sst.write(textChunk + "\n\n");
                    }
                }

                if (delta && delta.s && delta.s === "meta" && delta.d) {
                    this.meta = delta.d;
                }
                else if (delta && delta.d) {
                    if (this.meta.sources) {
                        delta.s = this.meta.sources[delta.s];
                    }

                    if (typeof delta.d === "string") {
                        this.fullResponse += delta.d;
                    }

                    if (typeof delta.d !== "string") {
                        delta.d = JSON.stringify(delta.d);
                    }

                    const key = (delta.s) ? delta.s : "default";
                    const tokenKey = "__tokens_" + key;

                    if (!this.result[key]) {
                        this.result[key] = "";
                    }
                    if (!this.result[tokenKey]) {
                        this.result[tokenKey] = 0;
                    }

                    const value = this.transformers.reduce((acc, transformer) => {
                        return transformer(acc);
                    }, delta.d);

                    this.result[key] += value;
                    this.result[tokenKey] += 1;

                    this.outputStreams.forEach((outputStream) => {
                        outputStream.write(textChunk + "\n\n");
                    });
                } else if (delta.s === "meta") {
                    this.outputStreams.forEach((outputStream) => {
                        outputStream.write(textChunk + "\n\n");
                    });
                }
            } catch (e) {
            }

        }
    }

    getFullResponse() {
        return this.fullResponse;
    }

    _write(chunk, encoding, callback) {
        // Convert the chunk (which must be a Buffer) to a string
        const textChunks = this.decoder.decode(chunk, { stream: true });

        for (const textChunk of textChunks.split('\n')) {
            this.handleChunk(textChunk);
        }

        // Indicate that the chunk has been processed successfully
        callback();
    }

    _final(callback) {
        // Flush any remaining text from the TextDecoder, but since
        // we've been streaming, there shouldn't be anything left to flush unless the stream ended unexpectedly.
        const remaining = this.decoder.decode();
        if (remaining) {
            this.handleChunk(remaining);
        }
        callback();
    }
}

export const sendOutOfOrderModeEventToStream = (resultStream) => {
    resultStream.write(`data: ${JSON.stringify({ s: "meta", m: "out_of_order" })}\n\n`);
}

export const forceFlush = (resultStream) => {
    sendStateEventToStream(resultStream, newStatus(
        {
            inProgress: false,
            message: " ".repeat(100000)
        }));
}

export const sendStatusEventToStream = (resultStream, statusEvent) => {
    resultStream.write(`data: ${JSON.stringify({ s: "meta", st: statusEvent })}\n\n`);
}

export const sendStateEventToStream = (resultStream, state) => {
    resultStream.write(`data: ${JSON.stringify({ s: "meta", state: state })}\n\n`);
}

export const sendToStream = (resultStream, src, data) => {
    if (!resultStream.writableEnded) {
        resultStream.write(`data: ${JSON.stringify({ s: src, ...data })}\n\n`);
    }
}

export const sendDirectToStream = (resultStream, data) => {
    if (!resultStream.writableEnded) {
        resultStream.write(data);
    }
}

export const sendDeltaToStream = (resultStream, src, delta) => {
    sendToStream(resultStream, src, { d: delta });
}

export const sendResultToStream = (resultStream, result) => {
    resultStream.write(`data: ${JSON.stringify({ s: "result", d: result })}\n\n`);
}

export const endStream = (resultStream) => {
    resultStream.write(`data: ${JSON.stringify({ s: "result", type: 'end' })}\n\n`);
}

export const findResultKey = (result) => {
    const resultKey = Object.keys(result).find((k) => !k.startsWith("__tokens_"));
    return resultKey;
}

export const findResult = (result) => {
    const resultKey = findResultKey(result);
    return result[resultKey];
}


export const sendErrorMessage = (writable, statusCode, code=null) => {

    if (!writable || writable.writableEnded) {
        console.log('Stream already ended, cannot send error message');
        return;
    }

    console.log("-- Error Message Response Status -- ", statusCode);
    console.log("-- Error Message Response Code -- ", code);

    const waitMessage = " Please try another model or wait a few minutes before trying again.";
    let errorMessage = "Error retrieving response. Please try again."

    if (code === 'content_filter') {
        errorMessage = "Content Filter: The response was blocked due to the content of the request.";
    } else if (statusCode === 429) {
        errorMessage = "Too Many Requests: You have sent too many requests in a given amount of time to this model." + waitMessage;
    } else if ([408, 503, 504].includes(statusCode)) {
        errorMessage = "Request Timed Out: We did not receive a timely response from the model providers server." + waitMessage;
    } else if (statusCode === 413) {
        errorMessage = "Request Entity Too Large: The request body is too large. Please try again with a smaller request.";
    }
    
    try {
        sendDeltaToStream(writable, "answer", {delta: {text: errorMessage}});
        if (!writable.writableEnded) {
            writable.end();
        }
    } catch (err) {
        console.error('Error while sending error message:', err);
    }

}
