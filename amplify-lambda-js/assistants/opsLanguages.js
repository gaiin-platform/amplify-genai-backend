
export const opsLanguages = {
    "v1": {
        blockTerminator: "auto",
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
        blockTerminator: "invoke",
        instructionsPreProcessor: (instructions) => {
            // Replace all {{ with \\{{ to escape Handlebars
            instructions = instructions.replace(/{{/g, "\\{{");
            return instructions
        },
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
    },
    "v3": {
        "suffixMessages": [
            {
                role: "user",
                content: `
You can use the tellUser operation to provide the user with information. 
Analyze the task or question and either output the requested information or run the necessary operations to produce it.

You output with the response should be in the EXACT format:
\`\`\`invoke
thought: <INSERT THOUGHT>
{
    "name": "<insert operation name>",
    "payload":{...json for parameters with camelCase keys...}
}
\`\`\`

You ALWAYS output a SINGLE \`\`\`data code block with NOTHING BEFORE OR AFTER.
`
            }
        ],
        "messages": [
            {
                role: "system",
                content: `
                
Sample outputs to user prompts are shown below.:
\`\`\`invoke
{
  "thought": "I need to run an operation to get the information. The operations is called getXYZ.",
  "name": "getXYZ",
  "payload":{\"someParam\":\"someValue\", \"anotherParam\":1234}
}
\`\`\`
Note that there is NOTHING BEFORE OR AFTER the \`\`\`invoke block.

All of your output must be in a markdown block with nothing before or after as shown:

\`\`\`invoke
{
  "thought": "<insert 1-sentence thought>",
  "name": "<insert operation name>",
  "payload":{...json for parameters with camelCase keys...}
}
\`\`\`

My message that STARTS! with \`\`\`invoke is: 
`

            }
        ]
    }
}




