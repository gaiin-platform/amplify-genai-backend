//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {extractProtocol, getContexts, getDataSourcesByUse, isDocument} from "../datasource/datasources.js";
import {getSourceMetadata, aliasContexts} from "./chat/controllers/meta.js";
import {addContextMessage} from "./chat/controllers/common.js";
import { callLiteLLM } from "../litellm/litellmClient.js";
import {defaultSource} from "./sources.js";
import {getLogger} from "./logging.js";
import {createTokenCounter} from "../azure/tokens.js";
import {countChatTokens} from "../azure/tokens.js";
import {getContextMessages} from "./chat/rag/rag.js";
import {forceFlush, sendStateEventToStream, sendStatusEventToStream} from "./streams.js";
import {newStatus} from "./status.js";

const logger = getLogger("chatWithData");

/**
 * ✅ OPTIMIZED: Fit messages within token limit efficiently
 */
const fitMessagesInTokenLimit = (messages, tokenLimit) => {
    let tokenCount = 0;
    const messagesToKeep = [];

    // Process messages in reverse order (keeping most recent)
    for(let i = messages.length - 1; i >= 0; i--){
        const currCount = countChatTokens([messages[i]]);
        const remaining = tokenLimit - tokenCount;

        if(currCount <= remaining){
            messagesToKeep.push(messages[i]);
            tokenCount += currCount;
        } else if(i === messages.length - 1 && remaining > 100){
            // Keep at least part of the most recent message
            const ratio = Math.min(0.9, remaining / currCount);
            const splitIndex = Math.floor(messages[i].content.length * ratio);
            messagesToKeep.push({
                ...messages[i], 
                content: messages[i].content.slice(-splitIndex)
            });
            break;
        } else {
            break; // Can't fit more messages
        }
    }

    return messagesToKeep.reverse();
}

/**
 * ⚡ ULTRA-OPTIMIZED chatWithDataStateless
 * - Parallel context fetching
 * - Parallel RAG processing
 * - Streamlined token management
 * - Direct LiteLLM integration (no middleware)
 */
