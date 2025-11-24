//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas
import { sendStatusEventToStream } from "../../streams.js";
import { newStatus } from "../../status.js";
// for all Claude models  Not In Use
export const claudeTransform = (event) => {
    if(event && event.d && event.d.delta && event.d.delta.text) {
        return {d: event.d.delta.text}
    }
    else {
        return null;
    }
}


// for Mistral 7b and Mixtral 7x8b    Not In Use
export const mistralTransform = (event) => { 
    if(event && event.d) { 
        return event
    }
    else {
        return null;
    }
}


export const bedrockConverseTransform = (event, responseStream = null) => { 
    if (event && event.d && event.d.delta && event.d.delta.text) { 
        return {d: event.d.delta.text}
    } else if (responseStream && event && event.d && event.d.delta && event.d.delta.reasoningContent.text && event.d.delta.reasoningContent.text) {
        const reasoning = event.d.delta.reasoningContent.text;
        sendStatusEventToStream(responseStream, newStatus( {id: "reasoning", summary: "Thinking Details:", message: reasoning, icon: "bolt", inProgress: true, animated: true} ));
    } else if (event && event.d && event.d && event.d.stopReason) {
        switch (event.d.stopReason) {
            case "content_filtered":
                console.log("Bedrock Content_filtered Stop Reason");
                return {d: "\nYour request was blocked by an AWS content filter."}
            case "guardrail_intervened":
                console.log("Bedrock Guardrail_intervened Stop Reason");
                return {d: "\nYour request was blocked by your organization's guardrails."}
            default:
                return null;
        }
    } else {
        return null;
    }
}

export const bedrockTokenUsageTransform = (event) => {
    if (event && event.d && event.d.usage) {
        return event.d.usage;
    } else if (event && event.usage) {
        const usage = event.usage;
        // Calculate total cached tokens from both input cached fields
        usage.cached_tokens = (usage.inputCachedTokens || 0) + (usage.inputWriteCachedTokens || 0);
        return usage;
    } else {
        return null;
    }
}