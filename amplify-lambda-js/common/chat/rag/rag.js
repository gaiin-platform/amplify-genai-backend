import axios from "axios";
import {getAccessToken} from "../../params.js";
import {getLogger} from "../../logging.js";
import {extractKey} from "../../../datasource/datasources.js";

const logger = getLogger("rag");

const ragEndpoint = process.env.RAG_ENDPOINT;

export const getContextMessages = async (params, chatBody, dataSources) => {

    try {
        const token = getAccessToken(params);

        const lastMessage = chatBody.messages.slice(-1)[0];

        const keyLookup = {};
        dataSources.forEach(ds => {
            const key = extractKey(ds.id) + ".content.json";
            keyLookup[key] = ds;
        });

        const ragRequest = {
            data: {
                dataSources: Object.keys(keyLookup),
                userInput: lastMessage.content,
                limit: 10
            },
        }

        const response = await axios.post(ragEndpoint, ragRequest, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        logger.debug("RAG result total", response.data.length);

        const excerpts = "Possibly relevant information:\n----------------\n" + response.data.result.map((item) => {
            const [content, key, locations, indexes, charIndex, user] = item;

            return `From: ${keyLookup[key].name}
Location: ${JSON.stringify(locations)}
Content: ${content}
`;}).join("\n\n");

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

        return {
            messages:[{role:"user", content: excerpts}], sources};
    }catch (e) {
        logger.error("Error getting context messages from RAG", e);
        return {messages:[], sources:[]};
    }
}