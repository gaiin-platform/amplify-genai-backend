// Copyright (c) 2024 Vanderbilt University  
// Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { invokeAgent, listenForAgentUpdates } from "./agent.js";
import { newStatus, getThinkingMessage } from "../common/status.js";

/**
 * Handles interaction with an agent, invoking it with the given parameters and monitoring its state.
 * This function can be used with or without an assistant.
 * 
 * @param {Object} llm - The language model interface for sending messages and status updates
 * @param {Object} params - Parameters including account information and options
 * @param {Object} body - The request body containing messages
 * @param {Object} [assistant=null] - Optional assistant configuration
 * @returns {Promise<void>}
 */
export const handleAgentInteraction = async (llm, params, body, assistant = null) => {
    const statusInfo = newStatus({
        animated: true,
        inProgress: true,
        sticky: true,
        summary: `Thinking...`,
        icon: "info",
    });

    const response = invokeAgent(
        params.account.accessToken,
        params.options.conversationId,
        body.messages,
        { assistant }
    );
    
    llm.sendStatus(statusInfo);
    llm.forceFlush();
    llm.forceFlush();

    var stopPolling = false;
    var result = null;
    
    await Promise.race([
        response.then(r => {
            stopPolling = true;
            result = r;
            return r;
        }),
        listenForAgentUpdates(params.account.accessToken, params.account.user, params.options.conversationId, (state) => {
            if (!state) {
                return !stopPolling;
            }

            console.log("Agent state updated:", state);
            let msg = getThinkingMessage();
            let details = "";
            
            if (state.state) {
                try {
                    const tool_call = JSON.parse(state.state);
                    const tool = tool_call.tool;
                    
                    if (tool === "terminate") {
                        msg = "Hold on...";
                    }
                    else if (tool === "exec_code") {
                        msg = "Executing code...";
                        details = `\`\`\`python\n\n${tool_call.args.code}\n\n\`\`\``;
                    }
                    else {
                        function formatToolCall(toolCall) {
                            const lines = [`Calling: ${toolCall.tool}`, '   with:'];
                            Object.entries(toolCall.args).forEach(([key, value]) => {
                                lines.push(`      ${key}: ${JSON.stringify(value)}`);
                            });
                            return lines.join('\n');
                        }

                        msg = "Calling: " + tool_call.tool;
                        details = formatToolCall(tool_call);
                    }
                } catch (e) {
                    // Handle parse error silently
                }
            }
            else {
                msg = `Agent state updated: ${JSON.stringify(state)}`;
            }
            
            statusInfo.summary = msg;
            statusInfo.message = details;
            llm.sendStatus(statusInfo);
            llm.forceFlush();
            
            return !stopPolling;
        })
    ]);

    llm.sendStateEventToStream({
        agentLog: result
    });
    llm.forceFlush();

    if (typeof result === 'string') {
        try {
            result = JSON.parse(result);
        } catch (error) {
            console.error('Failed to parse result as JSON:', error);
            result = {
                error: "Failed to parse result as JSON",
                success: false,
                rawResult: result
            }
        }
    }

    if (result.success) {
        let responseFromAssistant = result.data.result.findLast(msg => msg.role === 'assistant').content;

        if (responseFromAssistant.args && responseFromAssistant.args.message) {
            responseFromAssistant = responseFromAssistant.args.message;
        }
        else {
            responseFromAssistant = JSON.stringify(responseFromAssistant);
        }

        const summaryRequest = {
            ...body,
            messages: [
                {
                    role: "user",
                    content:
                        `The user's prompt was: ${body.messages.slice(-1)[0].content}` +
                        `\n\nA log of the assistant's reasoning / work:\n---------------------\n${JSON.stringify(result.data.result)}` +
                        `\n\n---------------------` +
                        `\n\nRespond to the user.`
                }]
        };

        await llm.prompt(summaryRequest, []);
    }
    else {
        let responseFromAssistant = JSON.stringify(result);

        const summaryRequest = {
            ...body,
            messages: [
                {
                    role: "user",
                    content:
                        `The user's prompt was: ${body.messages.slice(-1)[0].content}` +
                        `\n\nA log of the assistant's reasoning / work:\n---------------------\n${responseFromAssistant}` +
                        `\n\n---------------------` +
                        `\n\nRespond to the user.`
                }]
        };

        await llm.prompt(summaryRequest, []);

    }

    return result;
};