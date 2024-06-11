//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { Writable } from 'stream';
import {getLogger} from "./logging.js";

const logger = getLogger("multiplexer");


export class StreamMultiplexer {
    constructor(outputStream) {
        this.outputStream = outputStream;
        this.sources = [];
        this.sourceStates = {};
        this.endPromises = []; // Store promises related to ending of sources
    }

    addSource(inputStream, src, processor) {
        this.sourceStates[src] = { state: 'active', endPromiseResolve: null };

        // Create a promise for the end of each source and save its resolve function
        const endPromise = new Promise((resolve) => {
            this.sourceStates[src].endPromiseResolve = resolve;
        });
        this.endPromises.push(endPromise);

        const source = {
            inputStream: inputStream,
            src: src,
            onData: (chunks) => {

                //logger.debug("Chunks: ", chunks.toString());

                for(let data of chunks.toString().split('\n')) {

                    if(data.trim().length === 0) {
                        continue;
                    }

                    if (data.startsWith('data:')) {
                        data = data.slice(6);

                        if (data.trim() === '[DONE]') {
                            // When the source is done, we consider it finished
                            //this.sourceStates[src].endPromiseResolve();
                            return;
                        }

                        try {
                            //console.log("Parsing", data);

                            let eventObj = JSON.parse(data);

                            let transformed = eventObj;

                            if (processor) {
                                transformed = processor(eventObj);
                            }

                            if (transformed) {
                                transformed.s = src;
                                //logger.debug("Sending event: ", transformed);
                                this.outputStream.write("data: "+JSON.stringify(transformed) + '\n\n');
                            }
                        } catch (err) {
                            // Handle any parsing error or write error here
                            // For now, we log it
                            logger.error('Error handling input source data:', "'" + data + "'");
                        }
                    }
                }
            },
            onError: (err) => {
                this.sourceStates[src].state = 'error';

                //logger.error('Error from source', src, ':', err);
                let errEvent = {s: src, type: 'error'};
                this.outputStream.write("data: " + JSON.stringify(errEvent) + '\n\n');

                // When an error occurs, we consider the source finished
                this.sourceStates[src].endPromiseResolve();
            },
            onEnd: () => {
                this.sourceStates[src].state = 'end';

                logger.debug('Source stream ended:', src);
                try {
                    let endEvent = {s: src, type: 'end'};
                    this.outputStream.write("data: " + JSON.stringify(endEvent) + '\n\n');
                } catch (err) {
                }

                // Resolve the promise associated with the source ending
                this.sourceStates[src].endPromiseResolve();
            },
        };

        inputStream.on('data', source.onData);
        inputStream.on('error', source.onError);
        inputStream.on('end', source.onEnd);

        this.sources.push(source);

        // Return a function to disconnect this particular source
        return () => this.removeSource(src);
    }

    removeSource(src) {
        const sourceIndex = this.sources.findIndex(s => s.src === src);
        if (sourceIndex >= 0) {
            const source = this.sources[sourceIndex];
            source.inputStream.removeListener('data', source.onData);
            source.inputStream.removeListener('error', source.onError);
            source.inputStream.removeListener('end', source.onEnd);
            this.sources.splice(sourceIndex, 1);

            // If the source is being removed manually, we should also resolve the end promise
            this.sourceStates[src]?.endPromiseResolve();
            delete this.sourceStates[src]; // Clean up the state entry
        }
    }

    // Wait for all sources to end
    waitForAllSourcesToEnd() {
        // Promise.all resolves when all promises in the array resolve
        return Promise.all(this.endPromises);
    }
}
