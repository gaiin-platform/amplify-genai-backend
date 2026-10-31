//Copyright (c) 2024 Vanderbilt University
//
// Single implementation of the group / amplify-group membership checks used by
// userDefinedAssistants.js (group assistants, standalone assistants) and
// layeredAssistantRouter.js (personal and group layered assistants).
//
// Both functions ALWAYS resolve to a real boolean and fail closed (false) on any
// missing configuration, missing record, non-200 response or error. Callers must
// `await` them: an un-awaited call returns a truthy Promise.

import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import { getLogger } from "../common/logging.js";

const logger = getLogger("group-membership");

const dynamodbClient = new DynamoDBClient({ });

/**
 * Is the caller (identified by `token`) a member of any of the given amplify groups?
 * @param {string[]} amplifyGroups
 * @param {string} token - the caller's access token
 * @returns {Promise<boolean>}
 */
export const userInAmplifyGroup = async (amplifyGroups, token) => {
    if (!amplifyGroups || amplifyGroups.length === 0) return false;
    logger.debug("Checking if user is in amplify groups:", amplifyGroups)

    const apiBaseUrl = process.env.API_BASE_URL;
    if (!apiBaseUrl) {
        logger.error("API_BASE_URL environment variable is not set");
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

        if (response.status !== 200 || responseContent?.success !== true) {
            logger.error(`Error verifying amp group membership: status ${response.status}`, responseContent);
            return false;
        }
        return responseContent.isMember === true;
    } catch (e) {
        logger.error(`Error verifying amp group membership: ${e}`);
    }
    return false;
}

/**
 * Is `user` allowed to use the assistant group `groupId`?
 * True when the group is public, the user is a direct member or system user, or the
 * user is in one of the group's amplify groups.
 * @param {string} user
 * @param {string} groupId
 * @param {string} token - the caller's access token
 * @returns {Promise<boolean>}
 */
export const isMemberOfGroup = async (user, groupId, token) => {
    const tableName = process.env.ASSISTANT_GROUPS_DYNAMO_TABLE;
    if (!tableName) {
        logger.error("ASSISTANT_GROUPS_DYNAMO_TABLE env var not set");
        return false;
    }

    try {
        const response = await dynamodbClient.send(new GetItemCommand({
            TableName: tableName,
            Key: { group_id: { S: groupId } }
        }));

        if (!response.Item) {
            logger.error(`No group entry found for groupId: ${groupId}`);
            return false;
        }

        const group = unmarshall(response.Item);
        if (group.isPublic ||
            (group.members && Object.keys(group.members).includes(user)) ||
            (group.systemUsers && group.systemUsers.includes(user)) ||
            await userInAmplifyGroup(group.amplifyGroups ?? [], token)) {
            return true;
        }
        logger.warn(`User is not a member of groupId: ${groupId}`);
    } catch (error) {
        logger.error(`An error occurred while processing groupId ${groupId}:`, error);
    }
    return false;
}
