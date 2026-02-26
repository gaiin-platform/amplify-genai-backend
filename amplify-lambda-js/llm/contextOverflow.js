//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

/**
 * Context Overflow Management System
 *
 * PHILOSOPHY: FAIL-FIRST with PROACTIVE SPLITTING
 * - chatWithData handles large contexts proactively (85% threshold)
 * - This module handles overflow RECOVERY for edge cases (long conversations)
 * - Zero overhead for 99% of users who don't overflow
 *
 * ARCHITECTURE:
 *
 *   User Request → chatWithData
 *        │
 *        ├── Contexts < 85% full → Merge all, single LLM call
 *        │
 *        └── Contexts >= 85% full → Split proactively:
 *                │
 *                ├── Call 1: contexts + question (internal)
 *                └── Call 2: conversation + context response → stream
 *        │
 *        ▼
 *   callUnifiedLLM → Provider
 *        │
 *        ├── Success (99%) → Done!
 *        │
 *        └── Overflow Error → handleContextOverflow()
 *                │
 *                ├── Historical extraction (shrink long conversations)
 *                ├── Retry with recovered messages
 *                │
 *                └── If still fails → Critical log + error
 *
 * ONE RECOVERY ATTEMPT:
 *   - First overflow = try recovery (historical extraction)
 *   - If recovery fails = critical log + throw
 */

import { getLogger } from '../common/logging.js';
import { createTokenCounter } from '../azure/tokens.js';
import { CacheManager } from '../common/cache.js';
import crypto from 'crypto';
import { sendStatusEventToStream, forceFlush } from '../common/streams.js';
import { newStatus } from '../common/status.js';

const logger = getLogger('contextOverflow');

// Recovery attempt tracking (don't critical log first overflow - it's our chance to recover)
const recoveryAttempts = new Map();
const RECOVERY_ATTEMPT_TTL = 5 * 60 * 1000; // 5 minutes

// ============================================================================
// OVERFLOW DETECTION
// ============================================================================

/**
 * Detect if an error is a context overflow error and extract details
 *
 * Provider patterns:
 * - Bedrock: "prompt is too long: 202437 tokens > 200000 maximum"
 * - OpenAI/Azure: "maximum context length is 8192 tokens, however you requested 17603 tokens"
 * - Gemini: "Request payload size exceeds the limit" or similar
 *
 * @param {Error} error - The error from the LLM provider
 * @returns {Object} { isOverflow, provider, requested, limit, overflow }
 */
