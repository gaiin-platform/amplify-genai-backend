//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import axios from "axios";
import {getAccessToken, setModel} from "../../params.js";
import {getLogger} from "../../logging.js";
import {extractKey} from "../../../datasource/datasources.js";
import {LLM} from "../../llm.js";
import {getChatFn, getModelByType, ModelTypes} from "../../params.js";
import Bottleneck from "bottleneck";
import {trace} from "../../trace.js";

const logger = getLogger("rag");

const limiter = new Bottleneck({
    maxConcurrent: 10,
    minTime: 10
});

const ragEndpoint = process.env.API_BASE_URL + '/embedding-dual-retrieval';

async function getRagResults(params, token, search, ragDataSourceKeys, ragGroupDataSourcesKeys, ragAstDataSourcesKeys, count) {
    if (ragDataSourceKeys.length === 0 && ragGroupDataSourcesKeys.length === 0 && ragAstDataSourcesKeys.length === 0) {
        logger.debug("WARNING: No RAG data sources found, something when wrong, most likely from key to global translation");
        return {data: {result: []}};
    }
    const ragRequest = {
        data: {
            dataSources: ragDataSourceKeys,
            groupDataSources : ragGroupDataSourcesKeys, 
            astDataSources : ragAstDataSourcesKeys,
            userInput : search,
            limit: count
        },
    }

    logger.debug("RAG request", {data:{...ragRequest, userInput: "REDACTED"}});

    trace(params.requestId, ["rag", "request"], ragRequest);

    const response = await axios.post(ragEndpoint, ragRequest, {
        headers: {
            'Authorization': `Bearer ${token}`
        },
        timeout: 180000 // 3 minutes timeout in milliseconds
    });

    logger.debug("RAG response status", response.status);

    return response;
}


export const getContextMessages = async (params, chatBody, dataSources) => {
    const model = getModelByType(params, ModelTypes.CHEAPEST);
    const ragLLMParams = setModel( {...params, options: {skipRag: true, dataSourceOptions:{}}}, model);

    const chatFn = async (body, writable, context) => {
        return await getChatFn(model, body, writable, context);
    }

    const llm = new LLM(
        chatFn,
        ragLLMParams,
        null);

    const updatedBody = {
        ...chatBody,
        imageSources: [],
        model: model.id,
        max_tokens: model.outputTokenLimit,
        options: {
            ...chatBody.options,
            model,
            skipRag: true, // Prevent the use of documents
            ragOnly: true,  // in any way
            skipDocumentCache: true // Prevent the use of the documents - advanced rag
        }
    }

    return await getContextMessagesWithLLM(llm, params, updatedBody, dataSources);
}

function createSuperset(arrayOfObjects) {
    const superset = {};

    arrayOfObjects.forEach(obj => {
        Object.keys(obj).forEach(key => {
            if (!superset[key]) {
                // Initialize a new Set if the key is new
                superset[key] = new Set();
            }
            // Add the value to the Set to ensure uniqueness
            superset[key].add(obj[key]);
        });
    });

    // Convert Sets to arrays for a more standard object structure
    Object.keys(superset).forEach(key => {
        superset[key] = Array.from(superset[key]);
    });

    return superset;
}

