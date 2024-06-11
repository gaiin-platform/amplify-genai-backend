//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas




const promptForWorkflow = async (workflowName, workflowDescription) => {

    const customInstructions = `
Whenever you are asked a question or to perform a task that has a data source, you need to stop and think carefully. 

You will need to solve the problem using one of the following techniques (placeholders for you to fill in are marked with <LLM: ...>:

1. If the data source fits into a single prompt, just run a single prompt by responding like this:

\`\`\`prompt
{"input":"<LLM: choose datasource or variable>","prompt":"<LLM: create a detailed prompt to answer the question or perform the task with the data source>", "outputTo":"<LLM: create a variable to store the result>"}
\`\`\`

2. If the data source doesn't fit into a single prompt, it will need to be split up into chunks.

3. You can use "map" to run a prompt with each chunk. This will create a new list of results and have the most information. Respond like this to map the chunks to a prompt:

\`\`\`prompt
{"steps":[{"input":"<LLM: choose datasource or variable>","map":"<LLM: create a detailed prompt to answer the question or perform the task with the data source>""outputTo":"<LLM: create a variable to store the result>"}]}
\`\`\`

4. You can respond like this to apply a series of map prompting operations in a row:

\`\`\`prompt
{"steps":[{"input":"<LLM: choose datasource or variable>","map":"<LLM: create a detailed prompt to answer the question or perform the first step of the task with the data source>", "outputTo":"someVar"},
...etc....
{"input":"<LLM: choose some earlier datasource or var to read from>","map":"<LLM: create a detailed prompt to process each result from the prior prompt to create a new list>","outputTo":"some new var"}]}
\`\`\`

4. If the result list should have a "reduce" operation performed, then you should think of a prompt that can be used to combine results (e.g., "create a combined summary of X and Y" or "keep the information most relevant to question Q from X and Y"). You only need to reduce if there is some form of summarization, condensation of results, etc. You should respond like this to perform a reduce:

\`\`\`prompt
{"steps":[{"input"...."map":"<LLM: create a detailed prompt to answer the question or perform the first step of the task with the data source>","outputTo":"..."},....,{"input":"<LLM: choose some earlier var or data source to reduce on>","reduce":"<LLM: create a detailed prompt explaining how to pair-wise combine two results to produce an answer to the original task>", "outputTo":"choose some output var"}]}
\`\`\`

Think carefully about the user's request and then respond with JSON with a prompt, map, reduce plan. You can have any number of map, reduce, and prompt operations that form a directed graph from the datasource to one or more named output. We want the minimum number of operations to solve the problem and you don't have to worry about the tools to do it or dealing with list concatenation, etc.
    `;



}