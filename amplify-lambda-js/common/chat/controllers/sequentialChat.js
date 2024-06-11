//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {countChatTokens} from "../../../azure/tokens.js";
import {StreamMultiplexer} from "../../multiplexer.js";
import {sendSourceMetadata} from "./meta.js";
import { PassThrough, Writable } from 'stream';
import {newStatus} from "../../status.js";
import {isKilled} from "../../../requests/requestState.js";
import {getLogger} from "../../logging.js";
import {sendStatusEventToStream} from "../../streams.js";
import {getUser} from "../../params.js";
import {addContextMessage, createContextMessage} from "./common.js";

const logger = getLogger("sequentialChat");

export const handleChat = async ({account, chatFn, chatRequest, contexts, metaData, responseStream, eventTransformer, tokenReporting}) => {

    // The multiplexer is used to multiplex the streaming responses from the LLM provider
    // back to the client. This is necessary because we are going to run multiple requests (potentially)
    // to generate a single response. We want the client to see one continuous stream and not have to
    // deal with the fact that the response is coming from multiple sources. It is also possible for
    // the multiplexer to handle responses from multiple parallel requests and fuse them into a single
    // stream for the client.
    const multiplexer = new StreamMultiplexer(responseStream);

    const user = account.user;
    const requestId = chatRequest.options.requestId;

    sendSourceMetadata(multiplexer, metaData);

    const status = newStatus(
        {
            inProgress: true,
            message: "",
            icon: "bolt",
            sticky: false
        });

    if(contexts.length > 1) {
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


        if((await isKilled(user, responseStream, chatRequest))){
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

        if(contexts.length > 1) {
            status.message = `Sending prompt ${index + 1} of ${contexts.length}`;
            status.dataSource = context.id;
            sendStatusEventToStream(
                responseStream,
                status);
        }

        logger.debug("Creating stream wrapper");
        const streamReceiver = new PassThrough();
        multiplexer.addSource(streamReceiver, context.id, eventTransformer);

        logger.debug("Calling chat function");
        await chatFn(requestWithData, streamReceiver);
        logger.debug("Chat function returned");

        await multiplexer.waitForAllSourcesToEnd();

        logger.debug("Chat function streaming finished");
    }

    if(contexts.length > 1) {
        status.message = `Completed ${contexts.length} of ${contexts.length} prompts`;
        status.inProgress = false;
        sendStatusEventToStream(
            responseStream,
            status);
    }

}