export const chatWithDataStateless = async (params, model, chatRequestOrig, dataSources, responseStream) => {
    if(!chatRequestOrig.messages){
        throw new Error("Chat request must have messages.");
    }

    const account = params.account;
    const options = params.options || {};
    const srcPrefix = options.source || defaultSource;
    
    // ⚡ PARALLEL PHASE 1: Initialize all independent operations
    const [
        tokenCounter,
        dataSourcesByUse
    ] = await Promise.all([
        // Token counter initialization
        Promise.resolve(createTokenCounter()),
        
        // Categorize data sources
        getDataSourcesByUse(params, chatRequestOrig, dataSources)
    ]);

    logger.debug("All datasources for chatWithData: ", dataSourcesByUse);

    // Extract categorized data sources
    const categorizedDataSources = dataSourcesByUse.dataSources || [];
    const ragDataSources = !params.options.skipRag ? (dataSourcesByUse.ragDataSources || []) : [];
    const conversationDataSources = params.options.skipRag && !params.options.skipDocumentCache ? 
        (dataSourcesByUse.conversationDataSources || []) : [];

    // Build lookup table for data source details
    const dataSourceDetailsLookup = {};
    [...categorizedDataSources, ...ragDataSources, ...conversationDataSources].forEach(ds => {
        dataSourceDetailsLookup[ds.id] = ds;
    });

    // ⚡ PARALLEL PHASE 2: RAG processing and token calculations
    const tokenLimitBuffer = chatRequestOrig.max_tokens || 1000;
    const minTokensForContext = (categorizedDataSources.length > 0) ? 1000 : 0;
    const maxTokensForMessages = model.inputContextWindow - tokenLimitBuffer - minTokensForContext;

    const [
        ragResults,
        fittedMessages
    ] = await Promise.all([
        // RAG query processing with caching
        (async () => {
            if (ragDataSources.length === 0) return {messages: [], sources: []};
            
            // ⚡ CACHE: Check for cached RAG results
            const { CacheManager } = await import('./cache.js');
            const crypto = await import('crypto');
            
            const messagesHash = crypto.createHash('sha256')
                .update(JSON.stringify(chatRequestOrig.messages))
                .digest('hex').slice(0, 32);
            const dataSourcesHash = crypto.createHash('sha256')
                .update(JSON.stringify(ragDataSources.map(ds => ds.id).sort()))
                .digest('hex').slice(0, 32);
            
            const cachedRAG = await CacheManager.getCachedRAGResults(account.user, messagesHash, dataSourcesHash);
            if (cachedRAG) {
                logger.debug(`Using cached RAG results for user ${account.user}`);
                
                // Send cached sources if found
                if (cachedRAG.sources.length > 0) {
                    sendStateEventToStream(responseStream, {
                        sources: { rag: { sources: cachedRAG.sources } }
                    });
                }
                return cachedRAG;
            }
            
            // Send RAG status
            const ragStatus = newStatus({
                inProgress: true,
                sticky: false,
                message: "I am searching for relevant information...",
                icon: "aperture",
            });
            sendStatusEventToStream(responseStream, ragStatus);
            forceFlush(responseStream);
            
            // Perform RAG query
            const result = await getContextMessages(params, chatRequestOrig, ragDataSources);
            
            // Cache the RAG results
            CacheManager.setCachedRAGResults(account.user, messagesHash, dataSourcesHash, result);
            
            // Update status
            ragStatus.inProgress = false;
            ragStatus.message = result.sources.length > 0 ? 
                "Found relevant information" : "No relevant information found";
            sendStatusEventToStream(responseStream, ragStatus);
            
            // Send sources if found
            if (result.sources.length > 0) {
                sendStateEventToStream(responseStream, {
                    sources: { rag: { sources: result.sources } }
                });
            }
            
            forceFlush(responseStream);
            return result;
        })(),
        
        // Fit messages in token limit
        (async () => {
            const msgTokens = tokenCounter.countMessageTokens(chatRequestOrig.messages);
            if (msgTokens > maxTokensForMessages) {
                return fitMessagesInTokenLimit(chatRequestOrig.messages, maxTokensForMessages);
            }
            return chatRequestOrig.messages;
        })()
    ]);

    // Build safe messages and insert RAG context
    const safeMessages = fittedMessages.map(m => ({role: m.role, content: m.content}));
    const chatRequest = {
        ...chatRequestOrig,
        messages: [
            ...safeMessages.slice(0, -1),
            ...ragResults.messages,
            ...safeMessages.slice(-1)
        ]
    };

    // Calculate max tokens for contexts
    const msgTokens = tokenCounter.countMessageTokens(chatRequest.messages);
    const maxTokens = model.inputContextWindow - (msgTokens + tokenLimitBuffer);
    logger.debug(`Using a max of ${maxTokens} tokens per request for ${model.id}`);

    // ⚡ PARALLEL PHASE 3: Context fetching for all data sources
    let contexts = [];
    if(!params.options.ragOnly && (categorizedDataSources.length > 0 || conversationDataSources.length > 0)) {
        const contextResolverEnv = {
            tokenCounter: tokenCounter.countTokens,
            params,
            chatRequest: chatRequestOrig
        };

        // Send status for all data sources
        const dataSourceList = [...categorizedDataSources, ...conversationDataSources];
        const statuses = dataSourceList.map(ds => newStatus({
            inProgress: true,
            sticky: false,
            message: `Processing: ${ds.name}...`,
            icon: "aperture",
        }));
        
        statuses.forEach(status => sendStatusEventToStream(responseStream, status));
        forceFlush(responseStream);

        // ⚡ PARALLEL: Fetch all contexts simultaneously with caching
        const { CacheManager } = await import('./cache.js');
        const contextResults = await Promise.all([
            ...categorizedDataSources.map(async ds => {
                // Check cache first
                const cached = await CacheManager.getCachedContexts(account.user, ds, maxTokens, options);
                if (cached) {
                    logger.debug(`Using cached contexts for datasource ${ds.id}`);
                    return cached.map(r => ({...r, type: "documentContext", dataSourceId: ds.id}));
                }
                
                // Not cached, fetch and cache
                const results = await getContexts(contextResolverEnv, ds, maxTokens, options);
                CacheManager.setCachedContexts(account.user, ds, maxTokens, options, results);
                return results.map(r => ({...r, type: "documentContext", dataSourceId: ds.id}));
            }),
            ...conversationDataSources.map(async ds => {
                // Check cache first
                const cacheOptions = {...options, isConversation: true};
                const cached = await CacheManager.getCachedContexts(account.user, ds, maxTokens, cacheOptions);
                if (cached) {
                    logger.debug(`Using cached conversation contexts for datasource ${ds.id}`);
                    return cached.map(r => ({...r, type: "documentCacheContext", dataSourceId: ds.id}));
                }
                
                // Not cached, fetch and cache
                const results = await getContexts(contextResolverEnv, ds, maxTokens, options, true);
                CacheManager.setCachedContexts(account.user, ds, maxTokens, cacheOptions, results);
                return results.map(r => ({...r, type: "documentCacheContext", dataSourceId: ds.id}));
            })
        ]);

        contexts = contextResults
            .flat()
            .filter(context => context !== null)
            .map(context => ({...context, id: srcPrefix + "#" + context.id}));

        // Clear all statuses
        statuses.forEach(status => {
            status.inProgress = false;
            sendStatusEventToStream(responseStream, status);
        });
        forceFlush(responseStream);

        // Process and send sources
        if (contexts.length > 0) {
            const sources = contexts.map(ctx => {
                const dataSource = ctx.dataSource || dataSourceDetailsLookup[ctx.dataSourceId];
                if (!dataSource) return null;

                if (isDocument(dataSource)) {
                    const name = dataSource.name || `Attached Document (${dataSource.type})`;
                    return {
                        type: ctx.type,
                        source: {
                            key: dataSource.id,
                            name,
                            type: dataSource.type,
                            locations: ctx.locations,
                            contentKey: dataSource.metadata?.userDataSourceId
                        }
                    };
                } else {
                    const type = (extractProtocol(dataSource.id) || "data://").split("://")[0];
                    return {
                        type,
                        source: {
                            key: dataSource.id,
                            name: dataSource.name || dataSource.id,
                            locations: ctx.locations
                        }
                    };
                }
            }).filter(s => s);

            // Group sources by type
            const byType = sources.reduce((acc, source) => {
                if(!acc[source.type]){
                    acc[source.type] = {sources: [], data: {chunkCount: 0}};
                }
                acc[source.type].sources.push(source.source);
                acc[source.type].data.chunkCount++;
                return acc;
            }, {});

            sendStateEventToStream(responseStream, { sources: byType });
        }
    }

    // Default context if none found
    if(contexts.length === 0){
        logger.debug("No data sources, using default context");
        contexts = [{id: srcPrefix}];
    }

    // ⚡ PARALLEL PHASE 4: Metadata generation and context optimization
    const [metaData, shouldMergeContexts] = await Promise.all([
        // Generate source metadata
        Promise.resolve(getSourceMetadata({contexts})),
        
        // Check if contexts can be merged
        Promise.resolve(() => {
            if (contexts.length <= 1) return false;
            const totalTokens = contexts.reduce((acc, ctx) => acc + (ctx.tokens || 1000), 0);
            return totalTokens <= maxTokens;
        })()
    ]);

    let updatedContexts = aliasContexts(metaData, contexts);

    // Merge contexts if they fit within token limit
    if (shouldMergeContexts) {
        logger.debug("Merging contexts into one for efficiency");
        const mergedContext = contexts
            .map((c, index) => `DataSource ${index + 1}: \n\n${c.context}`)
            .join("\n\n");
        updatedContexts = [{
            id: 0, 
            context: mergedContext, 
            contexts: updatedContexts
        }];
    }

    // ✅ DIRECT LITELLM INTEGRATION: Eliminated sequentialChat middleman
    // Send source metadata directly to stream
    const srcList = Array.from(Object.keys(metaData.sources)).reduce((arr, key) => ((arr[metaData.sources[key]] = key), arr), []);
    sendStateEventToStream(responseStream, { 
        metadata: { sources: srcList } 
    });
    
    // Handle multiple contexts if needed
    if (updatedContexts.length > 1) {
        // Multiple contexts - need to iterate
        sendStatusEventToStream(
            responseStream,
            newStatus({
                inProgress: false,
                message: `I will need to send ${updatedContexts.length} prompts for this request`,
                icon: "bolt",
                sticky: true
            })
        );
        
        for (const [index, context] of updatedContexts.entries()) {
            // Add context to messages
            const messagesWithContext = addContextMessage([...chatRequest.messages], context);
            const requestWithContext = {
                ...chatRequest,
                messages: messagesWithContext
            };
            
            // Send status for this context
            if (updatedContexts.length > 1) {
                sendStatusEventToStream(
                    responseStream,
                    newStatus({
                        inProgress: true,
                        message: `Sending prompt ${index + 1} of ${updatedContexts.length}`,
                        dataSource: context.id,
                        icon: "bolt",
                        sticky: false
                    })
                );
            }
            
            // ✅ Direct LiteLLM call for each context
            await callLiteLLM(requestWithContext, model, account, responseStream, [], true);
        }
        
        // Final status
        if (updatedContexts.length > 1) {
            sendStatusEventToStream(
                responseStream,
                newStatus({
                    inProgress: false,
                    message: `Completed ${updatedContexts.length} of ${updatedContexts.length} prompts`,
                    icon: "bolt",
                    sticky: false
                })
            );
        }
    } else {
        // Single context - direct call
        const context = updatedContexts[0];
        const messagesWithContext = context.context ? 
            addContextMessage([...chatRequest.messages], context) : 
            chatRequest.messages;
        
        const requestWithContext = {
            ...chatRequest,
            messages: messagesWithContext
        };
        
        // ✅ Direct LiteLLM call
        await callLiteLLM(requestWithContext, model, account, responseStream, [], true);
    }

    logger.debug("Chat function finished, ending responseStream");
    responseStream.end();
    logger.debug("Response stream ended");
};