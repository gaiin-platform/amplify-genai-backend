//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {Writable} from "stream";
import {TextDecoder} from "util";

export class ConsoleWritableStream extends Writable {
    constructor(outputDeltas, options) {
        super(options);
        // Initialize a TextDecoder to decode UTF-8 text by default
        this.outputDeltas = outputDeltas;
        this.decoder = new TextDecoder('utf-8');
        this.meta = {};
    }

    _write(chunk, encoding, callback) {
        // Convert the chunk (which must be a Buffer) to a string
        const textChunks = this.decoder.decode(chunk, { stream: true });

        for(const textChunk of textChunks.split('\n')) {

            if(textChunk.trim().length > 0) {

                if (this.outputDeltas) {
                    const json = textChunk.slice(6);
                    try {

                        const delta = JSON.parse(json);

                        if (delta && delta.s && delta.s === "meta" && delta.d) {
                            this.meta = delta.d;
                            console.log("Meta: " + JSON.stringify(this.meta));
                        }
                        else if (delta && delta.s && delta.s === "meta" && delta.st) {
                            console.log("Meta Status: " + JSON.stringify(delta.st));
                        }
                        else if (delta && delta.d) {
                            if(this.meta.sources){
                                delta.s = this.meta.sources[delta.s];
                            }
                            if(typeof delta.d !== "string"){
                                delta.d = JSON.stringify(delta.d);
                            }
                            console.log(delta.s +":"+delta.d);
                        } else if (delta && delta.type && delta.type === 'end') {
                            console.log(`\n--------- End ${delta.s} ------------`)
                        } else if (!delta) {
                            console.log("Invalid event>>>" + textChunk);
                        }

                    } catch (e) {
                        console.log("Error processing event: " + e, json);
                    }
                } else {
                    console.log(textChunk);
                }
            }
        }

        // Indicate that the chunk has been processed successfully
        callback();
    }

    _final(callback) {
        // Flush any remaining text from the TextDecoder, but since
        // we've been streaming, there shouldn't be anything left to flush unless the stream ended unexpectedly.
        const remaining = this.decoder.decode();
        if (remaining) {
            console.log(remaining);
        }
        callback();
    }
}