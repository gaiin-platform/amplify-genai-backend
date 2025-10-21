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
    
    // 🔍 DEBUG: Log incoming datasources
    console.log("📥 chatWithDataStateless: Incoming datasources:", {
        dataSources_length: dataSources?.length || 0,
        dataSources_ids: dataSources?.map(ds => ds.id?.substring(0, 50)),
        dataSources_types: dataSources?.map(ds => ds.type),
        skipRag: params.options?.skipRag,
        ragOnly: params.options?.ragOnly
    });
    
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
    
    // 🔍 DEBUG: Log RAG decision factors
    logger.info("RAG Decision Debug:", {
        skipRag: params.options?.skipRag,
        ragOnly: params.options?.ragOnly,
        skipDocumentCache: params.options?.skipDocumentCache,
        dataSourcesByUse_ragDataSources_length: dataSourcesByUse.ragDataSources?.length || 0,
        dataSourcesByUse_dataSources_length: dataSourcesByUse.dataSources?.length || 0,
        raw_dataSources_length: dataSources?.length || 0
    });

    // Extract categorized data sources
    const categorizedDataSources = dataSourcesByUse.dataSources || [];
    const ragDataSources = !params.options.skipRag ? (dataSourcesByUse.ragDataSources || []) : [];
    const conversationDataSources = params.options.skipRag && !params.options.skipDocumentCache ? 
        (dataSourcesByUse.conversationDataSources || []) : [];
    
    // 🔍 DEBUG: Log extracted data sources
    logger.info("Extracted DataSources:", {
        categorizedDataSources_length: categorizedDataSources.length,
        ragDataSources_length: ragDataSources.length,
        ragDataSources_ids: ragDataSources.map(ds => ds.id),
        conversationDataSources_length: conversationDataSources.length
    });

    // Build lookup table for data source details
    const dataSourceDetailsLookup = {};
    [...categorizedDataSources, ...ragDataSources, ...conversationDataSources].forEach(ds => {
        dataSourceDetailsLookup[ds.id] = ds;
    });

    // 🔥 SEQUENTIAL RAG PROCESSING: Follow original pattern for immediate source transmission
    const tokenLimitBuffer = chatRequestOrig.max_tokens || 1000;
    const minTokensForContext = (categorizedDataSources.length > 0) ? 1000 : 0;
    const maxTokensForMessages = model.inputContextWindow - tokenLimitBuffer - minTokensForContext;

    // ✅ STEP 1: Process RAG first (sequential, not parallel)
    let ragResults = { messages: [], sources: [] };
    
    if (ragDataSources.length > 0) {
        logger.info(`🔍 RAG Query: Starting with ${ragDataSources.length} data sources`);
        
        // Send RAG status
        const ragStatus = newStatus({
            inProgress: true,
            sticky: false,
            message: "I am searching for relevant information...",
            icon: "aperture",
        });
        if (responseStream && !responseStream.destroyed && responseStream.writable) {
            sendStatusEventToStream(responseStream, ragStatus);
            forceFlush(responseStream);
        }
        
        // Perform RAG query with error handling
        logger.info(`🔍 RAG Query: Calling getContextMessages with ${ragDataSources.length} sources`);
        console.log("📋 RAG DataSources being searched:", ragDataSources.map(ds => ({
            id: ds.id?.substring(0, 50),
            type: ds.type,
            name: ds.name
        })));
        
        try {
            ragResults = await getContextMessages(params, chatRequestOrig, ragDataSources);
            console.log(`✅ RAG Query completed:`, {
                sources_found: ragResults.sources?.length || 0,
                messages_added: ragResults.messages?.length || 0,
                sources_sample: ragResults.sources?.[0]
            });
            logger.info(`✅ RAG Query: Completed with ${ragResults.sources?.length || 0} sources found`);
        } catch (error) {
            console.error("❌ RAG Query Failed:", error);
            logger.error("❌ RAG Query Failed:", error.message);
            ragStatus.message = "RAG search failed, continuing without additional context";
            ragStatus.inProgress = false;
            if (responseStream && !responseStream.destroyed && responseStream.writable) {
                sendStatusEventToStream(responseStream, ragStatus);
                forceFlush(responseStream);
            }
            ragResults = { messages: [], sources: [] }; // Empty result on failure
        }
        
        // ✅ IMMEDIATELY send RAG sources following original pattern
        console.log("🔍 RAG: About to send sources - Debug info:", {
            ragResults_sources_length: ragResults.sources?.length || 0,
            responseStream_exists: !!responseStream,
            responseStream_destroyed: responseStream?.destroyed,
            responseStream_writable: responseStream?.writable
        });
        
        if (responseStream && !responseStream.destroyed) {
            ragStatus.inProgress = false;
            ragStatus.message = ragResults.sources.length > 0 ? 
                "Found relevant information" : "No relevant information found";
            sendStatusEventToStream(responseStream, ragStatus);
            
            console.log("🔍 RAG: Stream is valid, checking sources length:", ragResults.sources.length);
            if (ragResults.sources.length > 0) {
                console.log("📡 RAG: Sending sources to frontend (original pattern):", ragResults.sources.length, "sources");
                sendStateEventToStream(responseStream, {
                    sources: {
                        rag: {
                            sources: ragResults.sources
                        }
                    }
                });
                console.log("✅ RAG: Sources sent to stream using original pattern");
            } else {
                console.log("❌ RAG: No sources to send to frontend");
            }
            
            forceFlush(responseStream);
        } else {
            console.log("❌ RAG: Cannot send sources - stream invalid:", {
                responseStream_exists: !!responseStream,
                responseStream_destroyed: responseStream?.destroyed,
                responseStream_writable: responseStream?.writable
            });
        }
    } else {
        logger.warn("⚠️ RAG Query: No RAG data sources found, skipping RAG search");
    }

    // ✅ STEP 2: Process messages for token fitting
    const msgTokens = tokenCounter.countMessageTokens(chatRequestOrig.messages);
    const fittedMessages = msgTokens > maxTokensForMessages ? 
        fitMessagesInTokenLimit(chatRequestOrig.messages, maxTokensForMessages) : 
        chatRequestOrig.messages;

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
    const chatRequestTokens = tokenCounter.countMessageTokens(chatRequest.messages);
    const maxTokens = model.inputContextWindow - (chatRequestTokens + tokenLimitBuffer);
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
        
        if (responseStream && !responseStream.destroyed && responseStream.writable) {
            statuses.forEach(status => sendStatusEventToStream(responseStream, status));
            forceFlush(responseStream);
        }

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
        if (responseStream && !responseStream.destroyed && responseStream.writable) {
            statuses.forEach(status => {
                status.inProgress = false;
                sendStatusEventToStream(responseStream, status);
            });
            forceFlush(responseStream);
        }

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

            if (responseStream && !responseStream.destroyed && responseStream.writable) {
                console.log("📡 Document: Sending regular document sources:", Object.keys(byType), "types");
                sendStateEventToStream(responseStream, { sources: byType });
                console.log("✅ Document: Regular sources sent to stream");
            }
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
        Promise.resolve((() => {
            if (contexts.length <= 1) return false;
            const totalTokens = contexts.reduce((acc, ctx) => acc + (ctx.tokens || 1000), 0);
            return totalTokens <= maxTokens;
        })())
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