//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas




export const createContextMessage = (context) => {
    return {
        "role": "user", "content":
            `Using the following information:
-----------------------------
${context.context}
`
    };
}

export const addContextMessage = (messages, context) => {
    if(context.context && context.context.length > 0) {
        return [
            ...messages.slice(0, -1),
            createContextMessage(context),
            ...messages.slice(-1)
        ]
    }

    return messages;
}