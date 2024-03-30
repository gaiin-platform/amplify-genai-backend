


export const createContextMessage = (context) => {
    return {
        "role": "user", "content":
            `Using the following information:
-----------------------------
${context.context}
`
    };
}

export const addContextMessage = (messages, context, modelId) => {
    if(context.context && context.context.length > 0) {
        if (modelId.includes("anthropic")) {
            const userMessage = messages.slice(-1);
            userMessage.content += createContextMessage(context).content;
            return [...messages.slice(0, -1), ...userMessage];
        }

        return [
            ...messages.slice(0, -1),
            createContextMessage(context),
            ...messages.slice(-1)
        ]
    }

    return messages;
}