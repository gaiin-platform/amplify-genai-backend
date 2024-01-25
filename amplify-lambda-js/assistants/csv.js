import {getContent} from "../datasource/datasources.js";
import {
    sendDeltaToStream,
    sendResultToStream,
    StreamResultCollector
} from "../common/streams.js";
import {newStatus} from "../common/status.js";
import {transform as fnTransformer} from "../common/chat/events/openaifn.js";
import Bottleneck from "bottleneck";
import {isKilled} from "../requests/requestState.js";
import {getUser} from "../common/params.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("csvAssitant");

const generateCSVSchema = function(columns) {
    const properties = columns.reduce(function(props, columnName) {
        props[columnName] = { type: 'string' };
        return props;
    }, {});

    const schema = {
        type: 'object',
        properties: {
            thought: {
                type: 'string',
            },
            rows: {
                type: 'array',
                items: {
                    type: 'object',
                    properties: properties,
                    required: columns
                }
            }
        },
        required: ['thought','rows']
    };

    return schema;
};

export const csvAssistant = {
    name: "batch",
    displayName: "Batch Processing Assistant",
    handlesDataSources: (ds) => {
        return ds.every((ds) => {
            return ds.type === "text/csv";
        });
    },
    handlesModel: (model) => {
        return true;
    },
    description: "This assistant takes each row in a CSV file and prompts the LLM using the" +
        " contents of the row and outputs the result as new CSV. It should not be used for any task " +
        "that is based on summarizing, explaining high-level details of the dataset, analyzing the " +
        "file as a whole, etc. Ask yourself, do I have to look at every single column in the dataset" +
        "separately? If not, then don't use this assistant.",
    handler: async (llm, params, body, dataSources, responseStream) => {

        const limiter = new Bottleneck({
            maxConcurrent: 5,
            minTime: 1000
        });

        const user = getUser(params);

        const csvPromises = [];

        //const [index, row] of rows.entries()
        //for (const ds of dataSources) {
        for(const [dsIndex, ds] of dataSources.entries()){

            try {

                const content = await getContent(ds);

                if (!content || !content.content || content.content.length === 0) {
                    sendResultToStream(responseStream, "The data source was empty.");
                    responseStream.end();
                    return;
                }

                // First, we figure out the columns in the CSV so that we can add
                // them to each prompt
                const firstRow = (content && content.content && content.content.length > 0) ?
                    content.content[0].content : "column1";


                llm.sendStatus(newStatus(
                    {
                        inProgress: false,
                        sticky: true,
                        message: `There are ${content.content.length} rows in the CSV file.`,
                        icon: "info",
                    }
                ));
                llm.forceFlush();

                // This is the schema to represent the row results that will be output
                const rowsSchema = generateCSVSchema(["Explanation", "Output"]);

                // We package the rows in groups of chunkSize so that we can prompt the LLM and limit
                // the chance of hallucination.
                const rows = [];
                const chunkSize = 1;
                for (let i = 0; i < content.content.length; i += chunkSize) {
                    const chunk = content.content.slice(i, i + chunkSize);
                    rows.push(chunk);
                }

                llm.sendStatus(newStatus(
                    {
                        inProgress: false,
                        sticky: true,
                        message: `This will require ${rows.length} prompts.`,
                        icon: "repeat",
                    }
                ));
                llm.forceFlush();

                const taskMessage = body.messages.slice(-1)[0].content;

                let csvBlockSent = false;
                let counter = 0;

                //sendResultToStream(responseStream, "```csv\n");
                sendDeltaToStream(responseStream, `0`, "```csv\n")


                const inProcessRows = {};
                const status = newStatus(
                    {
                        inProgress: true,
                        icon: "repeat",
                    }
                );

                const getInProcessRows = () => Object.entries(inProcessRows).filter(([k, v]) => v).map(([k, v]) => k);

                const rowStarted = (index) => {
                    inProcessRows[index] = true;
                    status.message = `Processing rows ${getInProcessRows().join(", ")}`;
                    llm.sendStatus(status);
                    llm.forceFlush();
                }

                const rowFinished = (index) => {
                    inProcessRows[index] = false;
                    status.message = `Processing rows ${getInProcessRows().join(",")}`;
                    llm.sendStatus(status);
                    llm.forceFlush();
                }

                llm.enableOutOfOrderStreaming(responseStream);

                // For each group of chunkSize rows, we need to prompt the LLM for results
                for (const [index, row] of rows.entries()) {

                    const inputData = row.map((r) => r.content.replace(/(\r\n|\n|\r)/gm, "\\n")).join(" ");

                    // We add the group of rows to the original prompt from the user as the
                    // context for the LLM
                    const updatedChatBody = {
                        ...body,
                        messages: [
                            ...body.messages.slice(0, -1),
                            {
                                role: "user",
                                content:
                                    `
You must be extremely accurate. 
Make sure you output the original data exactly. The new columns should come after the original columns. 
If the new columns have commas, make sure to wrap them in quotes.

${taskMessage}

Do not repeat the original data in the Output.

Using the data:
\`\`\`csv
Input: ${inputData}
\`\`\`
`
                            }
                        ]
                    }
                    // We prompt for the updated results
                    const resultCollector = new StreamResultCollector();
                    resultCollector.addTransformer(fnTransformer);

                    let tries = 3;

                    const promptForOutputRows = async (triesLeft) => {

                        if((await isKilled(user, responseStream, body))){
                            try{
                               await limiter.stop();
                            } catch (e) {
                            }
                            return;
                        }

                        if(triesLeft === 0){
                            return row.map((r, index) => {
                                return r.content + ","
                                    + "Error processing row." + ","
                                    + "Error processing row.";
                            }).join("\n");
                        }

                        const response = await llm.promptForJson(updatedChatBody, rowsSchema, [], resultCollector);

                        if(!response.rows ||
                            response.rows.length === 0 ||
                            !response.rows[0].Output ||
                            !response.rows[0].Explanation
                        ) {
                            triesLeft = triesLeft - 1;
                            return await promptForOutputRows(triesLeft);
                        }

                        const createColumn = (c) => {
                            try {
                                if (c.indexOf(",") > -1) {
                                    return "\"" + c.replaceAll("\"", "\\\"") + "\"";
                                }
                            } catch (e) {
                                return '';
                            }
                            return c;
                        }

                        return row.map((r, index) => {
                            return r.content + ","
                                + createColumn(response.rows[index].Output) + ","
                                + createColumn(response.rows[index].Explanation);
                        }).join("\n");
                    }

                    const result =
                        limiter.schedule(async () => {
                            try {
                                rowStarted(index + 1);
                                const outputRows = await promptForOutputRows(tries);

                                sendDeltaToStream(responseStream, `${dsIndex}_${index}`, outputRows);
                                rowFinished(index + 1);
                            } catch (e) {
                                rowFinished(index + 1);
                                logger.error(e);
                            }
                        });
                    csvPromises.push(result);

                }
            } catch (e) {
                const error = e;
            }
        }
        await Promise.all(csvPromises);
        sendResultToStream(responseStream, "\n```");
        responseStream.end();
    }
};