export function detectContextOverflow(error) {
    // Check multiple locations for the error message (different providers put it in different places)
    const axiosMessage = error?.message || '';
    const responseDataMessage = error?.response?.data?.error?.message || error?.response?.data?.message || '';
    const parsedMessage = error?._parsedErrorMessage || ''; // Set by OpenAI after reading streamed error
    const message = parsedMessage || responseDataMessage || axiosMessage || String(error) || '';

    // =========================================================================
    // BEDROCK PATTERNS (Claude via Bedrock)
    // =========================================================================
    // Pattern 1: "prompt is too long: 202437 tokens > 200000 maximum"
    const bedrockMatch = message.match(/prompt is too long:\s*(\d+)\s*tokens?\s*>\s*(\d+)/i);
    if (bedrockMatch) {
        const requested = parseInt(bedrockMatch[1]);
        const limit = parseInt(bedrockMatch[2]);
        return {
            isOverflow: true,
            provider: 'bedrock',
            requested,
            limit,
            overflow: requested - limit
        };
    }

    // Pattern 2: ValidationException with token info
    const bedrockValidationMatch = message.match(/ValidationException.*?(\d+)\s*tokens.*?(\d+)/i);
    if (bedrockValidationMatch) {
        const nums = [parseInt(bedrockValidationMatch[1]), parseInt(bedrockValidationMatch[2])];
        const requested = Math.max(...nums);
        const limit = Math.min(...nums);
        return {
            isOverflow: true,
            provider: 'bedrock',
            requested,
            limit,
            overflow: requested - limit
        };
    }

    // Pattern 2b: ValidationException without token counts (e.g., "Input is too long for requested model")
    if (/ValidationException.*Input is too long/i.test(message)) {
        return {
            isOverflow: true,
            provider: 'bedrock',
            requested: null,
            limit: null,
            overflow: null
        };
    }

    // Pattern 3: "input length and 'max_tokens' exceed context limit: X + Y > Z"
    const bedrockMaxTokensMatch = message.match(/input length and.*max_tokens.*exceed.*context limit:\s*(\d+)\s*\+\s*(\d+)\s*>\s*(\d+)/i);
    if (bedrockMaxTokensMatch) {
        const inputTokens = parseInt(bedrockMaxTokensMatch[1]);
        const maxTokens = parseInt(bedrockMaxTokensMatch[2]);
        const contextLimit = parseInt(bedrockMaxTokensMatch[3]);
        return {
            isOverflow: true,
            provider: 'bedrock',
            requested: inputTokens,
            limit: contextLimit - maxTokens,
            overflow: inputTokens - (contextLimit - maxTokens),
            maxTokensReserved: maxTokens,
            totalContextLimit: contextLimit
        };
    }

    // Pattern 4: "request too large" or model-specific messages
    if (/request.*(too large|too long)|input.*exceeds/i.test(message)) {
        const numbersMatch = message.match(/(\d{4,})/g);
        let requested = null, limit = null;
        if (numbersMatch && numbersMatch.length >= 2) {
            const nums = numbersMatch.map(n => parseInt(n));
            requested = Math.max(...nums);
            limit = Math.min(...nums);
        }
        return {
            isOverflow: true,
            provider: 'bedrock',
            requested,
            limit,
            overflow: requested && limit ? requested - limit : null
        };
    }

    // =========================================================================
    // OPENAI / AZURE PATTERNS
    // =========================================================================
    // Pattern 1: "maximum context length is 8192 tokens, however you requested 17603 tokens"
    const openaiMatch = message.match(/maximum context length is (\d+) tokens.*requested (\d+) tokens/i);
    if (openaiMatch) {
        const limit = parseInt(openaiMatch[1]);
        const requested = parseInt(openaiMatch[2]);
        return {
            isOverflow: true,
            provider: 'openai',
            requested,
            limit,
            overflow: requested - limit
        };
    }

    // Pattern 2: "This model's maximum context length is X tokens"
    const openaiAltMatch = message.match(/model's maximum context length is (\d+) tokens.*requested (\d+) tokens/i);
    if (openaiAltMatch) {
        const limit = parseInt(openaiAltMatch[1]);
        const requested = parseInt(openaiAltMatch[2]);
        return {
            isOverflow: true,
            provider: 'openai',
            requested,
            limit,
            overflow: requested - limit
        };
    }

    // Pattern 3: "resulted in X tokens" (Azure variant)
    const azureMatch = message.match(/maximum context length is (\d+).*?resulted in (\d+)/i);
    if (azureMatch) {
        const limit = parseInt(azureMatch[1]);
        const requested = parseInt(azureMatch[2]);
        return {
            isOverflow: true,
            provider: 'azure',
            requested,
            limit,
            overflow: requested - limit
        };
    }

    // Pattern 4: "Input tokens exceed the configured limit of X tokens. Your messages resulted in Y tokens"
    const configuredLimitMatch = message.match(/configured limit of (\d+) tokens.*resulted in (\d+) tokens/i);
    if (configuredLimitMatch) {
        const limit = parseInt(configuredLimitMatch[1]);
        const requested = parseInt(configuredLimitMatch[2]);
        return {
            isOverflow: true,
            provider: 'openai',
            requested,
            limit,
            overflow: requested - limit
        };
    }

    // Pattern 5: content_too_long or context_length_exceeded error code (generic fallback)
    if (/content_too_long|context_length_exceeded/i.test(message)) {
        const numbersMatch = message.match(/(\d{4,})/g);
        let requested = null, limit = null;
        if (numbersMatch && numbersMatch.length >= 2) {
            const nums = numbersMatch.map(n => parseInt(n));
            requested = Math.max(...nums);
            limit = Math.min(...nums);
        }
        return {
            isOverflow: true,
            provider: 'openai',
            requested,
            limit,
            overflow: requested && limit ? requested - limit : null
        };
    }

    // =========================================================================
    // GEMINI PATTERNS
    // =========================================================================
    const geminiPatterns = [
        /input context is too long/i,
        /RESOURCE_EXHAUSTED/i,
        /payload size exceeds/i,
        /exceeds.*maximum.*tokens/i,
        /too many tokens/i,
        /context.*too (long|large)/i,
        /token.*limit.*exceeded/i
    ];

    for (const pattern of geminiPatterns) {
        if (pattern.test(message)) {
            const numbersMatch = message.match(/(\d{4,})\s*tokens?/gi);
            let requested = null;
            let limit = null;

            if (numbersMatch && numbersMatch.length >= 2) {
                const numbers = numbersMatch.map(m => parseInt(m.replace(/\D/g, '')));
                requested = Math.max(...numbers);
                limit = Math.min(...numbers);
            }

            return {
                isOverflow: true,
                provider: 'gemini',
                requested,
                limit,
                overflow: requested && limit ? requested - limit : null
            };
        }
    }

    // Generic fallback for any overflow-like error we might have missed
    const genericOverflowPatterns = [
        /context.*length.*exceed/i,
        /input.*too.*long/i,
        /prompt.*too.*long/i,
        /exceed.*context/i,
        /token.*count.*exceed/i
    ];

    for (const pattern of genericOverflowPatterns) {
        if (pattern.test(message)) {
            return {
                isOverflow: true,
                provider: 'unknown',
                requested: null,
                limit: null,
                overflow: null
            };
        }
    }

    return { isOverflow: false };
}


// ============================================================================
// DYNAMIC BUDGET CALCULATION
// ============================================================================

/**
 * Calculate token budgets dynamically based on model and current request
 * NO HARDCODED VALUES - everything derived from model config
 *
 * @param {Object} model - Model configuration with inputContextWindow, outputTokenLimit
 * @param {number} userMaxTokens - User's requested max_tokens for response
 * @returns {Object} { contextLimit, responseBuffer, availableForInput, intactBudget, historicalBudget }
 */
export function calculateBudgets(model, userMaxTokens = 2000, overflowInfo = null) {
    // Use actual context limit from overflow error if available (more accurate than model config)
    const configContextLimit = model?.inputContextWindow || 128000;
    const modelOutputLimit = model?.outputTokenLimit || 4096;

    // Response buffer = what user requested for max_tokens
    const responseBuffer = Math.min(userMaxTokens || modelOutputLimit, modelOutputLimit);

    // For Bedrock: inputTokens + max_tokens <= contextLimit
    // So available input = contextLimit - max_tokens - safety margin
    // Use totalContextLimit from overflow error if available (actual model limit)
    const totalContextLimit = overflowInfo?.totalContextLimit || configContextLimit;

    // Safety margin = 2% of context window (accounts for tokenizer variance)
    const safetyMargin = Math.floor(totalContextLimit * 0.02);

    // Available for input = total context limit - response buffer - safety margin
    // This accounts for Bedrock's formula: inputTokens + max_tokens <= contextLimit
    const availableForInput = totalContextLimit - responseBuffer - safetyMargin;

    // 60/40 split: 60% for INTACT messages, 40% for historical context extraction
    const intactBudget = Math.floor(availableForInput * 0.60);
    const historicalBudget = Math.floor(availableForInput * 0.40);

    return {
        contextLimit: totalContextLimit,
        responseBuffer,
        safetyMargin,
        availableForInput,
        intactBudget,       // Keep recent messages INTACT within this budget
        historicalBudget    // For historical context extraction responses
    };
}

/**
 * Calculate what portion of messages should become "historical context"
 * Returns the split point and token counts
 *
 * @param {Array} messages - Full message array
 * @param {Object} budgets - From calculateBudgets()
 * @returns {Object} { intactMessages, historicalMessages, intactTokens, historicalTokens }
 */
export function calculateHistoricalContextStructure(messages, budgets) {
    if (!messages || messages.length === 0) {
        return { intactMessages: [], historicalMessages: [], intactTokens: 0, historicalTokens: 0 };
    }

    const tokenCounter = createTokenCounter();

    try {
        // Work backwards from the end, keeping CONTIGUOUS messages that fit
        // We MUST keep messages contiguous to maintain conversation flow
        // (skipping messages breaks user/assistant alternation and thinking blocks)
        let intactTokens = 0;
        let intactStartIndex = messages.length; // Start with nothing intact

        for (let i = messages.length - 1; i >= 0; i--) {
            const msgTokens = tokenCounter.countTokens(messages[i].content || '');

            if (intactTokens + msgTokens <= budgets.intactBudget) {
                intactTokens += msgTokens;
                intactStartIndex = i;
            } else {
                break;
            }
        }

        // Everything before intactStartIndex becomes historical
        const historicalMessages = messages.slice(0, intactStartIndex);
        const intactMessages = messages.slice(intactStartIndex);

        // Calculate historical tokens
        const historicalTokens = historicalMessages.reduce(
            (sum, msg) => sum + tokenCounter.countTokens(msg.content || ''), 0
        );

        return {
            intactMessages,
            historicalMessages,
            intactTokens,
            historicalTokens
        };

    } finally {
        tokenCounter.free();
    }
}


// ============================================================================
// HISTORICAL CONTEXT EXTRACTION
// ============================================================================

/**
 * Extract relevant information from historical messages using LLM
 * This is called when conversation history alone causes overflow
 *
 * @param {Object} params - Request params with account info
 * @param {Array} historicalMessages - Messages to extract from
 * @param {string} currentQuestion - User's current question (for relevance)
 * @param {Object} model - Model to use for extraction
 * @param {Function} llmCallFn - Non-streaming LLM call function
 * @returns {string} Extracted relevant context
 */
export async function extractHistoricalContext(
    params,
    historicalMessages,
    currentQuestion,
    model,
    llmCallFn,
    options = {}
) {
    if (!historicalMessages || historicalMessages.length === 0) {
        return { extractedContext: '', historicalEndIndex: -1 };
    }

    if (options.skipHistoricalContext) {
        return { extractedContext: '', historicalEndIndex: -1 };
    }

    // Check if caching is safe (smart messages filtering can make it unsafe)
    const skipCache = options.smartMessagesFiltered === true;
    const conversationId = options.conversationId;
    const userId = params.user || params.accountId || params.account?.user || 'unknown';
    const totalMessageCount = options.totalMessageCount || 0;


    // NOTE: No incremental caching - extraction is QUERY-AWARE
    // Each user question needs fresh extraction of relevant content
    // Cache only stores the historical cutoff point, not the extraction content

    // CRITICAL: Extraction uses the CHEAPEST model, not the user's model
    // So ALL budget calculations must be based on the cheapest model's context window
    const cheapestModel = options.cheapestModel || model;
    const cheapestModelContextWindow = cheapestModel?.inputContextWindow || 128000;
    // Use 70% of cheapest model context for extraction (leave room for prompt overhead)
    const cheapestModelMaxChars = Math.floor(cheapestModelContextWindow * 0.7 * 4); // 70% of context, ~4 chars/token

    // Calculate user model's max chars for fallback (if message too big for cheapest but fits user model)
    // Use 70% to leave room for extraction prompt overhead
    // Use 3.5 chars/token (conservative) - some documents tokenize poorly (e.g., 1.5 chars/token for old English)
    const userModelContextWindow = model?.inputContextWindow || 128000;
    const userModelMaxChars = Math.floor(userModelContextWindow * 0.7 * 3.5);

    // The historical budget from options is based on user's model - cap it to cheapest model
    const userBudgetChars = (options.historicalBudget || 30000) * 4;
    const maxChars = Math.min(userBudgetChars, cheapestModelMaxChars);

    // Oversized threshold: messages that are too big for the combined extraction
    // These get individual extraction calls (which may use user's model if too big for cheapest)
    const oversizedThreshold = Math.floor(maxChars * 0.5); // If >50% of budget, extract individually
    const MAX_OVERSIZED_TO_PROCESS = 5;

    logger.info(`[EXTRACTION] Starting: ${historicalMessages.length} historical messages, budget ${options.historicalBudget || 30000} tokens`);
    logger.debug(`[EXTRACTION] Cheapest model: ${cheapestModel?.id || cheapestModel?.name || 'unknown'} (${cheapestModelContextWindow} tokens, max ${cheapestModelMaxChars} chars)`);
    logger.debug(`[EXTRACTION] User model: ${model?.id || model?.name || 'unknown'} (${userModelContextWindow} tokens, max ${userModelMaxChars} chars fallback)`);
    logger.debug(`[EXTRACTION] Oversized threshold: >${oversizedThreshold} chars`);

    // Reverse messages: NEWEST first, OLDEST last
    const reversedMessages = [...historicalMessages].reverse();

    // Separate oversized messages from normal ones
    // Only the 3 most recent oversized messages get individual processing
    const oversizedMessages = []; // Will process individually
    const normalMessages = []; // Will process together

    for (const msg of reversedMessages) {
        const msgLength = msg.content?.length || 0;
        // Use MD5 hash to identify/track oversized messages
        const contentHash = crypto.createHash('md5').update(msg.content || '').digest('hex').substring(0, 12);

        if (msgLength > oversizedThreshold && oversizedMessages.length < MAX_OVERSIZED_TO_PROCESS) {
            if (msgLength <= cheapestModelMaxChars) {
                oversizedMessages.push({ ...msg, contentHash, useModel: 'cheapest' });
            } else if (msgLength <= userModelMaxChars) {
                // Too big for cheapest but fits user's model - use that instead
                oversizedMessages.push({ ...msg, contentHash, useModel: 'user' });
                logger.info(`[EXTRACTION] Oversized message (${msgLength} chars) too big for cheapest model, will use user model (${model?.id || 'unknown'})`);
            } else {
                // Too big even for user's model - truncate to fit the larger model
                // Take from the END of the message (most recent content is usually most relevant)
                const maxModelChars = Math.max(cheapestModelMaxChars, userModelMaxChars);
                const truncatedContent = msg.content.slice(-maxModelChars);
                const useModel = userModelMaxChars >= cheapestModelMaxChars ? 'user' : 'cheapest';
                logger.warn(`[EXTRACTION] TRUNCATING message from ${msgLength} to ${maxModelChars} chars (keeping end) to fit ${useModel} model, hash: ${contentHash}`);
                oversizedMessages.push({
                    ...msg,
                    content: truncatedContent,
                    originalLength: msgLength,
                    contentHash,
                    useModel,
                    truncated: true
                });
            }
        } else {
            normalMessages.push(msg);
        }
    }

    if (oversizedMessages.length > 0) {
        logger.info(`[EXTRACTION] Found ${oversizedMessages.length} oversized messages`);
    }

    // Process oversized messages IN PARALLEL - query-aware (fresh extraction each time)
    // cheapestModel already defined above (used for threshold calculation)

    // Create extraction promises for all oversized messages
    const oversizedPromises = oversizedMessages.map(async (msg) => {
        // Select model based on message size - use cheapest if possible, fallback to user's model
        const extractionModelForMsg = msg.useModel === 'user' ? model : cheapestModel;
        const modelLimit = msg.useModel === 'user' ? userModelMaxChars : cheapestModelMaxChars;
        logger.debug(`[EXTRACTION] Processing oversized: ${msg.content?.length} chars, model: ${extractionModelForMsg?.id || 'unknown'}`);

        const singleMsgPrompt = `You are extracting relevant context from a conversation message to help answer the user's current question.

Message (${msg.role}):
${msg.content}

User's Current Question: ${currentQuestion}

Extract information from this message that is relevant to answering the question. Include:
- Topics and subjects discussed in this message
- Direct quotes or statements that relate to the question
- Facts, data, numbers, or specifications mentioned
- Decisions or conclusions reached
- Technical details discussed

IMPORTANT: If the user is asking whether something was discussed/mentioned, list the main topics covered in this message so the answer can be verified.

Provide the relevant excerpts and information (not a summary):`;

        const singleMsgMessages = [
            { role: 'system', content: 'You extract relevant context from conversation messages. Extract specific relevant content, not summaries.' },
            { role: 'user', content: singleMsgPrompt }
        ];


        try {
            const result = await llmCallFn(params, singleMsgMessages, extractionModelForMsg, { max_tokens: 2000 });
            const extracted = result?.content || '';

            if (extracted && !extracted.toLowerCase().includes('no relevant context')) {
                logger.info(`[EXTRACTION] Oversized ${msg.contentHash} result: ${extracted.length} chars`);
                return extracted;
            }
            logger.info(`[EXTRACTION] Oversized ${msg.contentHash}: No relevant context found`);
            return null;
        } catch (error) {
            logger.error(`[EXTRACTION] Oversized ${msg.contentHash} FAILED: ${error.message}`);
            return null;
        }
    });

    // Wait for all oversized extractions to complete in parallel
    const oversizedResults = await Promise.all(oversizedPromises);
    const oversizedExtractions = oversizedResults.filter(Boolean); // Remove nulls

    if (oversizedMessages.length > 0) {
        logger.debug(`[EXTRACTION] Oversized results: ${oversizedExtractions.length}/${oversizedMessages.length} returned content`);
    }

    // The oversized extractions REPLACE their original huge messages
    // So our budget for normal messages = total budget - oversized extraction size
    const oversizedExtractedSize = oversizedExtractions.join('\n\n---\n\n').length;
    const normalMessagesBudget = maxChars - oversizedExtractedSize;

    logger.debug(`[EXTRACTION] Budget after oversized: ${normalMessagesBudget} chars`);

    const totalNormalChars = normalMessages.reduce((sum, msg) => sum + (msg.content?.length || 0) + 20, 0);
    logger.debug(`[EXTRACTION] Normal messages: ${normalMessages.length} msgs, ${totalNormalChars} chars, fits: ${totalNormalChars <= normalMessagesBudget}`);

    // normalMessages is in NEWEST-first order, reverse to OLDEST-first for selection
    // (if we must drop, drop the newest which are closest to intact messages)
    const oldestFirstMessages = [...normalMessages].reverse();

    const includedMessages = [];
    let charCount = 0;

    for (const msg of oldestFirstMessages) {
        const msgLength = (msg.content?.length || 0) + 20;
        if (charCount + msgLength > normalMessagesBudget) break;
        includedMessages.push(msg);
        charCount += msgLength;
    }

    const droppedCount = normalMessages.length - includedMessages.length;
    logger.debug(`[EXTRACTION] Normal: ${includedMessages.length}/${normalMessages.length} included, ${droppedCount} dropped`);

    if (droppedCount > 0) {
        const droppedMessages = oldestFirstMessages.slice(includedMessages.length);
        logger.debug(`[EXTRACTION] Dropped ${droppedCount} messages:`);
        droppedMessages.forEach((msg, idx) => {
            logger.debug(`[EXTRACTION]   Dropped: ${msg.role}, ${msg.content?.length || 0} chars`);
        });
    }


    // Build the content string in chronological order (includedMessages is already oldest-first)
    const historicalContent = includedMessages.map(msg =>
        `[${msg.role}]: ${msg.content}`
    ).join('\n\n');

    logger.debug(`[EXTRACTION] Historical content: ${historicalContent.length} chars`);

    // If we have no normal messages and no oversized extractions, skip LLM call
    if (!historicalContent && oversizedExtractions.length === 0) {
        logger.debug('[EXTRACTION] No historical content, skipping');
        return { extractedContext: '', historicalEndIndex: historicalMessages.length - 1 };
    }

    // If we only have oversized extractions and no normal messages, combine and return
    if (!historicalContent && oversizedExtractions.length > 0) {
        const combinedOversized = oversizedExtractions.join('\n\n---\n\n');
        logger.info(`[EXTRACTION] Complete: ${combinedOversized.length} chars from oversized only`);
        return { extractedContext: combinedOversized, historicalEndIndex: historicalMessages.length - 1 };
    }

    // Query-aware extraction for normal messages
    let extractionPrompt;
    const oversizedContext = oversizedExtractions.join('\n\n---\n\n');

    if (oversizedContext) {
        // Combine oversized extractions + normal messages
        extractionPrompt = `This is historical conversation content that no longer fits in the context window.

Previously Extracted Context (from large messages):
${oversizedContext}

Additional Conversation Messages:
${historicalContent}

The user is now asking: "${currentQuestion}"

Your task: Extract information from this history that helps answer their question or provides context they need.

IMPORTANT: If the user is asking whether something was discussed/mentioned (a yes/no question about topics), you MUST list the topics that WERE discussed so the answer can be verified. For example, if asked "did we discuss X?", list what topics were actually covered.

Include:
- Topics and subjects that were discussed in this conversation
- Any prior work, code, or solutions related to their question
- Relevant facts, decisions, or conclusions from the conversation
- Context they would need to continue the conversation

When uncertain whether something is relevant, err on the side of including it.`;
    } else {
        // Full extraction from normal messages only
        extractionPrompt = `This is historical conversation content that no longer fits in the context window.

Conversation History:
${historicalContent}

The user is now asking: "${currentQuestion}"

Your task: Extract information from this history that helps answer their question or provides context they need.

IMPORTANT: If the user is asking whether something was discussed/mentioned (a yes/no question about topics), you MUST list the topics that WERE discussed so the answer can be verified. For example, if asked "did we discuss X?", list what topics were actually covered.

Include:
- Topics and subjects that were discussed in this conversation
- Any prior work, code, or solutions related to their question
- Relevant facts, decisions, or conclusions from the conversation
- Context they would need to continue the conversation

When uncertain whether something is relevant, err on the side of including it.`;
    }

    const extractionMessages = [
        { role: 'system', content: 'You extract relevant context from conversation history. For questions asking whether something was discussed, always list what topics WERE discussed so the answer can be verified. When uncertain, include rather than omit.' },
        { role: 'user', content: extractionPrompt }
    ];

    try {
        // Use cheapest model for normal messages extraction (cost optimization)
        const extractionModel = options.cheapestModel || model;

        const result = await llmCallFn(params, extractionMessages, extractionModel, { max_tokens: 6000 });
        const extractedContent = result?.content || '';
        const historicalEndIndex = historicalMessages.length - 1;

        logger.info(`[EXTRACTION] Complete: ${extractedContent.length} chars extracted`);

        if (!skipCache && conversationId) {
            const modelId = model?.id || model?.name || 'unknown';
            await CacheManager.setCachedHistoricalContext(userId, conversationId, {
                historicalEndIndex,
                extractedContext: extractedContent,
                messageCount: totalMessageCount,
                modelId
            });
        }

        return { extractedContext: extractedContent, historicalEndIndex };

    } catch (error) {
        logger.error('Historical context extraction failed:', error.message);
        // Fallback: just take the last part of historical content
        return {
            extractedContext: historicalContent.slice(-2000),
            historicalEndIndex: historicalMessages.length - 1
        };
    }
}

/**
 * Build final messages array with historical context injected
 *
 * Historical context is inserted at the START (after system prompt) to maintain
 * chronological order - it's older context, so it should come before recent messages.
 *
 * @param {Array} intactMessages - Messages to keep intact
 * @param {string} historicalContext - Extracted context from historical messages
 * @returns {Array} Final messages array
 */
export function buildMessagesWithHistoricalContext(intactMessages, historicalContext, currentQuestion = '') {
    if (!historicalContext || historicalContext.trim().length === 0) {
        return intactMessages;
    }

    // Detect if user is asking about conversation history/topics
    const historyQuestionPatterns = [
        /what.*(topic|subject|discuss|talk|mention|cover)/i,
        /have we.*(discuss|talk|mention|cover)/i,
        /did we.*(discuss|talk|mention)/i,
        /was.*(mention|discuss|talk)/i,
        /were.*(mention|discuss|talk)/i,
        /earlier.*(conversation|chat)/i,
        /previous.*(conversation|message)/i,
        /conversation history/i,
        /what have we/i,
        /do you (see|remember|recall|know).*(any|about)/i,
        /any.*(stuff|content|info|information)/i,
        /is there.*(any|mention)/i
    ];
    const isHistoryQuestion = historyQuestionPatterns.some(p => p.test(currentQuestion));

    logger.debug(`[OVERFLOW] Building context msg, isHistoryQuestion: ${isHistoryQuestion}`);

    // Use stronger header and system role for history questions to ensure the extracted context isn't ignored
    // System messages have higher priority than user/assistant messages in attention
    const header = isHistoryQuestion
        ? `CRITICAL INSTRUCTION: The following is a summary of topics discussed EARLIER in this conversation (before the large document below). When the user asks about conversation history or what was discussed, you MUST reference this information:`
        : `Relevant Historical Context from earlier in this conversation:`;

    const role = isHistoryQuestion ? 'system' : 'assistant';


    // Insert historical context - use system role for history questions to give it priority
    const contextMessage = {
        role,
        content: `-------\n${header}\n\n${historicalContext}\n-------`
    };

    // Find where to insert: after system message(s), before conversation messages
    // This maintains chronological order: historical context is OLDER, so it goes first
    let insertIndex = 0;
    for (let i = 0; i < intactMessages.length; i++) {
        if (intactMessages[i].role === 'system') {
            insertIndex = i + 1;
        } else {
            break;
        }
    }

    return [
        ...intactMessages.slice(0, insertIndex),
        contextMessage,
        ...intactMessages.slice(insertIndex)
    ];
}


// ============================================================================
// RECOVERY TRACKING (ONE-STRIKE SYSTEM)
// ============================================================================

/**
 * Track recovery attempts - we only get ONE chance
 */
function trackRecoveryAttempt(requestId) {
    // Clean old entries
    const now = Date.now();
    for (const [key, entry] of recoveryAttempts.entries()) {
        if (now - entry.timestamp > RECOVERY_ATTEMPT_TTL) {
            recoveryAttempts.delete(key);
        }
    }

    const existing = recoveryAttempts.get(requestId);
    if (existing) {
        existing.attempts++;
        existing.timestamp = now;
        return existing.attempts;
    }

    recoveryAttempts.set(requestId, { attempts: 1, timestamp: now });
    return 1;
}

/**
 * Clear recovery tracking for a request (call on success)
 */
function clearRecoveryTracking(requestId) {
    recoveryAttempts.delete(requestId);
}

/**
 * Check if we should critical log an overflow error
 * First overflow = recovery opportunity, don't log
 * Second overflow (after recovery fails) = real failure, log it
 *
 * @param {string} requestId
 * @returns {boolean}
 */
export function shouldCriticalLogOverflow(requestId) {
    const entry = recoveryAttempts.get(requestId);
    return entry && entry.attempts >= 1;
}


// ============================================================================
// MAIN RECOVERY ORCHESTRATION
// ============================================================================

/**
 * Handle context overflow with recovery
 *
 * This is the main entry point called when an overflow is detected.
 * Recovery strategy: Extract historical context from long conversations
 *
 * NOTE: Data source contexts are handled PROACTIVELY in chatWithData (85% threshold).
 * If we get here, the overflow is from conversation history, not contexts.
 *
 * @param {Object} options
 * @param {Error} options.error - The overflow error
 * @param {Object} options.params - Original request params
 * @param {Array} options.messages - Original messages
 * @param {Object} options.model - Model configuration
 * @param {Stream} options.responseStream - Response stream (for status updates)
 * @param {Function} options.llmCallFn - Function to call LLM (streaming)
 * @param {Function} options.internalLLMCallFn - Function to call LLM (non-streaming, for extraction)
 * @param {Object} options.llmOptions - Original options passed to LLM call
 * @returns {Object} { success, result, strategy }
 */
export async function handleContextOverflow({
    error,
    params,
    messages,
    model,
    responseStream,
    llmCallFn,
    internalLLMCallFn,
    llmOptions = {}
}) {
    logger.info(`[OVERFLOW] Recovery triggered`);

    const overflowInfo = detectContextOverflow(error);
    const requestId = params?.options?.requestId || 'unknown';

    if (!overflowInfo.isOverflow) {
        throw error;
    }

    // Track recovery attempt - ONE chance only
    const attemptNum = trackRecoveryAttempt(requestId);

    if (attemptNum > 1) {
        logger.error(`Context overflow: Recovery already attempted, giving up`);
        throw error;
    }

    logger.info(`[OVERFLOW] ${messages?.length} msgs, ${overflowInfo.requested} > ${overflowInfo.limit} tokens (${overflowInfo.provider})`);

    // Send status to user
    const recoveryStatus = newStatus({
        inProgress: true,
        sticky: false,
        message: "Analyzing conversation context...",
        icon: "bolt"
    });

    if (responseStream && !responseStream.writableEnded) {
        sendStatusEventToStream(responseStream, recoveryStatus);
        forceFlush(responseStream);
    }

    // Calculate budgets dynamically based on model and overflow info
    // Pass overflowInfo to use actual context limit from error (more accurate)
    const budgets = calculateBudgets(model, llmOptions.max_tokens, overflowInfo);

    logger.debug(`[OVERFLOW] Budget (contextLimit: ${overflowInfo.totalContextLimit || 'N/A'})`);
    logger.debug(`[OVERFLOW]   intact: ${budgets.intactBudget}, historical: ${budgets.historicalBudget}`);

    let strategy = null;
    let recoveredMessages = messages;

    // =====================================================
    // RECOVERY: Extract historical context from long conversations
    // =====================================================
    const structure = calculateHistoricalContextStructure(messages, budgets);

    logger.info(`[OVERFLOW] Split: ${structure.historicalMessages.length} historical, ${structure.intactMessages.length} intact`);

    if (structure.historicalMessages.length > 0) {

        // Get the current question - prefer from intact messages, fallback to last message in original array
        // This handles the edge case where the last message itself is too large to be intact
        let currentQuestion = structure.intactMessages[structure.intactMessages.length - 1]?.content || '';
        if (!currentQuestion && messages.length > 0) {
            // Last message is too big to be intact - it's likely a large document paste
            // Use a generic context-preserving question since we can't know what they'll ask
            const lastMsg = messages[messages.length - 1];
            const truncatedContent = lastMsg?.content?.substring(0, 500) || '';
            currentQuestion = `The user has shared a large document. Preserve any relevant context from the conversation history that might help discuss or analyze content like: "${truncatedContent}..."`;
            logger.info(`Using generic extraction prompt (last msg was ${lastMsg?.content?.length} chars document)`);
        }

        try {
            // Pass totalMessageCount for cache storage, historicalBudget for truncation, and cheapestModel for cost optimization
            const cheapestModel = params.options?.cheapestModel || llmOptions.cheapestModel || model;
            const extractionOptions = {
                ...llmOptions,
                totalMessageCount: messages.length,
                historicalBudget: budgets.historicalBudget,
                cheapestModel
            };

            const extractionResult = await extractHistoricalContext(
                params,
                structure.historicalMessages,
                currentQuestion,
                model,
                internalLLMCallFn || llmCallFn,
                extractionOptions
            );

            // Build recovered messages with historical context
            let intactForRecovery = structure.intactMessages;

            // If NO intact messages (last message was too big), we need to include the last user message
            // Extract/summarize it since it won't fit as-is
            if (intactForRecovery.length === 0 && messages.length > 0) {
                const lastMsg = messages[messages.length - 1];
                if (lastMsg.role === 'user') {
                    // The user's message is huge - include the extraction as context and
                    // add a truncated version of their message as the "question"
                    const truncatedContent = lastMsg.content?.substring(0, 10000) || '';
                    intactForRecovery = [{
                        role: 'user',
                        content: `[Document/message truncated for context limit - first 10000 chars shown]\n\n${truncatedContent}\n\n[...content truncated...]`
                    }];
                    logger.info(`Last message (${lastMsg.content?.length} chars) truncated to 10000 chars for recovery`);
                }
            }

            recoveredMessages = buildMessagesWithHistoricalContext(
                intactForRecovery,
                extractionResult.extractedContext,
                currentQuestion
            );

            strategy = 'historical_extraction';
            logger.info(`[OVERFLOW] Extracted ${extractionResult.extractedContext?.length || 0} chars, ${recoveredMessages.length} recovered msgs`);

        } catch (extractError) {
            logger.error('Historical context extraction failed:', extractError.message);
            // Fallback: just use intact messages without extraction
            recoveredMessages = structure.intactMessages;
            strategy = 'simple_truncation';
        }
    } else {
        // Edge case: conversation is short but still overflowed (single huge message?)
        logger.warn('Overflow with no historical messages to extract - cannot recover');
        strategy = 'no_recovery_possible';
    }

    // Clear status
    if (responseStream && !responseStream.writableEnded) {
        recoveryStatus.inProgress = false;
        sendStatusEventToStream(responseStream, recoveryStatus);
        forceFlush(responseStream);
    }

    // Retry with recovered messages
    if (!recoveredMessages || recoveredMessages.length === 0) {
        logger.error('Recovery produced no messages');
        throw error;
    }

    logger.info(`[OVERFLOW] Retrying with ${recoveredMessages.length} messages (${strategy})`);

    try {
        // IMPORTANT: Disable extended thinking for recovery retry
        // The historical context message we inject doesn't have thinking blocks,
        // which would cause validation errors with extended thinking enabled
        const retryOptions = {
            ...llmOptions,
            disableReasoning: true
        };
        const result = await llmCallFn(params, recoveredMessages, responseStream, retryOptions);

        // Success! Clear recovery tracking
        clearRecoveryTracking(requestId);

        logger.info(`[OVERFLOW] Recovery succeeded (${strategy})`);

        return {
            success: true,
            result,
            strategy,
            overflowInfo
        };

    } catch (retryError) {
        const retryOverflow = detectContextOverflow(retryError);

        logger.error(`[OVERFLOW] Recovery failed: ${retryError?.message?.substring(0, 100)}`);
        if (retryOverflow.isOverflow) retryError.recoveryFailed = true;

        throw retryError;
    }
}


// ============================================================================
// FAIL-FIRST WRAPPER (for UnifiedLLMClient integration)
// ============================================================================

/**
 * Wrapper that implements fail-first approach
 * Send request directly, catch overflow, recover
 *
 * ZERO overhead for normal users - only kicks in on actual overflow
 *
 * @param {Function} llmCallFn - The actual LLM call function to wrap
 * @param {Object} params - Request params
 * @param {Array} messages - Messages to send
 * @param {Stream} responseStream - Response stream
 * @param {Object} options - LLM options
 * @param {Object} overflowOptions - { model, internalLLMCallFn }
 * @returns {Object} LLM result
 */
export async function callWithOverflowRecovery(
    llmCallFn,
    params,
    messages,
    responseStream,
    options = {},
    overflowOptions = {}
) {
    const { model, internalLLMCallFn } = overflowOptions;

    try {
        // FAIL-FIRST: Just send it directly, no pre-checking
        return await llmCallFn(params, messages, responseStream, options);

    } catch (error) {
        const overflowInfo = detectContextOverflow(error);

        if (!overflowInfo.isOverflow) {
            throw error;
        }

        // Overflow detected - attempt recovery
        logger.info('Overflow detected, initiating recovery...');

        const recovery = await handleContextOverflow({
            error,
            params,
            messages,
            model,
            responseStream,
            llmCallFn,
            internalLLMCallFn,
            llmOptions: options
        });

        if (recovery.success) {
            return recovery.result;
        }

        throw error;
    }
}
