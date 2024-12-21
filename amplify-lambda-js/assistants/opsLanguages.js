

const EXCLUDE_BY_TAGS = ['amplify:api-doc-helper', 'amplify:api-key-manager']
// Using ops for apiDocumentation, the assistant is not allowed to perform ops
export const excludeOpsInstrByAstTag = (assistantTags) => {
    return assistantTags.some(tag => EXCLUDE_BY_TAGS.includes(tag));
};


export const opsLanguages = {
    "v1": {
        "messages": [
            {
                role: "user",
                content: `
You are going to help me accomplish prompting tasks with the LLM. You can have the system run operations by outputting special \`\`\`auto markdown blocks. YOU MUST CREATE AN \`\`\`auto block to run any operations. Before creating an \`\`\`auto block, **THINK STEP BY STEP**. 

The valid operations are:
{{ops llm}}

The <opId> must be one of the operation IDs listed above.

All parameters to an op must be on one line. Escape any newlines inside of parameters to an op.

The format of these blocks MUST BE EXACTLY:
\`\`\`auto
opId("param1", "param2",...)
\`\`\`
Examples:
\`\`\`auto
someOperation("some value", "1", "abc")
anotherOp("some value")
\`\`\`

\`\`\`auto
anotherOp("some value")
\`\`\`

\`\`\`auto
llmQueryDatasource("#$0", "Please provide a summary of the content")
otherOp("some value")
bigoOp("some value", "another value", "1...asdf")
\`\`\`

\`\`\`auto
thisOpHasNoParams()
llmQueryDatasource("#$5", "Tell me the policies on international travel")
bigoOp("some value", "another value", "1...asdf")
\`\`\`                    
`
            }
        ]
    },
    "v2": {
        "messages": [
            {
                role: "user",
                content: `
\`\`\`invoke
{
  "name": "<insert operation name>",
  "payload":{...json for parameters with camelCase keys...}
}
\`\`\`

If you need to run code, you can invoke an operation to get updated information, save data, etc. Don't use artifacts in this conversation.

If you get more than two errors in a row, you should stop and ask the user how to proceed.
                    `
            }
        ]
    }
}




