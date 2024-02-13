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
        const search = lastMessage.content;

        const keyLookup = {};
        dataSources.forEach(ds => {
            const key = extractKey(ds.id) + ".content.json";
            keyLookup[key] = ds;
        });

        const ragDataSourceKeys = Object.keys(keyLookup);

        const ragLLMParams = setModel(params, Models[process.env.RAG_ASSISTANT_MODEL_ID]);

        const llm = new LLM(
            chatFn,
            ragLLMParams,
            null);


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

        const result = await llm.promptForJson(
            {
                messages:[
                    {role:"user",
                        content:"Please provide 10 very detailed two-sentence descriptions of specific types of information that " +
                        "would help me perform the following task for the user:\nTask:\n----------------\n" + search
                    }]
            },
            ideasSchema,
            []
        );

        const resultsPerIdea = 10;
        const ragPromises = [];
        for(const idea of result.ideas) {
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
            {role:"user", content: "Possibly relevant information:\n----------------\n"},
            ...sources.map((item, index) => {
                return {role:"user",content:`${index + 1}. From: ${keyLookup[item.key].name}
Location: ${JSON.stringify(item.locations)}
Content: ${item.content}
`}})];



        return {messages, sources};
    }catch (e) {
        logger.error("Error getting context messages from RAG", e);
        return {messages:[], sources:[]};
    }
}