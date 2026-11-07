// Pure message-merging helper for the Bedrock adapter (kept free of AWS/secret imports so it is unit-testable).

export function combineMessages(oldMessages, failSafeUserMessage) {
    if (!oldMessages || oldMessages.length === 0) return oldMessages;

    const delimiter = "\n_________________________\n";
    const messages = [];
    let currentMessage = null;

    // Single pass optimization
    for (let j = 0; j < oldMessages.length; j++) {
        const msg = oldMessages[j];
        const role = msg.role;

        // Don't combine tool messages or assistant messages with tool_calls - preserve full structure
        if (role === 'tool' || (role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0)) {
            // Push any pending message first
            if (currentMessage) {
                messages.push(currentMessage);
                currentMessage = null;
            }
            // Push tool-related message as-is (preserves tool_call_id and tool_calls)
            messages.push({ ...msg });
            continue;
        }

        let content = msg.content || "NA Intentionally left empty, disregard and continue";

        // NOTE: the last user message intentionally gets NO leading delimiter. Rendering must be
        // deterministic across turns (and across tool-loop iterations) so prompt-cache prefixes match;
        // merged same-role messages below already get a delimiter between them.

        content = typeof content === 'string' ? content.trimEnd() : content; // remove white space

        if (!currentMessage || currentMessage.role !== role) {
            // New role, push previous and start new
            if (currentMessage) messages.push(currentMessage);
            currentMessage = { role, content };
        } else {
            // Same role, combine content
            currentMessage.content += delimiter + content;
        }
    }

    // Push last message
    if (currentMessage) messages.push(currentMessage);

    // Ensure first message is user
    if (messages.length === 0 || messages[0].role !== 'user') {
        messages.unshift({ role: 'user', content: failSafeUserMessage });
    }

    return messages;
}
