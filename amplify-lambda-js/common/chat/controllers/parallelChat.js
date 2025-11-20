//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {StreamMultiplexer} from "../../multiplexer.js";
import { PassThrough, Writable } from 'stream';
import {getSourceMetadata, sendSourceMetadata, aliasContexts} from "./meta.js";
import {getLogger} from "../../logging.js";

const logger = getLogger("parallelChat");
export const handleChat = async ({chatFn, chatRequest, contexts, responseStream, eventTransformer}) => {

    const multiplexer = new StreamMultiplexer(responseStream);

    sendSourceMetadata(multiplexer, metaData);

    for (const context of contexts) {

        let messages = [...chatRequest.messages];

        logger.debug("Building message with context.");

            messages = [
                ...messages.slice(0, -1),
                {
                    "role": "user", "content":
                        `Using the following information:
-----------------------------
${context.context}
`
                },
                ...messages.slice(-1)
            ]


        const requestWithData = {
            ...chatRequest,
            messages: messages
        }


        logger.debug("Creating stream wrapper");
        const streamReceiver = new PassThrough();
        multiplexer.addSource(streamReceiver, context.id, eventTransformer);

        logger.debug("Calling chat function");
        chatFn(requestWithData, streamReceiver);
        logger.debug("Chat function returned, running in parallel");

    }

    await multiplexer.waitForAllSourcesToEnd();

    logger.debug("All parallel chat functions returned");

}