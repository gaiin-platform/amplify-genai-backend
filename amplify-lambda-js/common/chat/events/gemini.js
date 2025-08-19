//Copyright (c) 2024 Vanderbilt University  
//Authors: Nahuel Pereira (FortyAU)
import { openAiTransform, openaiUsageTransform } from "../events/openai.js";
import { getLogger } from "../../logging.js";

const logger = getLogger("gemini-events");

export const geminiTransform = (event, responseStream = null) => {
    // First try the OpenAI transform since Gemini's OpenAI compatibility mode has a similar format
    const openAiResult = openAiTransform(event, responseStream);
    if (openAiResult) {
        return openAiResult;
    }
    
    // If OpenAI transform didn't work, try Gemini-specific formats
    try {
        // For Gemini API native format
        if (event && event.candidates && event.candidates.length > 0) {
            if (event.candidates[0].content && event.candidates[0].content.parts) {
                const parts = event.candidates[0].content.parts;
                // Get text content from parts
                for (const part of parts) {
                    if (part.text) {
                        return { d: part.text };
                    }
                }
            }
        }
        
        // For Gemini in compatibility mode with complete response
        if (event && event.choices && event.choices.length > 0) {
            if (event.choices[0].message && event.choices[0].message.content) {
                return { d: event.choices[0].message.content };
            }
        }
        
        // Log if we couldn't match the event format
        if (!openAiResult) {
            logger.debug("Unrecognized Gemini event format:", JSON.stringify(event).substring(0, 200) + "...");
        }
    } catch (err) {
        logger.error("Error in geminiTransform:", err);
    }
    
    return openAiResult;
}

export const geminiUsageTransform = (event) => {
    // Try the OpenAI usage transform first
    const openAiUsage = openaiUsageTransform(event);
    if (openAiUsage) {
        return openAiUsage;
    }
    
    // Handle Gemini-specific usage format if present
    if (event && event.usage) {
        return {
            prompt_tokens: event.usage.prompt_tokens || 0,
            completion_tokens: event.usage.completion_tokens || 0,
            total_tokens: event.usage.total_tokens || 0
        };
    }
    
    return null;
} 