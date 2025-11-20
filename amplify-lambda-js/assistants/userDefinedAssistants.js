
//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import {getDataSourcesByUse, isImage} from "../datasource/datasources.js";
import { DynamoDBClient, GetItemCommand, QueryCommand } from "@aws-sdk/client-dynamodb";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import {fillInTemplate} from "./instructions/templating.js";
import {PutObjectCommand, S3Client} from "@aws-sdk/client-s3";
import {addAllReferences, DATASOURCE_TYPE, getReferences, getReferencesByType} from "./instructions/references.js";
import {opsLanguages} from "./opsLanguages.js";
import {newStatus} from "../common/status.js";
import {invokeAgent, constructTools, getTools} from "./agent.js";
// import AWSXRay from "aws-xray-sdk";

const s3Client = new S3Client();
const dynamodbClient = new DynamoDBClient({ });

async function getAssistantByAlias(user, assistantId) {
    const params = {
        TableName: process.env.ASSISTANTS_ALIASES_DYNAMODB_TABLE,
        Key: {
            user: { S: user },
            assistantId: { S: assistantId +"?type=latest" }
        }
    };

    try {
        const response = await dynamodbClient.send(new GetItemCommand(params));
        if (response.Item) {
            return unmarshall(response.Item);
        } else {
            console.log("No item retrieved in getAssistantByAlias")
            return null;
        }
    } catch (error) {
        console.error('Error getting assistant alias:', error);
        return null;
    }
}

async function getAssistantByAssistantDatabaseId(id) {

    const params = {
        TableName: process.env.ASSISTANTS_DYNAMODB_TABLE,
        Key: {
            id: { S: id }
        }
    };

    try {
        const response = await dynamodbClient.send(new GetItemCommand(params));
        if (response.Item) {
            return unmarshall(response.Item);
        } else {
            console.log("No item retrieved in getAssistantByAssistantDatabaseId")
            return null;
        }
    } catch (error) {
        console.error('Error getting assistant alias:', error);
        return null;
    }
}

const saveChatToS3 = async (assistant, currentUser, chatBody, metadata) => {
    console.log("saveChatToS3 function")
    // Define the parameters for the putObject operation

    if(!process.env.ASSISTANT_LOGS_BUCKET_NAME) {
        console.error("ASSISTANT_LOGS_BUCKET_NAME environment variable is not set");
        console.log("Will not log assistant chat");
        return null;
    }

    // date string in format 2023-12-29/
    const date = new Date().toISOString().split('T')[0];
    const requestId = chatBody.options.requestId;

    const key = `${assistant.assistantId}/${date}/${currentUser}/${requestId}.json`;

    const putObjectParams = {
        Bucket: process.env.ASSISTANT_LOGS_BUCKET_NAME,
        Key: key,
        Body: JSON.stringify({request:chatBody, currentUser, metadata}),
    };

    try {
        // Upload the object to S3
        const data = await s3Client.send(new PutObjectCommand(putObjectParams));
        console.log("Object uploaded successfully. Location:", resultKey);
        return data;
    } catch (error) {
        console.error("Error uploading object:", error);
        throw error;
    }
}

const isMemberOfGroup = async (current_user, ast_owner, token) => {
    try {
        const params = {
            TableName: process.env.GROUPS_DYNAMO_TABLE,
            Key: {
                group_id: { S: ast_owner }
            }
        };
    
        const response = await dynamodbClient.send(new GetItemCommand(params));
        
        if (response.Item) {
            const item = unmarshall(response.Item);
            // Check if the group is public or if the user is a direct member, a system user, or a member of an amplify group
            if (item.isPublic || 
                (item.members && Object.keys(item.members).includes(current_user)) || 
                (item.systemUsers && item.systemUsers.includes(current_user)) || 
                userInAmplifyGroup(item.amplifyGroups ?? [], token)) {
                return true;
            } 
            console.error( `User is not a member of groupId: ${ast_owner}`);
        } else {
            console.error(`No group entry found for groupId: ${ast_owner}`);
        }
    
    } catch (error) {
        console.error(`An error occurred while processing groupId ${ast_owner}:`, error);   
    }
}

