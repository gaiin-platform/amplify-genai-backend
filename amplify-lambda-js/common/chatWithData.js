//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {extractProtocol, getContexts, isDocument} from "../datasource/datasources.js";
import {getSourceMetadata, aliasContexts} from "./chat/controllers/meta.js";
import {addContextMessage} from "./chat/controllers/common.js";
import { callUnifiedLLM } from "../llm/UnifiedLLMClient.js";
import {defaultSource} from "./sources.js";
import {getLogger} from "./logging.js";
import {createTokenCounter} from "../azure/tokens.js";
import {countChatTokens} from "../azure/tokens.js";
import {getContextMessages} from "./chat/rag/rag.js";
import {forceFlush, sendStateEventToStream, sendStatusEventToStream} from "./streams.js";
import {newStatus} from "./status.js";
import {isKilled} from "../requests/requestState.js";

const logger = getLogger("chatWithData");

/**
 * Truncate content for UI display while preserving readability
 * @param {string} content - The full content
 * @param {number} maxChars - Maximum characters to show (default 500)
 * @returns {string} Truncated content with "..." if needed
 */
const truncateContentForDisplay = (content, maxChars = 500) => {
    if (!content || content.length <= maxChars) {
        return content;
    }
    
    // Find a good break point (end of sentence or paragraph)
    let truncated = content.substring(0, maxChars);
    const lastPeriod = truncated.lastIndexOf('.');
    const lastNewline = truncated.lastIndexOf('\n');
    
    // Use the latest good break point
    const breakPoint = Math.max(lastPeriod, lastNewline);
    if (breakPoint > maxChars * 0.7) { // If we found a good break point
        truncated = content.substring(0, breakPoint + 1);
    }
    
    return truncated + "...";
};

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
 * - Direct native provider integration (no Python subprocess)
 */
