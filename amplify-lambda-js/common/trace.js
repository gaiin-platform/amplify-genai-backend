import {S3Client, PutObjectCommand} from '@aws-sdk/client-s3';
import { v4 as uuidv4 } from 'uuid';

const client = new S3Client();
const bucket = process.env.TRACE_BUCKET_NAME;
const doTrace = process.env.TRACING_ENABLED;

const traces = {

}

export const trace = (requestId, tags, value) => {
    if(doTrace) {
        if (!traces[requestId]) {
            traces[requestId] = []
        }

        traces[requestId].push({
            tags,
            time: new Date().toISOString(),
            ...value
        })
    }
}

export const saveTrace = async (user, requestId) => {
    if(doTrace) {

        // Date in MM-DD-YYYY format
        const date = new Date().toISOString().split('T')[0];

        const data = {
            timeStamp: new Date().toISOString(),
            user,
            requestId,
            events: traces[requestId]
        };
        delete traces[requestId];


        if (data) {
            const seconduuid = uuidv4();
            const key = `traces/${user}/${date}/${requestId}-${seconduuid}.json`;

            const command = new PutObjectCommand({
                Bucket: bucket,
                Key: key,
                Body: JSON.stringify(data),
            });

            try {
                await client.send(command);
            } catch (error) {
                console.error("Error saving trace", error);
            }
        }
    }
}