import {Writable} from "stream";
import {TextDecoder} from "util";
import {newStatus} from "./status.js";

export class StreamResultCollector extends Writable {
    constructor(options) {
        super(options);
        // Initialize a TextDecoder to decode UTF-8 text by default
        this.decoder = new TextDecoder('utf-8');
        this.meta = {};
        this.result = {};
        this.transformers = [];
        this.outputStreams = [];
    }

    addOutputStream(outputStream){
        this.outputStreams.push(outputStream);
    }

    addTransformer(transformer){
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
                    if (this.meta.sources) {
                        delta.s = this.meta.sources[delta.s];
                    }
                    if (typeof delta.d !== "string") {
                        delta.d = JSON.stringify(delta.d);
                    }

                    const key = (delta.s) ? delta.s : "default";
                    const tokenKey = "__tokens_"+key;

                    if (!this.result[key]) {
                        this.result[key] = "";
                    }
                    if(!this.result[tokenKey]){
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
                }
            } catch (e) {
            }

        }
    }

    _write(chunk, encoding, callback) {
        // Convert the chunk (which must be a Buffer) to a string
        const textChunks = this.decoder.decode(chunk, {stream: true});

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
    resultStream.write(`data: ${JSON.stringify({s: "meta", m:"out_of_order"})}\n\n`);
}

export const forceFlush = (resultStream) => {
    sendStateEventToStream(resultStream, newStatus(
        {inProgress: false,
            message: " ".repeat(100000)}));
}

export const sendStatusEventToStream = (resultStream, statusEvent) => {
    resultStream.write(`data: ${JSON.stringify({s: "meta", st: statusEvent})}\n\n`);
}

export const sendStateEventToStream = (resultStream, state) => {
    resultStream.write(`data: ${JSON.stringify({s: "meta", state: state})}\n\n`);
}

export const sendToStream = (resultStream, src, data) => {
    if(!resultStream.writableEnded) {
        resultStream.write(`data: ${JSON.stringify({s: src, ...data})}\n\n`);
    }
}

export const sendDeltaToStream = (resultStream, src, delta) => {
    sendToStream(resultStream, src, {d: delta});
}

export const sendResultToStream = (resultStream, result) => {
    resultStream.write(`data: ${JSON.stringify({s: "result", d: result})}\n\n`);
}

export const endStream = (resultStream) => {
    resultStream.write(`data: ${JSON.stringify({s: "result", type:'end'})}\n\n`);
}

export const findResultKey = (result) => {
    const resultKey = Object.keys(result).find((k) => !k.startsWith("__tokens_"));
    return resultKey;
}

export const findResult = (result) => {
    const resultKey = findResultKey(result);
    return result[resultKey];
}