const userInAmplifyGroup = async (amplifyGroups, token) => {
    if (amplifyGroups.length === 0) return false;
    console.log("Checking if user is in amplify groups: ", amplifyGroups)

    const apiBaseUrl = process.env.API_BASE_URL;
    if (!apiBaseUrl) {
        console.error("API_BASE_URL environment variable is not set");
        return false;
    }

    try {
        const response = await fetch(`${apiBaseUrl}/amplifymin/verify_amp_member`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({data: {groups: amplifyGroups}})
        });
        
        const responseContent = await response.json();

        if (response.status !== 200 || !responseContent.success) {
            console.error(`Error verifying amp group membership: ${responseContent}`);
            return false;
        } else if (response.status === 200 && responseContent.success) {
            return responseContent.isMember || false;
        }
    } catch (e) {
        console.error(`Error verifying amp group membership: ${e}`);
        
    }
    return false;

}

const getStandaloneAst = async (assistantPublicId, current_user, token) => {
    const ast = await getLatestAssistant(assistantPublicId);
    if (!ast || ! ast.data?.astPath) return null;

    const params = {
        TableName: process.env.ASSISTANT_LOOKUP_DYNAMODB_TABLE,
        Key: {
            astPath: { S: ast.data.astPath }
        }
    };
    
    try {
        const response = await dynamodbClient.send(new GetItemCommand(params));
        
        if (!response.Item) {
            return null;
        }
        
        const item = unmarshall(response.Item);
        
        const accessTo = item.accessTo || {};
        
        if (!item.public) {
            if (current_user !== item.createdBy && 
                !accessTo.users?.includes(current_user) &&
                !await userInAmplifyGroup(accessTo.amplifyGroups || [], token)) {
                return null;
            }
        }
        
        return ast;
        
    } catch (error) {
        console.error('Error looking up standalone assistant:', error);
        return null;
    }
}


export const getAstgGroupId = async (assistantPublicId) => {
    const ast = await getLatestAssistant(assistantPublicId);
    return ast?.data?.groupId;
}

const getLatestAssistant = async (assistantPublicId) => {
    /**
     * Retrieves the most recent version of an assistant from the DynamoDB table.
     *
     * Args:
     *     assistantPublicId (str): The public ID of the assistant (optional).
     *
     * Returns:
     *     object: The most recent assistant item, or null if not found.
     */

    if (assistantPublicId) {
        const command = new QueryCommand({
            TableName: process.env.ASSISTANTS_DYNAMODB_TABLE,
            IndexName: "AssistantIdIndex",
            KeyConditionExpression: "assistantId = :assistantId",
            ExpressionAttributeValues: {
                ":assistantId": { S: assistantPublicId }
            },
            Limit: 1,
            ScanIndexForward: false
        });
        
        try {
            const response = await dynamodbClient.send(command);
            
            if (response.Count > 0) {
                const items = response.Items.map(item => unmarshall(item));
                const astRecord = items.reduce((max, item) => {
                    const itemVersion = item.version || 1;
                    const maxVersion = max.version || 1;
                    return itemVersion > maxVersion ? item : max;
                });
                return astRecord;
            }
        } catch (error) {
            console.error('Error querying assistant:', error);
            return null;
        }
    }

    return null;

}

export const getUserDefinedAssistant = async (current_user, assistantBase, assistantPublicId, token) => {
    const ast_owner = assistantPublicId.startsWith("astgp") ? await getAstgGroupId(assistantPublicId) : current_user;
    
    if (!ast_owner) return null;

    // verify the user has access to the group since this is a group assistant
    if (assistantPublicId.startsWith("astgp") && current_user !== ast_owner) {
        console.log( `Checking if ${current_user} is a member of group: ${ast_owner}`);
        if (!isMemberOfGroup(current_user, ast_owner, token)) return null;
    }
    let assistantData = null;
    const assistantAlias = await getAssistantByAlias(ast_owner, assistantPublicId);

    if (assistantAlias) {
        assistantData = await getAssistantByAssistantDatabaseId(
            assistantAlias.data.id
        );
        console.log("Assistant found by alias: ", assistantData);
    } else {
        //check if ast is standalone
        assistantData = await getStandaloneAst(assistantPublicId, current_user, token);
        console.log("Assistant found by standalone ast: ", assistantData);
    }

    if (assistantData) {
        const userDefinedAssistant =  fillInAssistant(assistantData, assistantBase)
        console.log(`Client Selected Assistant: `, userDefinedAssistant.displayName)
        return userDefinedAssistant;
    }

    return null;
};


