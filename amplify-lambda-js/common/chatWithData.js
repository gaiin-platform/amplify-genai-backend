//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { Writable } from 'stream';
import {extractProtocol, getContexts, getDataSourcesByUse, isDocument} from "../datasource/datasources.js";
import {countChatTokens, countTokens} from "../azure/tokens.js";
import {handleChat as sequentialChat} from "./chat/controllers/sequentialChat.js";
import {handleChat as parallelChat} from "./chat/controllers/parallelChat.js";
import {getSourceMetadata, sendSourceMetadata, aliasContexts} from "./chat/controllers/meta.js";
import {defaultSource} from "./sources.js";
import {openAiTransform, openaiUsageTransform} from "./chat/events/openai.js";
import {geminiTransform, geminiUsageTransform } from "./chat/events/gemini.js";
import {bedrockConverseTransform, bedrockTokenUsageTransform} from "./chat/events/bedrock.js";
import {getLogger} from "./logging.js";
import {getMaxTokens, isOpenAIModel, isGeminiModel} from "./params.js";
import {createTokenCounter} from "../azure/tokens.js";
import {recordUsage} from "./accounting.js";
import { v4 as uuidv4 } from 'uuid';
import {getContextMessages} from "./chat/rag/rag.js";
import {forceFlush, sendStateEventToStream, sendStatusEventToStream} from "./streams.js";
import {newStatus} from "./status.js";
import {createBlockDetector} from "./chat/controllers/blockDetector.js";
import {localKill} from "../requests/requestState.js";

const logger = getLogger("chatWithData");

class CustomWritable extends Writable {
    constructor(underlyingStream, options = {}) {
        super(options);
        this.underlyingStream = underlyingStream;
    }

    // Override the _write method and simply pass the data to the underlying stream
    _write(chunk, encoding, callback) {
        if (this.underlyingStream.writable) {
            this.underlyingStream.write(chunk, encoding, callback);
        } else {
            callback(new Error('Underlying stream is not writable'));
        }
    }

    // Override the _final method to prevent calling end on the underlying stream
    _final(callback) {
        // Do not call end on the underlying stream
        // You can perform any cleanup here if necessary
        callback();
    }
}

const chooseController = ({chatFn, chatRequest, dataSources}) => {
    return sequentialChat;
    //return parallelChat;
}

const generateSupersetObject = (arr) => {
    const result = {};

    arr.forEach(obj => {
        Object.keys(obj).forEach(key => {
            if (!result[key]) {
                result[key] = new Set();
            }
            result[key].add(obj[key]);
        });
    });

    // Convert sets to arrays for the final result
    Object.keys(result).forEach(key => {
        result[key] = Array.from(result[key]);
    });

    return result;
}

const summarizeRanges = (obj) => {
    const result = {};

    Object.keys(obj).forEach(key => {
        const values = obj[key];
        if (values.every(value => typeof value === 'number')) {
            const min = Math.min(...values);
            const max = Math.max(...values);
            result[key] = `${min}-${max}`;
        } else {
            result[key] = values;
        }
    });

    return result;
}

const summarizeLocations = (locations) => {
    const superset = generateSupersetObject(locations);
    return summarizeRanges(superset);
}

const splitMessageToFit = (message, tokenLimit) => {
    let content = message.content;
    let tokens = countTokens(content);

    while(tokens > tokenLimit){
        // Split at an arbitrary point in the string that is roughly the right length.
        const ratio = (tokenLimit / tokens) * 0.8;
        // Split at an arbitrary point in the string that is roughly the right length.
        const splitIndex = Math.floor(content.length * ratio);
        content = content.slice(-1 * splitIndex);
        tokens = countTokens(content);
    }

    return {...message, content};
}

const fitMessagesInTokenLimit = (messages, tokenLimit) => {
    // We have to make sure that the message history will fit into a prompt
    // and trim the list of messages if it won't.

    let tokenCount = 0;
    const messagesToKeep = [];

    for(let i = messages.length - 1; i >= 0; i--){
        const currCount = countChatTokens([messages[i]]);
        const remaining = tokenLimit - tokenCount;

        if(currCount <= remaining){
            // Message fits, add it
            messagesToKeep.push(messages[i]);
            tokenCount += currCount;
        }
        else if(currCount > 200 && remaining > 200){
            // The message is too big to fit and it is not the most recent message, so
            // we keep it if there are enough tokens left to justify trying to cram it in
           messagesToKeep.push(splitMessageToFit(messages[i], remaining));
           break;
        }
        else if(i === messages.length - 1){
            // No matter what, we need at least one message, so we just truncate the most
            // recent message if it is too big.
            messagesToKeep.push(splitMessageToFit(messages[i], remaining));
            break;
        }
    }

    return messagesToKeep.reverse();
}


