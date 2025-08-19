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
            return {d: event.delta};
        }
        
    }
    
    // Handle legacy completions endpoint format
    if (event && event.choices && event.choices.length > 0) {
        if (event.choices[0].delta && event.choices[0].delta.tool_calls){
            const calls = event.choices[0].delta.tool_calls;
            return {d: {tool_calls:calls}};
        }
        else if (event.choices[0].delta && event.choices[0].delta.content) {
            return {d: event.choices[0].delta.content};
        } else if (event.choices[0].message && event.choices[0].message.content) {
            return {d: event.choices[0].message.content};
        } 
    } else if (event && event.d && event.d.delta && event.d.delta.text) { // for error message
        return {d: event.d.delta.text}
    }
    console.log("----NO MATCH---", event , "\n\n")
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
        } else {
            // Handle legacy completions endpoint format
            usage.completion_tokens += usage.completion_tokens_details?.reasoning_tokens ?? 0;
        }
        
        return usage;
    } else {
        return null;
    }
}