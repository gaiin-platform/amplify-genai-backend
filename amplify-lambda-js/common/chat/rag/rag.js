import axios from "axios";
import {getAccessToken, setModel} from "../../params.js";
import {getLogger} from "../../logging.js";
import {extractKey} from "../../../datasource/datasources.js";
import {LLM} from "../../llm.js";
import {ModelID, Models} from "../../../models/models.js";
import Bottleneck from "bottleneck";
import {sendDeltaToStream} from "../../streams.js";

const logger = getLogger("rag");

const limiter = new Bottleneck({
    maxConcurrent: 10,
    minTime: 10
});

const ragEndpoint = process.env.RAG_ENDPOINT;

async function getRagResults(token, search, ragDataSourceKeys, count) {

    const ragRequest = {
        data: {
            dataSources: ragDataSourceKeys,
            userInput: search,
            limit: count
        },
    }

    const response = await axios.post(ragEndpoint, ragRequest, {
        headers: {
            'Authorization': `Bearer ${token}`
        }
    });
    return response;
}

/**
 *
 * json!({
 *   "$schema": "http://json-schema.org/draft-07/schema#",
 *   "type": "object",
 *   "properties": {
 *     "ideas": {
 *       "type": "array",
 *       "items": {
 *         "type": "object",
 *         "properties": {
 *           "descriptionOfSpecificHelpfulInformation": {
 *             "type": "string"
 *           }
 *         },
 *         "required": ["descriptionOfSpecificHelpfulInformation"],
 *         "additionalProperties": false
 *       }
 *     }
 *   },
 *   "required": ["ideas"],
 *   "additionalProperties": false
 * }) Please provide 10 very detailed two-sentence descriptions of specific types of information that would help me perform the following task for the user:
 * Task:
 * ----------------
 * {{Task}}
 *
 *
 * @param params
 * @param chatBody
 * @param dataSources
 * @returns {Promise<{sources: *[], messages: *[]}|{sources: *, messages: [{role: string, content: string},...*]}>}
 */

export const getContextMessages = async (chatFn, params, chatBody, dataSources) => {

    try {
        const token = getAccessToken(params);

        const lastMessage = chatBody.messages.slice(-1)[0];

        // Iterate through the four messages from the reverse of the chatBody messages
        // and concatenate their content together to form the search string
        // This is a workaround for the fact that the last message is not always the search string
        // and the search string can be split across multiple messages

        //const search = chatBody.messages.slice(-4).reverse().map(m => m.content).join(" ");

        const search = lastMessage.content;

        const keyLookup = {};
        dataSources.forEach(ds => {
            const key = extractKey(ds.id) + ".content.json";
            keyLookup[key] = ds;
        });

        const ragDataSourceKeys = Object.keys(keyLookup);

        const ragLLMParams = setModel(
            {...params, options: {skipRag: true}},
            Models[process.env.RAG_ASSISTANT_MODEL_ID]);

        const llm = new LLM(
            chatFn,
            ragLLMParams,
            null);


        const searches = await llm.promptForData(
            chatBody, [],
            `
            Imagine that you are looking through a frequently asked questions (FAQ) page on a website.
            The FAQ is based on the documents in this conversation.

            You are trying to find information in the FAQ to help you accomplish the following task for the user:
            Task:
            ----------------
            ${search}

            Please explain what questions you need to look for in the FAQ.
            `,
            {
                "firstQuestion": "First specific FAQ question to look for.",
                "secondQuestion": "Second specific FAQ question to look for.",
                "thirdQuestion": "Third specific FAQ question to look for.",
            },
            null,
            null, 3);


        // beforeDelta: 0, 2
        // afterDelta: 0, 2
        // Location range: 0, 2

        const result = {
            ideas: [
                {descriptionOfSpecificHelpfulInformation: search},
                {descriptionOfSpecificHelpfulInformation: searches.thought || search},
                {descriptionOfSpecificHelpfulInformation: searches.firstQuestion || searches.thought || search},
                {descriptionOfSpecificHelpfulInformation: searches.secondQuestion || searches.thought || search},
                {descriptionOfSpecificHelpfulInformation: searches.thirdQuestion || searches.thought || search},
            ]
        }


        const ideasSchema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "ideas": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "descriptionOfSpecificHelpfulInformation": {
                                "type": "string"
                            }
                        },
                        "required": ["descriptionOfSpecificHelpfulInformation"],
                        "additionalProperties": false
                    }
                }
            },
            "required": ["ideas"],
            "additionalProperties": false
        }

        // const result = {ideas:[{descriptionOfSpecificHelpfulInformation:search}]}
        // const result = await llm.promptForJson(
        //     {
        //         messages:[
        //             {role:"user",
        //                 content:"Please provide three very detailed two-sentence descriptions of specific types of information that " +
        //                 "would help me perform the following task for the user:\nTask:\n----------------\n" + search
        //             }]
        //     },
        //     ideasSchema,
        //     []
        // );

        const resultsPerIdea = 5;
        const ragPromises = [];
        for (const idea of result.ideas) {
            const result =
                limiter.schedule(async () => {
                    try {
                        const searchString = idea.descriptionOfSpecificHelpfulInformation;
                        const response = await getRagResults(token, searchString, ragDataSourceKeys, resultsPerIdea);
                        const sources = response.data.result.map((item) => {
                            const [content, key, locations, indexes, charIndex, user] = item;
                            const ds = keyLookup[key];
                            return {
                                name: ds.name,
                                key,
                                type: ds.type,
                                locations,
                                indexes,
                                charIndex,
                                user,
                                content
                            }
                        });
                        return sources;
                    } catch (e) {
                        return [];
                    }
                });
            ragPromises.push(result);
        }

        const sources = (await Promise.all(ragPromises)).flat();

        logger.debug("RAG result total", sources.length);

        const messages = [
            {role: "user", content: "Possibly relevant information:\n----------------\n"},
            ...sources.map((item, index) => {
                return {
                    role: "user", content: `${index + 1}. From: ${keyLookup[item.key].name}
Location: ${JSON.stringify(item.locations)}
Content: ${item.content}
`
                }
            })];


        // const relevantPages = await llm.promptForData(
        //     chatBody, [],
        //     `
        //     I am trying to accomplish this task:
        //     ----------------
        //     ${search}
        //
        //     The information:
        //     ----------------
        //     ${messages.map(m => m.content).join("\n")}
        //     ----------------
        //
        //     Please list the most important locations to review based on the available information.
        //     `,
        //     {
        //         "location1": "the most important location to review based on the available information.",
        //         "location2": "the second most important location to review based on the available information.",
        //         "location3": "the third most important location to review based on the available information.",
        //     },
        //     null,
        //     null, 3);


        return {messages, sources};
    } catch (e) {
        logger.error("Error getting context messages from RAG", e);
        return {messages: [], sources: []};
    }
}