export const chatWithDataStateless = async (params, chatFn, chatRequestOrig, dataSources, responseStream) => {

    if(!chatRequestOrig.messages){
        throw new Error("Chat request must have messages.");
    }

    // To RAG or not to RAG, that is the question...
    // 1. Is the document beneath a token threshold where we can just dump it in the prompt?
    // 2. Do we even need the documents farther back in the conversation to answer the question?
    // 3. Is the document done processing wtih RAG, if not, run against the whole document.

    const allSources = await getDataSourcesByUse(params, chatRequestOrig, dataSources);

    logger.debug("All datasources for chatWithData: ", allSources);

    // These data sources are the ones that will be completely inserted into the
    // conversation
    dataSources = allSources.dataSources;
    // These data sources will be searched with RAG for relevant information to
    // insert into the conversation
    let ragDataSources = [];
    let conversationDataSources = [];

    if (!params.options.skipRag) { // only populated on !params.options.skipRag
        ragDataSources = allSources.ragDataSources;
    } else if (!params.options.skipDocumentCache) { // only populated when rag is off
        conversationDataSources = allSources.conversationDataSources;
    } 

    // This is helpful later to convert a key to a data source
    // file name and type
    const dataSourceDetailsLookup = {};
    [ragDataSources, dataSources, conversationDataSources].forEach(sourceArray => {
        sourceArray.forEach(ds => {
            dataSourceDetailsLookup[ds.id] = ds;
        });
    });


    const ragStatus = newStatus({
        inProgress: true,
        sticky: false,
        message: "I am searching for relevant information...",
        icon: "aperture",
    });

    if (ragDataSources.length > 0) {
        sendStatusEventToStream(responseStream, ragStatus);
        forceFlush(responseStream);
    }

    // Query for related information from RAG
    const {messages:ragContextMsgs, sources} = (ragDataSources.length > 0) ?
        await getContextMessages(params, chatRequestOrig, ragDataSources) :
        {messages:[], sources:[]};

    if (ragDataSources.length > 0) {
        if (sources.length > 0) {
            sendStateEventToStream(responseStream, {
            sources: {
                rag:{
                    sources: sources
                }
            }
            });
        } else {
            logger.error("Rag Error: No sources found");
            ragStatus.message = "Rag ran into an unexpected error";
            // TODO: Think through this logic better
            // if (!params.options.skipDocumentCache) {
            //     logger.debug("File caching will be used instead...");
            //     conversationDataSources = ragDataSources;
            // }
        }

        ragStatus.inProgress = false;
        sendStatusEventToStream(responseStream, ragStatus);
        forceFlush(responseStream);
            
    }

    // Remove any non-standard attributes on messages
    const safeMessages = [
        ...chatRequestOrig.messages.map(m => {
            return {role: m.role, content: m.content}
        })
    ];

    // Build the chat request and insert the rag context
    const chatRequest = {
        ...chatRequestOrig,
        messages: [
            ...safeMessages.slice(0, -1),
            ...ragContextMsgs,
            ...safeMessages.slice(-1)
        ]
    };

    const requestId = params.requestId || ""+uuidv4();
    logger.debug(`Chat with data called with request id ${requestId}`);

    const account = params.account;
    const model = params.model;
    const options = params.options || {};
    const details = {userSetMaxTokenLimit: getMaxTokens(params)};

    let srcPrefix = options.source || defaultSource;

    const tokenCounter = createTokenCounter(model);

    // We have to leave a buffer for the output
    const tokenLimitBuffer = chatRequest.max_tokens || 1000;

    // If we have data sources, we need to make sure that we have enough tokens to
    // fit the context window of the model. If we don't have enough tokens, we will
    // trim the message history to fit.
    let msgTokens = tokenCounter.countMessageTokens(chatRequest.messages);
    const minTokensForContext = (dataSources && dataSources.length > 0) ? 1000 : 0;
    const maxTokensForMessages = model.inputContextWindow - tokenLimitBuffer - minTokensForContext
    if(msgTokens > maxTokensForMessages) {
        chatRequest.messages = fitMessagesInTokenLimit(chatRequest.messages, maxTokensForMessages);
    }

    // Since it isn't exact, we have to leave a buffer of tokens to ensure we don't go over the limit.
    // and account for formatting, etc.

    msgTokens = tokenCounter.countMessageTokens(chatRequest.messages);
    logger.debug(`Total tokens in messages: ${msgTokens}`);

    const maxTokens = model.inputContextWindow - (msgTokens + tokenLimitBuffer);

    logger.debug(`Using a max of ${maxTokens} tokens per request for ${model.id} with a buffer of ${tokenLimitBuffer}.`)


    // This is a block detector that is used to detect the end of an assistant operation
    // and automatically ignore the rest of the output. If it isn't set, nothing will
    // happen, it will just return the input.
    let responseStreamClosed = false;
    const blockTerminator = createBlockDetector(options.blockTerminator);
    // This function is used to transform the output of the LLM provider into
    // a format that can be streamed back to the client. It translates from the
    // native streaming format of the LLM provider to the format expected by the
    // client. Both us and the LLM provider will use Server Side Events that have the
    // format:
    // data: <json encoded event>
    // We have our own custom event format.
    // The transformer also is where we count output tokens from OpenAI. Each event
    // from OpenAI is one token. Every time we reach the "increment" of tokens, we
    // forward bill for another increment.
    const eventTransformer = (event) => {

        let result;

        if (isOpenAIModel(model.id)) {
            const usage = openaiUsageTransform(event);
            if (usage) {
                recordUsage(account, requestId, model, usage.prompt_tokens, usage.completion_tokens, 
                            usage.prompt_tokens_details?.cached_tokens ?? 0,
                           {...details, reasoning_tokens: usage.completion_tokens_details?.reasoning_tokens,
                            prompt_tokens_details: usage.prompt_tokens_details,
                           });
            }

            result = openAiTransform(event, responseStream);  
            
        } else if (model.provider === 'Bedrock') {
            const usage = bedrockTokenUsageTransform(event);
            if (usage) {
                recordUsage(account, requestId, model, usage.inputTokens, usage.outputTokens, usage.cached_tokens || 0, details);
            }
            result = bedrockConverseTransform(event, responseStream);
        } else if (isGeminiModel(model.id)) {            
            result = geminiTransform(event, responseStream);
            const usage = geminiUsageTransform(event);
            if (usage) {
                recordUsage(account, requestId, model, usage.prompt_tokens, usage.completion_tokens, 
                            usage.prompt_tokens_details?.cached_tokens ?? 0,
                           {...details, reasoning_tokens: usage.completion_tokens_details?.reasoning_tokens,
                            prompt_tokens_details: usage.prompt_tokens_details,
                           });
            }
        }
 
        if(result && result.d){
            const [blockEnded, remaining] = blockTerminator(result.d);
            if(blockEnded){
                result.d = remaining;
                //localKill(account, requestId);
            }
        }


        return result;
    }

    // This is where we take the data sources, which are typically uploaded files from the client,
    // and fetch them from the storage provider. We also break them up into chunks that fit into the
    // context window of whatever model we are using. Each chunk becomes a "context" and we will prompt
    // against each context in a separate request to the LLM.
    let contexts = []
    if(!params.options.ragOnly && (dataSources.length > 0 || conversationDataSources.length > 0)) {
        const DOCUMENT_CONTEXT_CACHE = "documentCacheContext";
        const DOCUMENT_CONTEXT = "documentContext";
        try {
            const contextResolverEnv = {
                tokenCounter: tokenCounter.countTokens,
                chatFn,
                params,
                chatRequest:chatRequestOrig
            };

           const statuses = [...dataSources, ...conversationDataSources].map(dataSource => {
                const status = newStatus({
                    inProgress: true,
                    sticky: false,
                    message: `Searching: ${dataSource.name}...`,
                    icon: "aperture",
                });
                return status;
            });
            
           statuses.map(async (status) => {
                sendStatusEventToStream(responseStream, status);
                forceFlush(responseStream);
            });

            contexts = (await Promise.all([
                ...dataSources.map(async dataSource => {
                    const results = await getContexts(contextResolverEnv, dataSource, maxTokens, options);
                    return results.map(result => ({...result, type: DOCUMENT_CONTEXT}));
                }), 
                ...conversationDataSources.map(async dataSource => {
                    const results = await getContexts(contextResolverEnv, dataSource, maxTokens, options, true);
                    return results.map(result => ({...result, type: DOCUMENT_CONTEXT_CACHE}));
                }) ]))
                .flat()
                .filter(context => context !== null)
                .map((context) => {
                    return {...context, id: srcPrefix + "#" + context.id};
                })

            statuses.forEach(status => {
                status.inProgress = false;
                sendStatusEventToStream(responseStream, status);
                forceFlush(responseStream);
            });

            if (contexts.length > 0) {

                const sources = contexts.map(s => {
                    const dataSource = s.dataSource;
                    if (s && !dataSource) {
                        const source = {key: s.id, type: dataSource.type, locations: s.locations};
                        return {type: DOCUMENT_CONTEXT, source};
                    } else if (dataSource && isDocument(dataSource)){
                        const name = dataSourceDetailsLookup[dataSource.id]?.name || "Attached Document ("+dataSource.type+")";
                        const getSourceData = (locations = null) => {
                            return {key: dataSource.id, name, type: dataSource.type, locations: locations ?? s.locations, contentKey: dataSource.metadata?.userDataSourceId};
                        }
                        if (s.type === DOCUMENT_CONTEXT || !s.content) return {type: s.type, source: getSourceData()};
                        if (s.type === DOCUMENT_CONTEXT_CACHE) {
                            // Return list of sources from the combined neighboring locations
                            if (s.content) {
                                return s.content.map(i => {
                                    const source = getSourceData(i.locations);
                                    source.content = i.content;
                                    return {type: s.type, source};
                                });
                            } else {
                                return {type: s.type, source: getSourceData()};
                            }
                        }
                        return null;
                    } else if (dataSource) {
                        const type = (extractProtocol(dataSource.id) || "data://").split("://")[0];
                        return {type, source: {key: dataSource.id, name: dataSource.name || dataSource.id, locations: s.locations}};
                    } else {
                        return null;
                    }
                }).flat().filter(s => s);

                const byType = sources.reduce((acc, source) => {
                    if(!acc[source.type]){
                        acc[source.type] = {sources: [], data: {chunkCount: 0}};
                    }
                    acc[source.type].sources.push(source.source);
                    acc[source.type].data.chunkCount++;
                    return acc;
                }, {});

                sendStateEventToStream(responseStream, {
                    sources: {
                    //
                        ...byType
                    }
                });
            }

        } catch (e) {
            logger.error(e);
            logger.error("Error fetching contexts: " + e);
            return {
                statusCode: 404,
                body: {error: "Data source not found."}
            };
        }
    }

    if(contexts.length === 0){
        logger.debug("No data sources, just using messages.");
        contexts = [{id:srcPrefix}];
    }

    // Create the source metadata that maps contexts to shorter ids to
    // to be more efficient.
    const metaData = getSourceMetadata({contexts});
    let updatedContexts = aliasContexts(metaData, contexts);

    // if the all contexts fit within the token limit then we can just merge them into one while leaving the metadata as its been
    if (updatedContexts.length > 1) {
        const totalDsTokens = contexts.reduce((acc, context) => acc + (context.tokens || 1000), 0);
        if (totalDsTokens <= maxTokens) {
            logger.debug("Merging contexts into one: ", updatedContexts);
            const mergedContext = contexts.map((c, index) => `DataSource ${index + 1}: \n\n${c.context}`).join("\n\n");
            // we can save the old contexts in its own attribute for now
            updatedContexts = [{id: 0, context: mergedContext, contexts: updatedContexts}];
        }
    }

    const chatContext = {
        account,
        chatFn,
        chatRequest,
        dataSources,
        contexts:updatedContexts,
        metaData,
        responseStream,
        eventTransformer
    };

    // Should no longer be needed, keeping for now
    // if(responseStream.setContentType){
    //     responseStream.setContentType('text/event-stream');
    // }

    // Since we have multiple contexts, we can potentially execute them in parallel.
    // This code provides future support for that, but currently we execute them in
    // sequence with the sequentialChat.
    const controller = chooseController(chatContext);
    await controller(chatContext);

    logger.debug("Chat function finished, ending responseStream.");

    responseStream.end();


    logger.debug("Response stream ended");
};

