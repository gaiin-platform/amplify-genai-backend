//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas




export const createContextMessage = (context) => {
    // Handle case where content exists but wasn't relevant to the query
    if (context.noRelevantContent) {
        return {
            "role": "user", "content":
                `Using the following information (NOTE: This document content may not be directly relevant to the user's question, but is provided for completeness. If none of this information helps answer their question, please clearly state "The provided document does not contain relevant information for your question."):
-----------------------------
${context.context}
`
        };
    }
    
    return {
        "role": "user", "content":
            `Using the following information:
-----------------------------
${context.context}
`
    };
}

export const addContextMessage = (messagesOrContext, contextOrTokenCounter) => {
    // Handle two different call patterns:
    // 1. addContextMessage(messages, context) - original pattern
    // 2. addContextMessage(context, tokenCounter) - new pattern from chatWithData.js
    
    if (Array.isArray(messagesOrContext)) {
        // Original pattern: addContextMessage(messages, context)
        const messages = messagesOrContext;
        const context = contextOrTokenCounter;
        
        if(context.context && context.context.length > 0) {
            return [
                ...messages.slice(0, -1),
                createContextMessage(context),
                ...messages.slice(-1)
            ]
        }
        return messages;
    } else {
        // New pattern: addContextMessage(context, tokenCounter) - just create a single message
        const context = messagesOrContext;
        return createContextMessage(context);
    }
}