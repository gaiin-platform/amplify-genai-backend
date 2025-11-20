//Copyright (c) 2024 Vanderbilt University  
//Authors: Nahuel Pereira (FortyAU)
import { sendStatusEventToStream } from "../../streams.js";
import { newStatus } from "../../status.js";
import { getLogger } from "../../logging.js";

const logger = getLogger("gemini-events");

export const geminiTransform = (event, _responseStream = null) => {
    try {
        // Handle Gemini OpenAI compatibility format directly
        if (event && event.choices && event.choices.length > 0) {
            const choice = event.choices[0];
            
            // Handle thinking/reasoning content from LLM
            if (choice && choice.delta?.content  && choice.delta?.extra_content?.google?.thought && _responseStream) {
                const thought = choice.delta?.content.replace("<thought>", "").replace("</thought>", "");
                sendStatusEventToStream(_responseStream, newStatus({
                    id: "reasoning",
                    summary: "Thinking Details:",
                    message: thought,
                    icon: "bolt",
                    inProgress: true,
                    animated: true
                }));
                return null; // Don't return thought as regular content
            }
            
            // Handle regular streaming delta content
            if (choice.delta && choice.delta.content) {
                return choice.delta.content; // Return raw text, sendDeltaToStream will wrap it
            }
            
            // Handle complete message format
            if (choice.message && choice.message.content) {
                return choice.message.content; // Return raw text
            }
            
            // Handle tool calls
            if (choice.delta && choice.delta.tool_calls) {
                return { tool_calls: choice.delta.tool_calls }; // Keep object format for tool calls
            }
        }
        
        // For Gemini API native format (non-OpenAI compatibility)
        if (event && event.candidates && event.candidates.length > 0) {
            if (event.candidates[0].content && event.candidates[0].content.parts) {
                const parts = event.candidates[0].content.parts;
                // Get text content from parts
                for (const part of parts) {
                    if (part.text) {
                        return part.text; // Return raw text
                    }
                }
            }
        }
        
        // Unrecognized Gemini event format
        
    } catch (err) {
        logger.error("Error in geminiTransform:", err);
    }
    
    return null;
}

export const geminiUsageTransform = (event) => {
    // Handle Gemini usage format directly
    if (event && event.usage) {
        const usage = {
            prompt_tokens: event.usage.prompt_tokens || 0,
            completion_tokens: event.usage.completion_tokens || 0,
            total_tokens: event.usage.total_tokens || 0
        };
        
        // Extract Gemini cached tokens (OpenAI compatibility format)
        // Use inputCachedTokens if already present, otherwise extract from prompt_tokens_details.cached_tokens
        usage.inputCachedTokens = event.usage.inputCachedTokens ?? 
                                 event.usage.prompt_tokens_details?.cached_tokens ?? 0;
        return usage;
    }
    
    return null;
} 