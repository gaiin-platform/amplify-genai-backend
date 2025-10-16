//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import tiktokenModel from '@dqbd/tiktoken/encoders/cl100k_base.json' with {type: 'json'};
import { Tiktoken } from '@dqbd/tiktoken/lite';

// Global encoder instance - reused across all requests
let globalEncoder = null;

// Token count cache
const tokenCountCache = new Map();
const TOKEN_CACHE_TTL = 60 * 60 * 1000; // 1 hour


export const createTokenCounter = () => {
    // Reuse global encoder instance
    if (!globalEncoder) {
        globalEncoder = new Tiktoken(
            tiktokenModel.bpe_ranks,
            tiktokenModel.special_tokens,
            tiktokenModel.pat_str,
        );
    }
    const encoding = globalEncoder;

    return {
        countTokens: (text) => {
            if(!text) {
                return 0;
            }

            // Check cache first
            const cacheKey = `${text.substring(0, 100)}:${text.length}`;
            const cached = tokenCountCache.get(cacheKey);
            if (cached && (Date.now() - cached.timestamp) < TOKEN_CACHE_TTL) {
                return cached.count;
            }

            try {
                const tokens = encoding.encode(text);
                const count = tokens.length;
                
                // Cache the result
                tokenCountCache.set(cacheKey, {
                    count,
                    timestamp: Date.now()
                });
                
                // Clean up old cache entries if too many
                if (tokenCountCache.size > 10000) {
                    const now = Date.now();
                    for (const [key, value] of tokenCountCache.entries()) {
                        if (now - value.timestamp > TOKEN_CACHE_TTL) {
                            tokenCountCache.delete(key);
                        }
                    }
                }
                
                return count;
            } catch (e) {
                console.error("Uncountable token text: ", text);
                console.error("Error counting tokens: ", e);
                return 0;
            }
        },
        countMessageTokens: (messages) => {
            const counts = messages.map(m => encoding.encode(m.content ?? '').length);
            const count = counts.reduce((accumulator, currentValue) => accumulator + currentValue, 0);
            return count;
        },
        free: () => {
            // Don't free the global encoder - keep it alive for reuse
            // encoding.free();
        }
    };
}

export const countTokens = (text) => {
    const encoding = new Tiktoken(
        tiktokenModel.bpe_ranks,
        tiktokenModel.special_tokens,
        tiktokenModel.pat_str,
    );

    const tokens = encoding.encode(text);

    encoding.free();

    return tokens.length;
}

export const countChatTokens = (messages) => {
    const encoding = new Tiktoken(
        tiktokenModel.bpe_ranks,
        tiktokenModel.special_tokens,
        tiktokenModel.pat_str,
    );

    const counts = messages.map(m => encoding.encode(m.content).length);
    const count = counts.reduce((accumulator, currentValue) => accumulator + currentValue, 0);

    encoding.free();

    return count;
}