export const getContextMessagesWithLLM = async (llm, params, chatBody, dataSources) => {

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
        const ragGroupDataSourcesKeys = {};
        const ragAstDataSourcesKeys = {};
        const ragDataSourceKeys = [];
        dataSources.forEach(async ds => {
            const key = extractKey(ds.id);
            if (ds.groupId) {
                // If the dataSource has a groupId, add it to the groupDataSources object
                if (!ragGroupDataSourcesKeys[ds.groupId]) {
                    ragGroupDataSourcesKeys[ds.groupId] = [];
                }
                ragGroupDataSourcesKeys[ds.groupId].push(key);
            } else if (ds.ast) {
                if (!ragAstDataSourcesKeys[ds.ast]) {   
                    ragAstDataSourcesKeys[ds.ast] = [];
                }
                ragAstDataSourcesKeys[ds.ast].push(key);
            } else {
                ragDataSourceKeys.push(key)
                // call the check-completion endpoint to ensure the embeddings are complete if not itll start
                // doing it here buys us time for any missing embeddings to complete
                // note: group data sources are always embedded and prechecked elsewhere
                checkEmbeddingCompletion(token, [key], params.requestId);
            }
            keyLookup[key] = ds;
        });
        
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
            (r)=>{
                return r.firstQuestion
            }, 3);

        const result = {
            ideas: [
                {descriptionOfSpecificHelpfulInformation: search},
                {descriptionOfSpecificHelpfulInformation: searches.thought || search},
                {descriptionOfSpecificHelpfulInformation: searches.firstQuestion || searches.thought || search},
                {descriptionOfSpecificHelpfulInformation: searches.secondQuestion || searches.thought || search},
                {descriptionOfSpecificHelpfulInformation: searches.thirdQuestion || searches.thought || search},
            ]
        }


        const resultsPerIdea = 5;
        const ragPromises = [];
        for (const idea of result.ideas) {
            const result =
                limiter.schedule(async () => {
                    try {
                        const searchString = idea.descriptionOfSpecificHelpfulInformation;
                        const response = await getRagResults(params, token, searchString, ragDataSourceKeys, ragGroupDataSourcesKeys, ragAstDataSourcesKeys, resultsPerIdea);
                        const sources = response.data.result.map((item) => {
                            const [content, key, locations, indexes, charIndex, user, tokenCount,  ragId, score] = item;
                            const ds = keyLookup[key];
                            return {
                                ragId,
                                tokenCount,
                                score: score || 0.5,
                                name: ds.name,
                                key,
                                contentKey: ds.metadata?.userDataSourceId ?? ds.key,
                                groupId: ds.groupId,
                                type: ds.type,
                                locations,
                                url: ds.metadata?.sourceUrl,
                                indexes,
                                charIndex,
                                user,
                                content
                            }
                        });
                        return sources;
                    } catch (e) {
                        if (e.response) {
                            // Extract status code and response message
                            const statusCode = e.response.status;
                            const responseMessage = e.response.data;

                            // Log the status code and message
                            console.error(`Error: Request failed with status code ${statusCode}`);
                            console.error(`Response Message: ${JSON.stringify(responseMessage)}`);
                        }
                        else {
                            logger.error("Error getting RAG results", e);
                        }
                        return [];
                    }
                });
            ragPromises.push(result);
        }

        const sources = (await Promise.all(ragPromises)).flat();

        // Sort the sources by score
        sources.sort((a, b) => -1 * (b.score - a.score));

        logger.debug("RAG raw result total", sources.length);

        // Filter the list of sources to only include one copy of each item
        // based on the ragId
        const uniqueSources = [];
        const seen = new Set();
        for (const item of sources) {
            if (!seen.has(item.ragId) && !seen.has(item.content)) {
                uniqueSources.push(item);
                seen.add(item.ragId);
                seen.add(item.content);
            }
        }

        logger.debug("RAG unique result total", uniqueSources.length);

        // Group the unique sources by the key
        const groupedSources = {};
        for (const item of uniqueSources) {
            if (!groupedSources[item.key]) {
                groupedSources[item.key] = [];
            }
            groupedSources[item.key].push(item);
        }

        const messages = [
            {role: "user", content: "Possibly relevant information:\n----------------\n"},
            ...Object.entries(groupedSources).map(([key,contentsFromKey], index) => {
                const content = contentsFromKey.map(item => {
                    return `Location: ${JSON.stringify(createSuperset(item.locations))}
Content: ${item.content}
                    `
                }).join("\n");

                return {
                    role: "user", content: `${index + 1}. From: ${keyLookup[key].name}
${content}
`
                }
            })];

        trace(params.requestId, ["rag", "result"], {sources: uniqueSources});

        return {messages, sources:uniqueSources};
    } catch (e) {
        logger.error("Error getting context messages from RAG", e);
        return {messages: [], sources: []};
    }
}

export const checkEmbeddingCompletion = async (token, dataSourceIds, requestId) => {
    const checkEmbeddingsEndpoint = process.env.API_BASE_URL + '/embedding/check-completion';

    const request = {
        data: {dataSources: dataSourceIds}
    }

    logger.debug("Checking embedding completion for data sources ", dataSourceIds);

    try {
        const response = await axios.post(checkEmbeddingsEndpoint, request, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.status === 200) {
            
            // If response contains failed_ids, trace it
            if (response.data && response.data.failed_ids && response.data.failed_ids.length > 0) {
                trace(requestId, ["rag", "embeddings_check_failed"], {
                    request: request,
                    failed_ids: response.data.failed_ids
                });
                
                logger.warn("Some datasources failed embedding", {
                    datasources: response.data.failed_ids
                });
            }
            
            return response?.data?.success;
        } else {
            logger.error("Embedding check failed with status", response.status);
            return response;
        }
    } catch (error) {
        logger.error("Error checking embedding completion", error);
    }
}