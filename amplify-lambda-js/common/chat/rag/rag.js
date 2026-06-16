//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import axios from "axios";
import {getAccessToken, setModel} from "../../params.js";
import {getLogger} from "../../logging.js";
import {extractKey} from "../../../datasource/datasources.js";
import {getModelByType, ModelTypes} from "../../params.js";
import { promptUnifiedLLMForData } from "../../../llm/UnifiedLLMClient.js";
import Bottleneck from "bottleneck";
import {trace} from "../../trace.js";
import {newStatus} from "../../status.js";
import {sendStatusEventToStream, sendStateEventToStream, forceFlush} from "../../streams.js";

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
    logger.debug("🔍 RAG: Using CHEAPEST model:", model.id, "vs user model:", params.model?.id || "unknown");

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

    return await getContextMessagesWithLLM(model, params, updatedBody, dataSources);
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

export const getContextMessagesWithLLM = async (model, params, chatBody, dataSources) => {

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
            const isBedrockKb = ds.id && ds.id.startsWith("bedrock-kb://");
            const key = isBedrockKb ? ds.id : extractKey(ds.id);
            if (ds.groupId) {
                // If the dataSource has a groupId, add it to the groupDataSources object
                if (!ragGroupDataSourcesKeys[ds.groupId]) {
                    ragGroupDataSourcesKeys[ds.groupId] = [];
                }
                ragGroupDataSourcesKeys[ds.groupId].push(key);
            } else if (ds.ast) {
                // ds.ast is either a plain assistantId string (standalone ast with path)
                // or an object { layeredAstId, astId } (leaf reached via a Layered Assistant).
                // Serialize objects to a stable JSON string so they survive as dict keys
                // through to dual retrieval, where classify_ast_src_ids_by_access parses them.
                const astKey = typeof ds.ast === 'object' ? JSON.stringify(ds.ast) : ds.ast;
                if (!ragAstDataSourcesKeys[astKey]) {
                    ragAstDataSourcesKeys[astKey] = [];
                }
                ragAstDataSourcesKeys[astKey].push(key);
            } else {
                ragDataSourceKeys.push(key)
                // call the check-completion endpoint to ensure the embeddings are complete if not itll start
                // doing it here buys us time for any missing embeddings to complete
                // note: group data sources are always embedded and prechecked elsewhere
                if (!isBedrockKb) {
                    checkEmbeddingCompletion(token, [key], params.requestId);
                }
            }
            keyLookup[key] = ds;
        });
        
        logger.debug("🔍 RAG: About to call promptUnifiedLLMForData with", dataSources.length, "dataSources");
        
        const promptMessages = [
            ...chatBody.messages.filter(m => m.role !== "system"), 
            {
                role: "user",
                content: `
            Imagine that you are looking through a frequently asked questions (FAQ) page on a website.
            The FAQ is based on the documents in this conversation.

            You are trying to find information in the FAQ to help you accomplish the following task for the user:
            Task:
            ----------------
            ${search}

            Please explain what questions you need to look for in the FAQ.

            IMPORTANT: Your response will be parsed as structured data. Do not provide conversational explanations or respond to previous messages. Focus only on generating the FAQ search questions for the task above.
            `
            }
        ];
        
        const searches = await promptUnifiedLLMForData(
            {
                account: params.account,
                options: {
                    model,
                    requestId: params.requestId,
                    disableReasoning: true,
                    skipHistoricalContext: true  // RAG question extraction doesn't need conversation history
                }
            },
            promptMessages.filter(m => m.role && m.role !== "system"), // Remove system messages to avoid confusion in the structured response
            {
                type: "object",
                properties: {
                    firstQuestion: {
                        type: "string",
                        description: "First specific FAQ question to look for."
                    },
                    secondQuestion: {
                        type: "string",
                        description: "Second specific FAQ question to look for."
                    },
                    thirdQuestion: {
                        type: "string",
                        description: "Third specific FAQ question to look for."
                    }
                },
                required: ["firstQuestion", "secondQuestion", "thirdQuestion"]
            },
            null // No streaming
        );
        
        logger.debug("✅ RAG: promptUnifiedLLMForData completed, result:", searches ? Object.keys(searches) : "null");

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
                        // Capture any datasources the backend denied/dropped. Identical across
                        // the parallel idea-calls (same inputs), so the caller dedups by key.
                        const removedDataSources = response.data?.removedDataSources || null;
                        const sources = (response.data.result || []).map((item) => {
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
                        return {sources, removedDataSources};
                    } catch (e) {
                        if (e.response) {
                            // Extract status code and response message
                            const statusCode = e.response.status;
                            const responseMessage = e.response.data;

                            // Log the status code and message
                            logger.error(`Error: Request failed with status code ${statusCode}`);
                            logger.error(`Response Message: ${JSON.stringify(responseMessage)}`);
                        }
                        else {
                            logger.error("Error getting RAG results", e);
                        }
                        return {sources: [], removedDataSources: null};
                    }
                });
            ragPromises.push(result);
        }

        const ragResponses = await Promise.all(ragPromises);
        const sources = ragResponses.flatMap(r => r.sources);

        // Merge denied datasources across idea-calls into a single deduped summary.
        // Categories: individual (user owns -> show names), group/ast (generic message).
        const mergedRemoved = {individual: new Set(), group: new Set(), ast: new Set()};
        for (const r of ragResponses) {
            const rds = r.removedDataSources;
            if (!rds) continue;
            (rds.individual || []).forEach(k => mergedRemoved.individual.add(k));
            (rds.group || []).forEach(k => mergedRemoved.group.add(k));
            (rds.ast || []).forEach(k => mergedRemoved.ast.add(k));
        }
        // Resolve names only for individual (owned) datasources; group/ast stay nameless.
        const removedDataSources = {
            individual: [...mergedRemoved.individual].map(key => ({
                key,
                name: keyLookup[key]?.name || undefined
            })),
            group: [...mergedRemoved.group].map(key => ({key})),
            ast: [...mergedRemoved.ast].map(key => ({key})),
        };
        const hasRemoved = removedDataSources.individual.length > 0 ||
            removedDataSources.group.length > 0 ||
            removedDataSources.ast.length > 0;
        
        logger.debug("🔍 RAG: Raw results returned:", sources.length, "sources");

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
        
        logger.debug("🔍 RAG: Final return - sources:", uniqueSources.length, "messages:", messages.length);

        return {messages, sources:uniqueSources, removedDataSources: hasRemoved ? removedDataSources : null};
    } catch (e) {
        logger.error("Error getting context messages from RAG", e);
        return {messages: [], sources: [], removedDataSources: null};
    }
}

