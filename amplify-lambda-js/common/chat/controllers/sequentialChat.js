//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { countChatTokens } from "../../../azure/tokens.js";
import { StreamMultiplexer } from "../../multiplexer.js";
import { sendSourceMetadata } from "./meta.js";
import { PassThrough, Writable } from 'stream';
import { newStatus } from "../../status.js";
import { isKilled } from "../../../requests/requestState.js";
import { getLogger } from "../../logging.js";
import { sendStatusEventToStream } from "../../streams.js";
import { getUser, getModel, setModel, getCheapestModelEquivalent, getMostAdvancedModelEquivalent } from "../../../common/params.js";
import { addContextMessage, createContextMessage } from "./common.js";
import { analyzeAndRecordGroupAssistantConversation } from "../../../groupassistants/conversationAnalysis.js";

const logger = getLogger("sequentialChat");

export const handleChat = async ({ account, chatFn, chatRequest, contexts, metaData, responseStream, eventTransformer, tokenReporting }) => {

    // The multiplexer is used to multiplex the streaming responses from the LLM provider
    // back to the client. This is necessary because we are going to run multiple requests (potentially)
    // to generate a single response. We want the client to see one continuous stream and not have to
    // deal with the fact that the response is coming from multiple sources. It is also possible for
    // the multiplexer to handle responses from multiple parallel requests and fuse them into a single
    // stream for the client.
    const multiplexer = new StreamMultiplexer(responseStream);

    const user = account.user;
    const requestId = chatRequest.options.requestId;
    let llmResponse = '';

    sendSourceMetadata(multiplexer, metaData);

    const status = newStatus(
        {
            inProgress: true,
            message: "",
            icon: "bolt",
            sticky: false
        });

    if (contexts.length > 1) {
        sendStatusEventToStream(
            responseStream,
            newStatus(
                {
                    inProgress: false,
                    message: `I will need to send ${contexts.length} prompts for this request`,
                    icon: "bolt",
                    sticky: true
                }));
    }

    for (const [index, context] of contexts.entries()) {


        if ((await isKilled(user, responseStream, chatRequest))) {
            return;
        }

        let messages = [...chatRequest.messages];

        logger.debug("Building message with context.");

        // Add the context as the next to last message in the
        // message list. This will provide the context for the user's
        // prompt.
        messages = addContextMessage(messages, context, chatRequest.options.model.id);

        const requestWithData = {
            ...chatRequest,
            messages: messages
        }

        const tokenCount = countChatTokens(messages);

        await tokenReporting(
            context.id, tokenCount
        )

        if (contexts.length > 1) {
            status.message = `Sending prompt ${index + 1} of ${contexts.length}`;
            status.dataSource = context.id;
            sendStatusEventToStream(
                responseStream,
                status);
        }

        logger.debug("Creating stream wrapper");
        const streamReceiver = new PassThrough();

        // Capture data as it's written to the streamReceiver for AI analysis
        streamReceiver.on('data', (chunk) => {
            const chunkStr = chunk.toString();
            const jsonStrings = chunkStr.split('\n').filter(str => str.startsWith('data: ')).map(str => str.replace('data: ', ''));

            for (const jsonStr of jsonStrings) {
                if (jsonStr === '[DONE]') {
                    continue;
                }

                try {
                    const chunkObj = JSON.parse(jsonStr);
                    if (chunkObj?.d?.delta?.text) {
                        llmResponse += chunkObj.d.delta.text;
                    }
                } catch (e) {
                    // Log the error and the problematic chunk, but don't throw
                    logger.debug(`Warning: Error parsing chunk: ${e.message}`);
                    logger.debug(`Problematic chunk: ${jsonStr}`);
                }
            }
        });

        multiplexer.addSource(streamReceiver, context.id, eventTransformer);

        logger.debug("Calling chat function");
        await chatFn(requestWithData, streamReceiver);
        logger.debug("Chat function returned");

        await multiplexer.waitForAllSourcesToEnd();

        logger.debug("Chat function streaming finished");
    }

    if (contexts.length > 1) {
        status.message = `Completed ${contexts.length} of ${contexts.length} prompts`;
        status.inProgress = false;
        sendStatusEventToStream(
            responseStream,
            status);
    }

    // if (chatRequest.options.assistantId.startsWith('astgp')) {
    if ((chatRequest.options.assistantId === 'astgp/77cf78dd-172e-4660-ab25-e45bcc8d5876' || chatRequest.options.assistantId === 'astgp/ebe68911-87e9-4914-95ba-5ec947a8828c') && ((!chatRequest.options.source && !chatRequest.options.ragOnly) || (chatRequest.options.source && !chatRequest.options.skipRag))) {
        logger.debug("Performing AI Analysis on conversationId:", chatRequest.options.conversationId);
        analyzeAndRecordGroupAssistantConversation(chatRequest, llmResponse, user).catch(error => {
            logger.debug('Error in analyzeAndRecordGroupAssistantConversation:', error);
        });
    }
}
