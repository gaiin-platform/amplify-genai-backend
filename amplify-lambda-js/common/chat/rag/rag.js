import axios from "axios";
import {getAccessToken} from "../../params.js";
import {getLogger} from "../../logging.js";

const logger = getLogger("rag");

const ragEndpoint = process.env.RAG_ENDPOINT;

export const getContextMessages = async (params, chatBody, dataSources) => {

    try {
        const token = getAccessToken(params);

        const lastMessage = chatBody.messages.slice(-1)[0];

        const ragRequest = {
            data: {
                dataSources: dataSources.map(ds => ds.id + ".content.json"),
                userInput: lastMessage.content
            },
        }

        const response = await axios.post(ragEndpoint, ragRequest, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        // keyId, src s3 key, location obj, origin indexes, charIndex, tokenCount,
        // embedding Index, owner email, content
        const data = JSON.parse(response.data.body).map((item) => {
            return {
                role: "user",
                content: JSON.stringify(item)
            }
        });

        return data;
    }catch (e) {
        logger.error("Error getting context messages from RAG", e);
        return [];
    }
}