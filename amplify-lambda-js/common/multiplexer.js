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
        this.buffers = {}; // Add buffer for incomplete messages
        this.streamClosed = false;
        
        // Debug information about the output stream
        logger.debug('Output stream type:', outputStream?.constructor?.name || 'Unknown');
        logger.debug('Output stream capabilities:', {
            hasOn: typeof outputStream?.on === 'function', 
            hasWrite: typeof outputStream?.write === 'function',
            hasEnd: typeof outputStream?.end === 'function',
            hasWritableEnded: outputStream?.writableEnded !== undefined,
            hasWritable: outputStream?.writable !== undefined
        });
        
        // Set up a listener for when the output stream ends, only if it supports events
        if (this.outputStream && typeof this.outputStream.on === 'function') {
            this.outputStream.on('finish', () => {
                this.streamClosed = true;
                logger.debug('Output stream finished/closed');
            });
            
            this.outputStream.on('error', (err) => {
                this.streamClosed = true;
                logger.error('Output stream error:', err);
            });
        } else {
            // For non-event emitter streams, we'll rely on direct checks
            logger.debug('Output stream does not support event listeners');
        }
    }
    
    // Helper method to safely write to output stream
    safeWrite(data) {
        if (!this.outputStream || this.streamClosed) {
            logger.debug('Cannot write to closed or undefined output stream');
            return false;
        }
        
        // Check if stream is ended
        const isStreamEnded = 
            // If the stream has writableEnded property, use it
            (this.outputStream.writableEnded !== undefined && this.outputStream.writableEnded) ||
            // Some streams have closed property
            (this.outputStream.closed !== undefined && this.outputStream.closed);
        
        if (isStreamEnded) {
            this.streamClosed = true;
            logger.debug('Cannot write to ended output stream');
            return false;
        }
        
        try {
            // Check if writable using different methods available
            const isWritable = 
                // If the stream has writable property, use it
                (this.outputStream.writable !== undefined ? this.outputStream.writable : true) &&
                // If the stream has a write method, it's likely writable
                typeof this.outputStream.write === 'function';
            
            if (isWritable) {
                return this.outputStream.write(data);
            } else {
                logger.debug('Output stream not writable');
                this.streamClosed = true;
                return false;
            }
        } catch (err) {
            logger.error('Error writing to output stream:', err);
            this.streamClosed = true;
            return false;
        }
    }

    addSource(inputStream, src, processor) {
        this.sourceStates[src] = { state: 'active', endPromiseResolve: null };
        this.buffers[src] = ''; // Initialize buffer for this source

        // Create a promise for the end of each source and save its resolve function
        const endPromise = new Promise((resolve) => {
            this.sourceStates[src].endPromiseResolve = resolve;
        });
        this.endPromises.push(endPromise);

        const source = {
            inputStream: inputStream,
            src: src,
            onData: (chunks) => {
                if (this.streamClosed) {
                    logger.debug('Ignoring data for source', src, 'as output stream is closed');
                    return;
                }

                // Append current chunk to buffer
                this.buffers[src] += chunks.toString();
                
                // Process complete events (SSE format uses double newlines as separators)
                const events = this.buffers[src].split('\n\n');
                
                // Keep the last potentially incomplete chunk in buffer
                this.buffers[src] = events.pop() || '';
                
                for (const eventText of events) {
                    // Skip empty events
                    if (eventText.trim().length === 0) {
                        continue;
                    }
                    
                    // Check for stream end marker
                    if (eventText.trim() === 'data: [DONE]') {
                        logger.debug('Received DONE signal for source:', src);
                        // Don't end the source yet - let the actual end event handle it
                        continue;
                    }
                    
                    // Handle metadata events
                    if (eventText.startsWith('data: {"s":"meta"')) {
                        logger.debug("Sending meta data...", eventText);
                        this.safeWrite(eventText + '\n\n');
                        continue;
                    }
                    
                    // Process regular data events
                    let dataContent = null;
                    if (eventText.startsWith('data:')) {
                        dataContent = eventText.slice(6).trim();
                    } else if (eventText.includes('\ndata:')) {
                        const dataIndex = eventText.indexOf('\ndata:');
                        dataContent = eventText.slice(dataIndex + 6).trim();
                    }
                    
                    if (dataContent !== null) {
                        try {
                            
                            // logger.debug("Parsing Event...");
                            let eventObj = JSON.parse(dataContent);
                            let transformed = eventObj;

                            if (processor) {
                                transformed = processor(eventObj);
                                // logger.debug("Processing Event...");
                            }

                            if (transformed) {
                                if (transformed.s !== 'meta') transformed.s = src;
                                this.safeWrite("data: " + JSON.stringify(transformed) + '\n\n');
                                // logger.debug("Event Transformed Sent... ", transformed);
                            }
                        } catch (err) {
                            // Handle any parsing error or write error here
                            logger.error('Error handling input source data:', "'" + eventText + "'", err);
                        }
                    }
                }
            },
            onError: (err) => {
                this.sourceStates[src].state = 'error';

                logger.error('Error from source', src, ':', err);
                let errEvent = {s: src, type: 'error'};
                this.safeWrite("data: " + JSON.stringify(errEvent) + '\n\n');

                // Clean up buffer for this source
                delete this.buffers[src];
                
                // When an error occurs, we consider the source finished
                this.sourceStates[src].endPromiseResolve();
            },
            onEnd: () => {
                this.sourceStates[src].state = 'end';

                logger.debug('Source stream ended:', src);
                
                // Process any remaining data in buffer
                if (this.buffers[src] && this.buffers[src].trim().length > 0) {
                    logger.debug('Processing remaining buffer for source:', src);
                    // Create a synthetic chunk to process remaining buffer
                    source.onData(Buffer.from(''));
                }
                
                // Clean up buffer for this source
                delete this.buffers[src];
                
                try {
                    if (!this.streamClosed) {
                        let endEvent = {s: src, type: 'end'};
                        this.safeWrite("data: " + JSON.stringify(endEvent) + '\n\n');
                    }
                } catch (err) {
                    logger.error('Error sending end event:', err);
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

            // Clean up buffer for this source
            delete this.buffers[src];
            
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
