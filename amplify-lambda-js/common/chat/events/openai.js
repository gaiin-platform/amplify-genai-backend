//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { sendStatusEventToStream } from "../../streams.js";
import { newStatus } from "../../status.js";

export const openAiTransform = (event, responseStream = null) => {
    // Handle new /responses endpoint format
    if (event && event.type) {
        // Handle streaming reasoning text deltas
        if ((event.type === "response.reasoning_summary_text.delta" && event.delta)) {
            if (responseStream) {
                // Send reasoning text as it streams (frontend should accumulate)
                sendStatusEventToStream(responseStream, newStatus({
                    id: "reasoning", 
                    summary: "Thinking Details:", 
                    message: event.delta, 
                    icon: "bolt", 
                    inProgress: true, 
                    animated: true,
                }));
            }
            return null; // Don't send this as content
        }
        
        // Handle text delta from assistant response
        if (event.type === "response.output_text.delta" && event.delta) {
            return event.delta;  // Return raw text, sendDeltaToStream will wrap it
        }
        
    }
    
    // Handle legacy completions endpoint format
    if (event && event.choices && event.choices.length > 0) {
        if (event.choices[0].delta && event.choices[0].delta.tool_calls){
            const calls = event.choices[0].delta.tool_calls;
            return {tool_calls:calls};  // Return raw object, sendDeltaToStream will wrap it
        }
        else if (event.choices[0].delta && event.choices[0].delta.content) {
            return event.choices[0].delta.content;  // Return raw text, sendDeltaToStream will wrap it
        } else if (event.choices[0].message && event.choices[0].message.content) {
            return event.choices[0].message.content;  // Return raw text, sendDeltaToStream will wrap it
        } 
    } else if (event && event.d && event.d.delta && event.d.delta.text) { // for error message
        return event.d.delta.text;  // Return raw text, sendDeltaToStream will wrap it
    }

    return null;
    
}

export const openaiUsageTransform = (event) => {
    if (event.usage) {
        const usage = event.usage;
        
        // Handle new /responses endpoint format
        if (usage.output_tokens !== undefined && usage.input_tokens !== undefined) {
            // Convert to legacy format for compatibility
            usage.prompt_tokens = usage.input_tokens;
            usage.completion_tokens = usage.output_tokens;
            // Add reasoning tokens if present
            usage.completion_tokens += usage.output_tokens_details?.reasoning_tokens ?? 0;
            // Extract reasoning tokens for separate tracking
            usage.reasoning_tokens = usage.output_tokens_details?.reasoning_tokens ?? 0;
        } else {
            // Handle legacy completions endpoint format
            const reasoningTokens = usage.reasoning_tokens ?? 0;
            usage.completion_tokens += reasoningTokens;
            // Extract reasoning tokens for separate tracking
            usage.reasoning_tokens = reasoningTokens;
        }
        
        // Extract cached tokens from OpenAI format
        // Input cached tokens: from prompt_tokens_details.cached_tokens
        usage.inputCachedTokens = (usage.inputCachedTokens ?? usage.prompt_tokens_details?.cached_tokens) ?? 0;
        
        return usage;
    } else {
        return null;
    }
}