export const fillInAssistant = (assistant, assistantBase) => {
    return {
        name: assistant.name,
        displayName: assistant.name,
        handlesDataSources: (ds) => {
            return true;
        },
        handlesModel: (model) => {
            return true;
        },
        description: assistant.description,

        disclaimer: assistant.disclaimer ?? '',

        handler: async (llm, params, body, ds, responseStream) => {

                const references = {};

            params = {
                ...params,
            options:{...params.options, skipRag: assistant.skipRag}
            }

            if(assistant.ragOnly) {
                params = {
                    ...params,
                    options:{...params.options, ragOnly: true}
                }
            }

            const dataSourceOptions = {};
            if(assistant.data && assistant.data.dataSourceOptions) {
                dataSourceOptions.dataSourceOptions = assistant.data.dataSourceOptions;
            }

            const suffixMessages = [];
            const extraMessages = [];

            if (params && params.options){
                if(params.options.timeZone) {
                    extraMessages.push({
                        role: "user",
                        content: "Helpful info, don't repeat: The user is in the " + params.options.timeZone + " time zone."
                    });
                }
                if(params.options.time){
                    extraMessages.push({
                        role: "user",
                        content: "Helpful info, don't repeat: The current time for the user is " + params.options.time
                    });
                }
            }


            if (assistant.data && assistant.data.messageOptions) {
                if(assistant.data.messageOptions.includeMessageIds){

                    const messageIdMapping = {};

                    body.messages = body.messages.map((m, i) => {

                        messageIdMapping[i] = m.id;

                        return {
                            ...m,
                            content: "MsgID: " + i + "\n\n" + m.content
                        };
                    });

                    llm.sendStateEventToStream({
                       messageIdMapping
                    });

                    extraMessages.push({
                        role: "user",
                        content:"You can have references or prior messages inserted into your response by " +
                            "referencing the MsgId like this %^MsgID. Examples %^0, %^1, etc. The reference" +
                            "will be replaced with the content of that message. DO NOT OUTPUT OR TALK ABOUT " +
                            "THESE IDS TO THE USER."
                    });
                }
            }


            if (assistant.data && assistant.data.dataSourceOptions) {

                const dataSourceMetadataForInsertion = [];
                const available = await getDataSourcesByUse(params, body, ds);

                if (assistant.data.dataSourceOptions.insertConversationDocumentsMetadata) {
                        dataSourceMetadataForInsertion.push(...(assistant.dataSources || []));
                        dataSourceMetadataForInsertion.push(...(available.conversationDataSources || []));
                }
                if (assistant.data.dataSourceOptions.insertAttachedDocumentsMetadata ){
                    dataSourceMetadataForInsertion.push(...(available.attachedDataSources || []));
                }

                if (dataSourceMetadataForInsertion.length > 0) {

                        const dataSourceSummaries = dataSourceMetadataForInsertion.map(ds => {

                            // If we have a userDataSourceId, use that, otherwise use the id.
                            // This is important if we need the original file text for any reason.
                            // The hash data source id can't be used to get the file text, but the
                            // user data source id can. The user data source id can also be translated
                            // back to the hash data source id if needed.
                            const dsid =  (ds.metadata && ds.metadata.userDataSourceId) ?
                                ds.metadata.userDataSourceId : ds.id;

                            return {id: dsid, name: ds.name, type:ds.type, metadata:(ds.metadata || {})};
                        });

                        addAllReferences(references, DATASOURCE_TYPE, dataSourceSummaries);
                        const dsR = getReferencesByType(references, DATASOURCE_TYPE);

                        const metadataStr = (r) => {
                            return Object.entries(r.object.metadata).map(
                                ([k,v]) => `${k}:${v}`).join("; ");
                        }

                        const dataSourceText = "Short_ID,NAME,TYPE,METADATA\n" + dsR.map(
                            r => r.type+r.id +","+r.object.name+","+r.object.type+","+metadataStr(r)).join("\n");

                        extraMessages.push({
                            role: "user",
                            content:
                            `You have the following data sources and documents available:
                            -------------                        
                            ${dataSourceText}
                            -------------
                            Any operation that asks for an ID or Key should be supplied with the Short_ID from the list above
                            of the corresponding data source or document. Avoid discussing these IDs, keys, etc. with the user
                            as they can't see them. If they ask you about them, it is OK to tell them and use them for operations, 
                            but otherwise don't describe them in your answers as it might confuse the user.
                            `,
                        });
                }
                if (assistant.data.dataSourceOptions.includeDownloadLinks ) { 
                    extraMessages.push({
                        role: "user",
                        content: `
                        Any documents you reference in your response MUST be formatted in the following way:
                            [<filename>](#dataSource:<filename>)

                            - Any spaces in the file name must be converted to '&' in the (#dataSource:<filename>)
                            Examples:
                            - Original Filename: Project Plan 2023.docx
                              Formatted: [Project Plan 2023.docx](#dataSource:Project&Plan&2023.docx)

                            - Original Filename: monthly_report.pdf
                              Formatted: [monthly_report.pdf)](#dataSource:monthly_report.pdf)
                        
                        Never under any circumstance make up document names. If you are not made aware of any documents then assume you do not have any to reference.
                        `});
                }
            }

            let blockTerminator = null;

            if (assistant.data && assistant.data.opsLanguageVersion === "v4") {

                const statusInfo = newStatus(
                    {
                        animated: true,
                        inProgress: true,
                        sticky: true,
                        summary: `Analyzing Request...`,
                        icon: "info",
                    }
                );

                let workflowTemplateId = assistant.data?.workflowTemplateId ? 
                                          {workflow: {templateId: assistant.data.workflowTemplateId}} : {};

                if (!workflowTemplateId.workflow && assistant.data.baseWorkflowTemplateId) { // backup
                    workflowTemplateId = {workflow: {templateId: assistant.data.baseWorkflowTemplateId}};
                }

                // const segment = AWSXRay.getSegment();
                // const agentSegment = segment.addNewSubsegment('chat-js.userDefinedAssistant.invokeAgent');
                const tools = getTools(body.messages)
                const { builtInOperations, operations } = constructTools(tools);

                const sessionId = params.options.conversationId;

                invokeAgent(
                    params.account.accessToken,
                    sessionId,
                    params.options.requestId,
                    body.messages,
                    {assistant, model: params.model.id, ...workflowTemplateId, 
                     builtInOperations, operations}
                );

                llm.sendStatus(statusInfo);
                llm.forceFlush();
                llm.forceFlush();

                llm.sendStateEventToStream({ agentRun: { startTime: new Date(), sessionId } });
                llm.forceFlush();
                llm.forceFlush();
                llm.endStream();

                return;

            } else if (assistant.data && assistant.data.operations && assistant.data.operations.length > 0) {
                if (assistant.data.opsLanguageVersion !== "custom") {
                    const opsLanguageVersion = assistant.data.opsLanguageVersion || "v1";
                    const langVersion = opsLanguages[opsLanguageVersion];
                    const instructionsPreProcessor = langVersion.instructionsPreProcessor;

                    if (instructionsPreProcessor) {
                        assistant.instructions = instructionsPreProcessor(assistant.instructions);
                    }

                    const langMessages = langVersion.messages;
                    blockTerminator = langVersion.blockTerminator;
                    extraMessages.push(...langMessages);
                    suffixMessages.push(...(langVersion.suffixMessages || []));
                }
                const containsIntegrations = assistant.data.operations.some(op => op.tags.includes("integration"));
                if (containsIntegrations) {
                    const integrationInstructions = `
                    If you detect an error message regarding *api credentials* please respond with your message and then finish with:

                        \`\`\` integrationsDialog 
                        \`\`\`

                    You must provide this exactly with the 3 tick marks and a newline, so i can render a button. Let the user know they can connect to the service by clicking on the button. 

                    This only applies when you have been notified there was an error regarding *api credentials* otherwise DO NOT mention it
                    `;
                    assistant.instructions += "\n\n" + integrationInstructions;
                }

            }


            const messagesWithoutSystem = body.messages.filter(
                (message) => message.role !== "system"
            );

            const groupType = body.options.groupType;
            if (groupType) {
                const groupTypeData = assistant.data.groupTypeData[groupType];
                if (!groupTypeData.isDisabled) {
                    assistant.instructions += "\n\n" + groupTypeData.additionalInstructions;
                    assistant.dataSources = [...assistant.dataSources, ...groupTypeData.dataSources];
                }
            }

            if (assistant.data && assistant.data.supportConvAnalysis) {
                body.options.analysisCategories = assistant.data?.analysisCategories ?? [];
            }

            if (assistant.data && assistant.data.trackConversations) {
                body.options.trackConversations = true;
            }

            const instructions = await fillInTemplate(
                llm,
                params,
                body,
                ds,
                assistant.instructions,
                {
                    assistant: assistant,
                    operations: assistant.data?.operations || []
                }
            );

                llm.sendStateEventToStream({
                    references: getReferences(references),
                    opsConfig: {
                        opFormat: {
                            name: "functionCalling",
                            type: "regex",
                            opPattern: "(\\w+)\\s*\\((.*)\\)\\s*$",
                            opIdGroup: 1,
                            opArgsGroup: 2,
                        },
                    }
                })

                const updatedBody = {
                    ...body,
                    messages: [
                        ...messagesWithoutSystem.slice(0,-1),
                        {
                            role: "user",
                            content: "Pay close attention to any provided information. Unless told otherwise, " +
                                "cite the information you are provided with quotations supporting your analysis " +
                                "the [Page X, Slide Y, Paragraph Q, etc.] of the quotation.",
                            data: {dataSources: extractAssistantDatasources(assistant)}
                        },
                        {
                            role: 'system',
                            content: instructions,
                        },
                        ...extraMessages,
                        body.messages.slice(-1)[0],
                        ...suffixMessages
                    ],
                    options: {
                        ...body.options,
                        ...dataSourceOptions,
                        prompt: instructions,
                        skipDocumentCache: true, // always rag documents for now
                        skipRag: assistant.skipRag
                    }
                };
            
            // for now we will include the ds in the current message
            if (assistant.dataSources && !dataSourceOptions.disableDataSources) updatedBody.imageSources =  [...(updatedBody.imageSources || []), ...assistant.dataSources.filter(ds => isImage(ds))];

            await assistantBase.handler(
                llm,
                {...params, blockTerminator: blockTerminator || params.blockTerminator},
                updatedBody,
                ds,
                responseStream);

            try {
                if (assistant.data && assistant.data.logChats) {
                    const user = assistant.data.logAnonymously ?
                        "anonymous" : params.account.user;

                    await saveChatToS3(
                        assistant,
                        user,
                        body,
                        {
                            user: params.account.user,
                            assistant: assistant,
                            dataSources: ds,
                        }
                    );
                }
            } catch (e) {
                console.error('Error logging assistant chat to S3:', e);
            }
        }
    };

}


function extractDriveDatasources(data) {
    if (!data) return [];
    return Object.values(data)
        .filter(providerData => providerData && typeof providerData === 'object')
        .flatMap(providerData => [
            // Extract from files
            ...(providerData.files ? Object.values(providerData.files) : []),
            // Extract from folders
            ...(providerData.folders ? 
                Object.values(providerData.folders).flatMap(folderFiles => 
                    Object.values(folderFiles)
                ) : []
            )
        ])
        .map(fileMetadata => fileMetadata.datasource)
        .filter(datasource => datasource && datasource.id);
}

function extractAssistantDatasources(assistant) {
    if (!assistant) return [];

    if (assistant.data?.integrationDriveData) {
        const driveDatasources = extractDriveDatasources(assistant.data.integrationDriveData);
        // console.log("Drive datasources: ", driveDatasources);
        assistant.dataSources = [...assistant.dataSources, ...driveDatasources];
    }

    // update ds with astp for dual embedding object access check 
    if (assistant.data?.astPath) {
        assistant.dataSources.forEach(ds => {
            ds.ast = assistant.assistantId;
        });
    }

    return assistant.dataSources || [];
}