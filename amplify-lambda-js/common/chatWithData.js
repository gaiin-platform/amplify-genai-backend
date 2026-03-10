//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {extractProtocol, getContexts, isDocument, processContextsSeparately} from "../datasource/datasources.js";
import {getSourceMetadata, aliasContexts} from "./chat/controllers/meta.js";
import {addContextMessage} from "./chat/controllers/common.js";
import { callUnifiedLLM } from "../llm/UnifiedLLMClient.js";
import {defaultSource} from "./sources.js";
import {getLogger} from "./logging.js";
import {createTokenCounter, countChatTokens} from "../azure/tokens.js";
import {getContextMessages} from "./chat/rag/rag.js";
import {forceFlush, sendStateEventToStream, sendStatusEventToStream} from "./streams.js";
import {newStatus} from "./status.js";
import {isKilled} from "../requests/requestState.js";
import { executeToolLoop, shouldEnableWebSearch } from "../tools/toolLoop.js";

const logger = getLogger("chatWithData");

// If contexts use >= 85% of available budget, split them from conversation to avoid overflow
const CONTEXT_FULLNESS_THRESHOLD = 0.85;

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
            } else {
                logger.warn("⚠️ RAG Query completed but no rag sources returned");
            }
            forceFlush(responseStream);
        }
    }

    // =====================================================
    // NO PRE-TRUNCATION: Fail-first overflow recovery handles this
    // See llm/contextOverflow.js - zero latency for normal users
    // =====================================================

    // Build safe messages and insert RAG context (no pre-truncation)
    const safeMessages = chatRequestOrig.messages.map(m => ({role: m.role, content: m.content}));
    const chatRequest = {
        ...chatRequestOrig,
        messages: [
            ...safeMessages.slice(0, -1),
            ...ragResults.messages,
            ...safeMessages.slice(-1)
        ]
    };

    // Calculate available tokens for contexts
    // IMPORTANT: Account for response tokens (max_tokens) - the model needs room to respond!
    const chatRequestTokens = tokenCounter.countMessageTokens(chatRequest.messages);
    let responseTokens = chatRequestOrig.max_tokens || 2000;

    const contextWindow = model.inputContextWindow;
    const safetyBuffer = Math.floor(contextWindow * 0.02); // 2% safety margin
    const maxTokens = Math.max(
        1000, // Minimum for any context
        contextWindow - chatRequestTokens - responseTokens - safetyBuffer
    );
    logger.debug(`Context budget: ${maxTokens} tokens (window: ${contextWindow}, messages: ${chatRequestTokens}, response: ${responseTokens})`);

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
        // 🔧 FIX: Deduplicate sources by dataSource.id to avoid showing same doc multiple times
        // when it gets chunked into multiple contexts
        if (contexts.length > 0) {
            const seenDocuments = new Set();  // Track which documents we've already added

            const sources = contexts.flatMap(ctx => {
                const dataSource = ctx.dataSource || dataSourceDetailsLookup[ctx.dataSourceId];
                if (!dataSource) return [];

                // 🚨 CRITICAL: Only apply document logic if it's actually a document (like original code)
                if (dataSource && isDocument(dataSource)) {
                    const name = dataSourceDetailsLookup[dataSource.id]?.name || `Attached Document (${dataSource.type})`;

                    // ✅ ATTACHED DOCUMENTS: Deduplicate - only show each document once
                    if (ctx.type === "documentContext") {
                        // Skip if we've already added this document
                        if (seenDocuments.has(dataSource.id)) {
                            return [];
                        }
                        seenDocuments.add(dataSource.id);

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
                        // Skip if we've already added this document
                        if (seenDocuments.has(dataSource.id)) {
                            return [];
                        }
                        seenDocuments.add(dataSource.id);

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
                                    content: i.content  // Full content for frontend sources
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

    // ✅ Convert contexts to messages
    const contextMessages = contexts.map(ctx => addContextMessage(ctx, tokenCounter.countTokens));

    // 🔍 DEBUG: Log context organization for multi-document scenarios
    // Note: Use contextMessages (after conversion) to get accurate content lengths
    if (contextMessages.length > 0) {
        // Group by dataSourceId from original contexts, but get lengths from converted messages
        const contextsByDoc = {};
        contexts.forEach((ctx, idx) => {
            const docId = ctx.dataSourceId || 'unknown';
            if (!contextsByDoc[docId]) {
                contextsByDoc[docId] = { chunks: 0, totalLength: 0 };
            }
            contextsByDoc[docId].chunks++;
            // Get actual content length from the converted message
            const msgContent = contextMessages[idx]?.content;
            contextsByDoc[docId].totalLength += typeof msgContent === 'string' ? msgContent.length : 0;
        });

        logger.debug(`Context organization: ${Object.keys(contextsByDoc).length} docs, ${contexts.length} chunks`);
    }

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

    // =====================================================
    // SMART CONTEXT SPLITTING: Check if contexts are too full
    // If >= 85% of budget used by contexts, split them from
    // conversation to avoid overflow. Otherwise, merge all.
    // =====================================================
    const contextTokens = contextMessages.length > 0
        ? countChatTokens(contextMessages)
        : 0;
    const contextFullness = maxTokens > 0 ? contextTokens / maxTokens : 0;
    const shouldSplitContexts = contextMessages.length > 0 && contextFullness >= CONTEXT_FULLNESS_THRESHOLD;

    logger.debug(`Context analysis: ${contextTokens}/${maxTokens} tokens (${(contextFullness * 100).toFixed(1)}% full), split: ${shouldSplitContexts}`);

    let finalMessages;
    let contextResponse = null;

    if (shouldSplitContexts) {
        // Context is >= 85% full - process contexts separately to avoid overflow
        logger.debug(`Context fullness ${(contextFullness * 100).toFixed(1)}% >= threshold - splitting contexts`);

        try {
            // Get user's question (last message)
            const userQuestion = chatRequest.messages[chatRequest.messages.length - 1]?.content || '';

            // Process contexts separately using cheap model
            contextResponse = await processContextsSeparately(
                { account, options: { ...options, model } },
                contextMessages,
                userQuestion,
                model
            );

            // Build final messages: conversation + context response (no raw contexts)
            finalMessages = [
                ...chatRequest.messages.slice(0, -1),  // Conversation history
                contextResponse,                        // Context analysis result
                ...chatRequest.messages.slice(-1)      // User's question
            ];

            logger.debug(`Contexts processed separately and integrated into conversation`);

        } catch (splitError) {
            logger.error(`Context splitting failed: ${splitError.message}, falling back to merged approach`);
            // Fallback: try merged approach anyway (might overflow, but fail-first will handle)
            finalMessages = [
                ...chatRequest.messages.slice(0, -1),
                ...contextMessages,
                ...chatRequest.messages.slice(-1)
            ];
        }
    } else {
        // Context is < 85% full - merge everything (normal case)
        logger.debug(`Context fullness ${(contextFullness * 100).toFixed(1)}% < threshold - merging all`);
        finalMessages = [
            ...chatRequest.messages.slice(0, -1),  // Includes original messages + RAG context
            ...contextMessages,                     // Add document contexts
            ...chatRequest.messages.slice(-1)      // Last message
        ];
    }

    // 🧹 CLEAN MESSAGES: Remove Location info and undefined messages before LLM call
    const cleanedMessages = finalMessages
        .filter(msg => msg && msg.role && msg.content !== undefined)
        .map(msg => ({
            role: msg.role,
            content: typeof msg.content === 'string' ?
                msg.content.replace(/Location:\s*\{[^}]*\}\s*/g, '').trim() :
                msg.content
        }))
        .filter(msg => msg.content && msg.content.length > 0);

    const requestWithContext = {
        ...chatRequest,
        messages: cleanedMessages
    };

    // Log final request info
    const hasRAGSources = ragResults.sources.length > 0;
    const hasContexts = contexts.length > 0;

    if (hasRAGSources || hasContexts) {
        logger.debug(`Final request: RAG sources: ${ragResults.sources.length}, contexts: ${contexts.length}, split: ${shouldSplitContexts}`);
    }
    if (!params.options.ragOnly && !hasRAGSources && !hasContexts) {
        logger.debug("No relevant contexts found, making direct LLM call");
    }

    // Check killswitch before LLM call
    if (await isKilled(account.user, responseStream, chatRequestOrig)) return;

    // Get conversationId and smartMessagesFiltered for overflow handler
    const smartMessagesFiltered = params.smartMessagesResult?._internal?.removedCount > 0;
    const conversationId = chatRequestOrig.options?.conversationId || params.options?.conversationId;

    // Check if web search or MCP is enabled
    let webSearchEnabled = shouldEnableWebSearch(chatRequestOrig);
    const mcpEnabled = chatRequestOrig?.mcpEnabled === true || chatRequestOrig?.options?.mcpEnabled === true;

    if (webSearchEnabled || mcpEnabled) {
        logger.debug(`Tool loop enabled (webSearch: ${webSearchEnabled}, mcp: ${mcpEnabled})`);
        await executeToolLoop(
            {
                account,
                options: {
                    ...options,
                    model,
                    requestId: params.options?.requestId
                }
            },
            requestWithContext.messages,
            model,
            responseStream,
            {
                max_tokens: requestWithContext.max_tokens || 2000,
                imageSources: chatRequestOrig.imageSources,
                mcpClientSide: mcpEnabled,
                tools: chatRequestOrig.tools || chatRequestOrig.options?.tools,
                webSearchEnabled: webSearchEnabled,
            }
        );
        return;  // Tool loop handles the response
    }

    // ✅ Direct native provider call
    // If we already split contexts, no need to pass _contexts for overflow recovery
    await callUnifiedLLM(
        { account, options: { ...options, model } },
        requestWithContext.messages,
        responseStream,
        {
            max_tokens: requestWithContext.max_tokens || 2000,
            imageSources: chatRequestOrig.imageSources,
            // Only pass contexts if we DIDN'T split them (for fail-first recovery on edge cases)
            _contexts: (!shouldSplitContexts && contexts.length > 0) ? contexts : null,
            // Pass smart messages filter flag for safe caching
            smartMessagesFiltered: smartMessagesFiltered,
            // Pass conversationId for cache management in overflow handler
            conversationId: conversationId
        }
    );
};