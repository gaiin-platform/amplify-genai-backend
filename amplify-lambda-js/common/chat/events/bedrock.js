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


export const bedrockConverseTransform = (event, responseStream = null, capturedContent = null) => {
    // Bedrock ConverseStream sends events with nested structure:
    // - contentBlockDelta: { delta: { text: "..." } } for text
    // - contentBlockStart: { start: { toolUse: { toolUseId, name } } } for tool start
    // - contentBlockDelta: { delta: { toolUse: { input: "..." } } } for tool input
    // - contentBlockStop: {} for block end
    // - messageStop: { stopReason: "..." } for message end

    // Handle text delta - check both direct delta (old format) and nested contentBlockDelta (new format)
    if (event && event.contentBlockDelta && event.contentBlockDelta.delta) {
        const delta = event.contentBlockDelta.delta;

        // Text content
        if (delta.text) {
            return delta.text;
        }

        // Reasoning content (Claude's thinking)
        if (responseStream && delta.reasoningContent && delta.reasoningContent.text) {
            const reasoning = delta.reasoningContent.text;
            sendStatusEventToStream(responseStream, newStatus({
                id: "reasoning",
                summary: "Thinking Details:",
                message: reasoning,
                icon: "bolt",
                inProgress: true,
                animated: true
            }));
            return null;
        }

        // Tool use input delta - accumulate the JSON input
        if (delta.toolUse) {
            const inputChunk = delta.toolUse.input || '';
            console.log(`🔧 contentBlockDelta.toolUse input: "${inputChunk}"`);
            if (capturedContent && capturedContent.currentToolCall) {
                capturedContent.currentToolCall.function.arguments += inputChunk;
            } else {
                console.log('🔧 WARNING: No currentToolCall to accumulate input');
            }
            return null;
        }
    }

    // Fallback for direct delta format (actual Bedrock format without contentBlockDelta wrapper)
    if (event && event.delta) {
        // Text content
        if (event.delta.text) {
            return event.delta.text;
        }

        // Reasoning content (Claude's thinking)
        if (responseStream && event.delta.reasoningContent && event.delta.reasoningContent.text) {
            const reasoning = event.delta.reasoningContent.text;
            sendStatusEventToStream(responseStream, newStatus({
                id: "reasoning",
                summary: "Thinking Details:",
                message: reasoning,
                icon: "bolt",
                inProgress: true,
                animated: true
            }));
            return null;
        }

        // Tool use input delta - accumulate the JSON input (direct format)
        if (event.delta.toolUse) {
            const inputChunk = event.delta.toolUse.input || '';
            console.log(`🔧 direct delta.toolUse input: "${inputChunk}"`);
            if (capturedContent && capturedContent.currentToolCall) {
                capturedContent.currentToolCall.function.arguments += inputChunk;
            } else {
                console.log('🔧 WARNING: No currentToolCall to accumulate input (direct format)');
            }
            return null;
        }
    }

    // Handle message stop - check both direct stopReason and nested messageStop
    const stopReason = event?.messageStop?.stopReason || event?.stopReason;
    if (stopReason) {
        switch (stopReason) {
            case "content_filtered":
                console.log("Bedrock Content_filtered Stop Reason");
                return "\nYour request was blocked by an AWS content filter.";
            case "guardrail_intervened":
                console.log("Bedrock Guardrail_intervened Stop Reason");
                return "\nYour request was blocked by your organization's guardrails.";
            case "tool_use":
                // Tool use stop reason - finalize any pending tool call
                console.log("Bedrock tool_use Stop Reason - model wants to use tools");
                if (capturedContent) {
                    // Finalize any pending tool call (since Bedrock may not send contentBlockStop)
                    if (capturedContent.currentToolCall) {
                        console.log(`🔧 Finalizing tool call on stop: ${JSON.stringify(capturedContent.currentToolCall)}`);
                        if (!capturedContent.toolCalls) capturedContent.toolCalls = [];
                        capturedContent.toolCalls.push(capturedContent.currentToolCall);
                        capturedContent.currentToolCall = null;
                    }
                    console.log(`🔧 Tool calls captured: ${capturedContent.toolCalls?.length || 0}`);
                }
                return null;
            default:
                return null;
        }
    }

    // Handle content block start - check for tool use start
    // Nested format: { contentBlockStart: { contentBlockIndex: N, start: { toolUse: { toolUseId, name } } } }
    // Direct format: { contentBlockIndex: N, start: { toolUse: { toolUseId, name } } }
    const toolUseStart = event?.contentBlockStart?.start?.toolUse ||
                         event?.contentBlockStart?.toolUse ||
                         event?.start?.toolUse;  // Direct format (actual Bedrock format)
    if (toolUseStart) {
        console.log(`🔧 toolUse start detected: id=${toolUseStart.toolUseId}, name=${toolUseStart.name}`);
        if (capturedContent) {
            if (!capturedContent.toolCalls) capturedContent.toolCalls = [];
            capturedContent.currentToolCall = {
                id: toolUseStart.toolUseId,
                type: 'function',
                function: {
                    name: toolUseStart.name,
                    arguments: ''
                }
            };
            console.log(`🔧 Initialized currentToolCall: ${JSON.stringify(capturedContent.currentToolCall)}`);
        } else {
            console.log('🔧 WARNING: capturedContent is null, cannot capture tool call');
        }
        return null;
    }

    // Handle content block stop
    if (event && event.contentBlockStop !== undefined) {
        console.log(`🔧 contentBlockStop event, currentToolCall exists: ${!!capturedContent?.currentToolCall}`);
        if (capturedContent && capturedContent.currentToolCall) {
            // End of content block - if we were building a tool call, finalize it
            console.log(`🔧 Finalizing tool call: ${JSON.stringify(capturedContent.currentToolCall)}`);
            capturedContent.toolCalls.push(capturedContent.currentToolCall);
            capturedContent.currentToolCall = null;
            console.log(`🔧 Tool calls count now: ${capturedContent.toolCalls.length}`);
        }
        return null;
    }

    return null;
}

export const bedrockTokenUsageTransform = (event) => {
    // Bedrock ConverseStream sends metadata as: { metadata: { usage: {...} } }
    // Also handle legacy formats for backward compatibility

    // Check for nested metadata.usage (Bedrock ConverseStream format)
    if (event && event.metadata && event.metadata.usage) {
        const usage = { ...event.metadata.usage };

        // Extract cached tokens from Claude/Anthropic format
        usage.inputCachedTokens = usage.cache_read_input_tokens || 0;
        usage.inputWriteCachedTokens = usage.cache_creation_input_tokens || 0;

        // Convert Bedrock camelCase to standard format
        usage.prompt_tokens = usage.inputTokens || usage.input_tokens || 0;
        usage.completion_tokens = usage.outputTokens || usage.output_tokens || 0;

        return usage;
    }

    // Legacy format: event.d.usage
    if (event && event.d && event.d.usage) {
        const usage = event.d.usage;
        usage.prompt_tokens = usage.inputTokens || 0;
        usage.completion_tokens = usage.outputTokens || 0;
        return usage;
    }

    // Direct usage format: event.usage
    if (event && event.usage) {
        const usage = event.usage;

        // Extract cached tokens from Claude/Anthropic format
        usage.inputCachedTokens = usage.cache_read_input_tokens || 0;
        usage.inputWriteCachedTokens = usage.cache_creation_input_tokens || 0;

        // Convert Bedrock snake_case to standard format
        usage.prompt_tokens = usage.inputTokens || usage.input_tokens || 0;
        usage.completion_tokens = usage.outputTokens || usage.output_tokens || 0;

        return usage;
    }

    return null;
}