/**
 * Emit a user-facing notification about datasources that were denied/dropped by
 * the dual-retrieval backend. Mirrors the existing denied-file warning pattern.
 *
 *  - individual: the user is typically the owner -> safe to show file names
 *  - group / ast: the user may NOT be the owner -> generic message only, no names
 *
 * @param {object} responseStream - active response stream
 * @param {object|null} removedDataSources - {individual, group, ast} from getContextMessages*
 */
export const notifyRemovedDataSources = (responseStream, removedDataSources) => {
    if (!removedDataSources || !responseStream || responseStream.destroyed) return;

    const {individual = [], group = [], ast = []} = removedDataSources;
    const messages = [];

    if (individual.length > 0) {
        const names = individual.map(d => d.name).filter(Boolean);
        const detail = names.length > 0 ? names.join(", ") : `${individual.length} file(s)`;
        messages.push(`The following data sources could not be accessed and were removed: ${detail}`);
    }

    // Group + AST: do NOT reveal names — the user may not own these files.
    if (group.length > 0 || ast.length > 0) {
        messages.push(
            "Some of the assistant's data sources were not properly captured, " +
            "so the assistant may not function as expected. Please contact the assistant's owner."
        );
    }

    if (messages.length === 0) return;

    // Translate into the shape RemovedDataSourcesBlock expects:
    // { invalidIds: string[], deniedAccess: {objectId, name?, reason?}[], invalidImageIds: string[] }
    const deniedAccess = [
        ...individual.map(d => ({objectId: d.key, name: d.name || d.key, reason: 'no_permission_record'})),
        ...group.map(d =>     ({objectId: d.key, reason: 'no_permission_record'})),
        ...ast.map(d =>       ({objectId: d.key, reason: 'no_permission_record'})),
    ];
    const frontendRemoved = {invalidIds: [], deniedAccess, invalidImageIds: []};

    sendStatusEventToStream(responseStream, newStatus({
        inProgress: false,
        message: `⚠️ Some data sources were removed: ${messages.join('; ')}`,
        icon: "warning",
        sticky: true,
        metadata: {removedDataSources: frontendRemoved}
    }));
    sendStateEventToStream(responseStream, {removedDataSources: frontendRemoved});
    forceFlush(responseStream);
};

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