export const chatWithDataStateless = async (params, model, chatRequestOrig, dataSources, responseStream) => {
    if(!chatRequestOrig.messages){
        throw new Error("Chat request must have messages.");
    }

    const account = params.account;
    const options = params.options || {};
    const srcPrefix = options.source || defaultSource;
    
    
    // 🚀 PERFORMANCE BREAKTHROUGH: Use pre-resolved data sources directly - NO duplicate calls!
    const tokenCounter = createTokenCounter();
    
    // If we're routed here, router should ALWAYS provide pre-resolved sources
    if (!params.preResolvedDataSourcesByUse) {
        logger.error("❌ CRITICAL: chatWithDataStateless called without pre-resolved data sources - this should never happen!");
        throw new Error("Pre-resolved data sources required but not provided");
    }
    
    logger.debug("✅ Using pre-resolved data sources from router (ZERO duplicate calls)");
    const dataSourcesByUse = params.preResolvedDataSourcesByUse;

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
        if (responseStream && !responseStream.destroyed) {
            sendStatusEventToStream(responseStream, ragStatus);
            forceFlush(responseStream);
        }
        
        // Perform RAG query with error handling
        logger.info(`🔍 RAG Query: Calling getContextMessages with ${ragDataSources.length} sources`);
        
        try {
            ragResults = await getContextMessages(params, chatRequestOrig, ragDataSources);
            logger.debug(`✅ RAG Query completed:`, {
                sources_found: ragResults.sources?.length || 0,
                messages_added: ragResults.messages?.length || 0,
                sources_sample: ragResults.sources?.[0]
            });
            logger.info(`✅ RAG Query: Completed with ${ragResults.sources?.length || 0} sources found`);
        } catch (error) {
            logger.error("❌ RAG Query Failed:", error);
            logger.error("❌ RAG Query Failed:", error.message);
            ragStatus.message = "RAG search failed, continuing without additional context";
            ragStatus.inProgress = false;
            if (responseStream && !responseStream.destroyed) {
                sendStatusEventToStream(responseStream, ragStatus);
                forceFlush(responseStream);
            }
            ragResults = { messages: [], sources: [] }; // Empty result on failure
        }
        
        // ✅ IMMEDIATELY send RAG sources following original pattern
        if (responseStream && !responseStream.destroyed) {
            ragStatus.inProgress = false;
            ragStatus.message = ragResults.sources.length > 0 ? 
                "Found relevant information" : "No relevant information found";
            sendStatusEventToStream(responseStream, ragStatus);
            
            if (ragResults.sources.length > 0) {
                sendStateEventToStream(responseStream, {
                    sources: {
                        rag: {
                            sources: ragResults.sources
                        }
                    }
                });
            }
            forceFlush(responseStream);
        }
    }

    // Calculate max tokens for context document processing
    const fittedMessages = fitMessagesInTokenLimit(chatRequestOrig.messages, maxTokensForMessages);

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
        
        if (responseStream && !responseStream.destroyed) {
            statuses.forEach(status => {
                sendStatusEventToStream(responseStream, status);
                forceFlush(responseStream); // Force flush each status
            });
        }

        // ⚡ PARALLEL: Fetch all contexts simultaneously with caching
        const { CacheManager } = await import('./cache.js');
        const contextResults = await Promise.all([
            ...categorizedDataSources.map(async ds => {
                // Check cache first
                const cached = await CacheManager.getCachedContexts(account.user, ds, maxTokens, options);
                if (cached && Array.isArray(cached) && cached.length > 0) {
                    logger.debug(`Using cached contexts for datasource ${ds.id}`);
                    return cached.map(r => ({...r, type: "documentContext", dataSourceId: ds.id}));
                }
                
                // Not cached, fetch and cache
                const results = await getContexts(contextResolverEnv, ds, maxTokens, options);
                // Only cache successful results, not null/empty
                if (results && Array.isArray(results) && results.length > 0) {
                    CacheManager.setCachedContexts(account.user, ds, maxTokens, options, results);
                }
                return (results || []).map(r => ({...r, type: "documentContext", dataSourceId: ds.id}));
            }),
            ...conversationDataSources.map(async ds => {
                // ⚠️ CRITICAL: Never cache conversation contexts because getExtractedRelevantContext 
                // depends on the user's current message! Each query needs fresh extraction.
                // Only the raw document content should be cached (inside getContent).
                
                // Always fetch fresh - extraction depends on current user message
                const results = await getContexts(contextResolverEnv, ds, maxTokens, options, true);
                return (results || []).map(r => ({...r, type: "documentCacheContext", dataSourceId: ds.id}));
            })
        ]);

        contexts = contextResults
            .flat()
            .filter(context => context !== null)
            .map(context => ({...context, id: srcPrefix + "#" + context.id}));

        // Clear all statuses
        if (responseStream && !responseStream.destroyed) {
            statuses.forEach(status => {
                status.inProgress = false;
                sendStatusEventToStream(responseStream, status);
            });
            forceFlush(responseStream);
        }

        // Process and send sources
        if (contexts.length > 0) {
            const sources = contexts.flatMap(ctx => {
                const dataSource = ctx.dataSource || dataSourceDetailsLookup[ctx.dataSourceId];
                if (!dataSource) return [];

                // 🚨 CRITICAL: Only apply document logic if it's actually a document (like original code)
                if (dataSource && isDocument(dataSource)) {
                    const name = dataSourceDetailsLookup[dataSource.id]?.name || `Attached Document (${dataSource.type})`;

                    // ✅ ATTACHED DOCUMENTS: Flat structure for frontend
                    if (ctx.type === "documentContext") {
                        return [{
                            type: ctx.type,
                            key: dataSource.id,
                            name: name,
                            dataType: dataSource.type,
                            locations: ctx.locations || [],
                            contentKey: dataSource.metadata?.userDataSourceId
                        }];
                    }
                    
                    // Handle cases where content is missing (fallback)
                    if (!ctx.content) {
                        return [{
                            type: ctx.type,
                            key: dataSource.id,
                            name: name,
                            dataType: dataSource.type,
                            locations: ctx.locations || [],
                            contentKey: dataSource.metadata?.userDataSourceId
                        }];
                    }
                    
                    if (ctx.type === "documentCacheContext") {
                        // Check if this context has extracted content groups (from combineNeighboringLocations)
                        if (ctx.content && Array.isArray(ctx.content)) {
                            // SAFEGUARD: Limit to top 10 most relevant groups to prevent UI overload
                            if (ctx.content.length > 20) {
                                logger.warn("🛡️ SAFEGUARD: Limiting to top 10 content groups to prevent UI overload");
                                ctx.content = ctx.content.slice(0, 10);
                            }
                            
                            // Return list of sources from the combined neighboring locations (original pattern)
                            return ctx.content.map(i => {
                                return {
                                    type: ctx.type,
                                    key: dataSource.id,
                                    name: name,
                                    dataType: dataSource.type,
                                    locations: i.locations || [],
                                    contentKey: dataSource.metadata?.userDataSourceId,
                                    content: truncateContentForDisplay(i.content)  // Truncate for UI display
                                };
                            });
                        } else {
                            return [{
                                type: ctx.type,
                                key: dataSource.id,
                                name: name,
                                dataType: dataSource.type,
                                locations: ctx.locations || [],
                                contentKey: dataSource.metadata?.userDataSourceId
                            }];
                        }
                    }
                    return [];
                } else if (dataSource) {
                    // Non-document sources (from original code)  
                    const sourceType = (extractProtocol(dataSource.id) || "data://").split("://")[0];
                    return [{
                        type: sourceType,
                        key: dataSource.id, 
                        name: dataSource.name || dataSource.id, 
                        locations: ctx.locations || []
                    }];
                } else {
                    return [];
                }
            }).filter(source => source);

            if (sources.length > 0) {
                // Send document sources with proper categorization
                const contextSources = {};
                let hasAttachedDocuments = false;
                let hasDocumentCache = false;

                sources.forEach(source => {
                    const categoryKey = source.type === "documentCacheContext" ? "documentCacheContext" : "documentContext";
                    
                    if (source.type === "documentCacheContext") {
                        hasDocumentCache = true;
                    } else {
                        hasAttachedDocuments = true;
                    }
                    
                    if (!contextSources[categoryKey]) {
                        contextSources[categoryKey] = { sources: [] };
                    }
                    contextSources[categoryKey].sources.push(source);
                });

                if (responseStream && !responseStream.destroyed) {
                    sendStateEventToStream(responseStream, {
                        sources: contextSources
                    });
                    forceFlush(responseStream);
                }
            }
        }
    }

    // ✅ Final message construction with all contexts
    const contextMessages = contexts.map(ctx => addContextMessage(ctx, tokenCounter.countTokens));
    
    // 🔍 DEBUG: Log context being passed to LLM
    if (contextMessages.length > 0) {
        logger.debug("🔍 CONTEXT PASSED TO LLM:", {
            contextCount: contextMessages.length,
            contextTypes: contexts.map(ctx => ctx.type),
            contextPreview: contextMessages.map(msg => ({
                role: msg.role,
                contentLength: typeof msg.content === 'string' ? msg.content.length : 'not-string',
                contentPreview: typeof msg.content === 'string' ? 
                    msg.content.substring(0, 100) + "..." : 
                    `[${typeof msg.content}] ${JSON.stringify(msg.content).substring(0, 100)}...`
            }))
        });
    }
    
    const rawMessages = [
        ...chatRequest.messages.slice(0, -1),
        ...contextMessages,
        ...chatRequest.messages.slice(-1)
    ];

    // 🧹 CLEAN MESSAGES: Remove Location info and undefined messages before LLM call
    const cleanedMessages = rawMessages
        .filter(msg => msg && msg.role && msg.content !== undefined) // Remove undefined messages
        .map(msg => ({
            role: msg.role,
            content: typeof msg.content === 'string' ? 
                msg.content.replace(/Location:\s*\{[^}]*\}\s*/g, '').trim() : // Remove Location: {...}
                msg.content
        }))
        .filter(msg => msg.content && msg.content.length > 0); // Remove empty content

    const requestWithContext = {
        ...chatRequest,
        messages: cleanedMessages
    };

    // Check killswitch before making final request
    if (await isKilled(account.user, responseStream, requestWithContext)) return;

    // Process the final request based on context types
    const hasRAGSources = ragResults.sources.length > 0;
    const hasContexts = contexts.length > 0;

    if (hasRAGSources || hasContexts) {
        logger.info(`🎯 Final request with contexts: RAG sources: ${ragResults.sources.length}, Context documents: ${contexts.length}`);
        
        // Check killswitch before LLM call
        if (await isKilled(account.user, responseStream, chatRequestOrig)) return;
        
        // ✅ Direct native provider call for each context
        await callUnifiedLLM(
            { account, options: { ...options, model } },  // Pass all options including trackConversations
            requestWithContext.messages,
            responseStream,
            { 
                max_tokens: requestWithContext.max_tokens || 2000,
                imageSources: chatRequestOrig.imageSources  // ✅ FIX: Pass imageSources through options
            }
        );
    } else if(!params.options.ragOnly) {
        // No context, direct LLM call
        logger.info("🎯 No relevant contexts found, making direct LLM call");
        
        // Check killswitch before LLM call
        if (await isKilled(account.user, responseStream, chatRequestOrig)) return;
        
        await callUnifiedLLM(
            { account, options: { ...options, model } },  // Pass all options including trackConversations
            requestWithContext.messages,
            responseStream,
            { 
                max_tokens: requestWithContext.max_tokens || 2000,
                imageSources: chatRequestOrig.imageSources  // ✅ FIX: Pass imageSources through options
            }
        );
    }
};