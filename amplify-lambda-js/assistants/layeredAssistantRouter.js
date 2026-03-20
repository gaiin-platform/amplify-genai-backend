// Copyright (c) 2024 Vanderbilt University
// Layered Assistant Router
//
// Phase-3 chat integration:
//   When a user submits a chat with assistantId == "astr/<uuid>" (personal) or
//   "astgr/<uuid>" (group), this module:
//
//   1. Fetches the LayeredAssistant record from DynamoDB
//   2. Checks access (owner for personal, group membership for group)
//   3. Walks the RouterNode tree using the LLM to pick children at each level
//      — each step sends a status event so the user sees routing progress
//   4. Returns the final LeafNode's assistantId (a normal assistant publicId)
//      which is then passed back into getUserDefinedAssistant in the normal flow.

import { DynamoDBClient, QueryCommand, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import { promptUnifiedLLMForData } from "../llm/UnifiedLLMClient.js";
import { newStatus } from "../common/status.js";
import { sendStatusEventToStream, forceFlush } from "../common/streams.js";
import { getLogger } from "../common/logging.js";
import { getLatestAssistant } from "./userDefinedAssistants.js";

const objectPermissionsEndpoint = process.env.API_BASE_URL + "/utilities/can_access_objects";

const logger = getLogger("layered-assistant-router");

const PERSONAL_PREFIX = "astr/";
const GROUP_PREFIX    = "astgr/";

// Maximum router depth — prevents infinite loops in pathological trees
const MAX_DEPTH = 10;

const dynamoClient = new DynamoDBClient({});

// ── DynamoDB helpers ──────────────────────────────────────────────────────────

/**
 * Fetch a LayeredAssistant item by its publicId via the PublicIdIndex GSI.
 * Returns the unmarshalled item or null.
 */
async function getLayeredAssistantByPublicId(publicId) {
    const tableName = process.env.LAYERED_ASSISTANTS_DYNAMODB_TABLE;
    if (!tableName) {
        logger.error("LAYERED_ASSISTANTS_DYNAMODB_TABLE env var not set");
        return null;
    }

    try {
        const command = new QueryCommand({
            TableName: tableName,
            IndexName: "PublicIdIndex",
            KeyConditionExpression: "publicId = :pid",
            ExpressionAttributeValues: { ":pid": { S: publicId } },
            Limit: 1,
        });
        const response = await dynamoClient.send(command);
        if (response.Count > 0 && response.Items?.length > 0) {
            return unmarshall(response.Items[0]);
        }
        logger.warn("No layered assistant found for publicId:", publicId);
        return null;
    } catch (err) {
        logger.error("Error querying layered assistant by publicId:", err);
        return null;
    }
}

// ── Access checks ─────────────────────────────────────────────────────────────

/**
 * Personal LA: caller is the owner, OR they have at least "read" access
 * via the object permissions system (used for shares).
 */
async function canAccessPersonal(item, user, token) {
    // Owner always has access
    if (item.createdBy === user) return true;

    // Check object permissions — shares grant "read" access
    const publicId = item.publicId;
    const dbId = item.id;
    if (!publicId || !dbId) return false;

    try {
        const response = await fetch(objectPermissionsEndpoint, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({
                data: {
                    dataSources: {
                        [publicId]: "read",
                        [dbId]:     "read",
                    },
                },
            }),
        });

        const responseBody = await response.json();
        const statusCode = responseBody.statusCode || undefined;
        if (response.status === 200 && statusCode === 200) return true;

        logger.warn(`User ${user} denied read access to personal LA ${publicId}`);
        return false;
    } catch (err) {
        logger.error("Error checking object permissions for personal LA:", err);
        return false;
    }
}

/**
 * Group LA: caller must be a member of the group that owns this LA.
 * Mirrors the isMemberOfGroup logic in userDefinedAssistants.js.
 */
async function canAccessGroup(item, user, token) {
    const groupId = item.groupId;
    if (!groupId) {
        logger.warn("Group LA has no groupId stored:", item.publicId);
        return false;
    }

    const groupTable = process.env.ASSISTANT_GROUPS_DYNAMO_TABLE;
    if (!groupTable) {
        logger.error("ASSISTANT_GROUPS_DYNAMO_TABLE env var not set");
        return false;
    }

    try {
        const response = await dynamoClient.send(new GetItemCommand({
            TableName: groupTable,
            Key: { group_id: { S: groupId } },
        }));

        if (!response.Item) {
            logger.error(`No group record found for groupId: ${groupId}`);
            return false;
        }

        const group = unmarshall(response.Item);

        // Public group — anyone can access
        if (group.isPublic) return true;

        // Direct member check
        if (group.members && Object.keys(group.members).includes(user)) return true;

        // System user check
        if (group.systemUsers && group.systemUsers.includes(user)) return true;

        // Amplify group membership check
        if (group.amplifyGroups?.length > 0) {
            const isMember = await checkAmplifyGroupMembership(group.amplifyGroups, token);
            if (isMember) return true;
        }

        logger.warn(`User ${user} is not a member of group ${groupId}`);
        return false;
    } catch (err) {
        logger.error("Error checking group membership for layered assistant:", err);
        return false;
    }
}

async function checkAmplifyGroupMembership(amplifyGroups, token) {
    const apiBaseUrl = process.env.API_BASE_URL;
    if (!apiBaseUrl) return false;

    try {
        const response = await fetch(`${apiBaseUrl}/amplifymin/verify_amp_member`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({ data: { groups: amplifyGroups } }),
        });
        const json = await response.json();
        return response.status === 200 && json.success && (json.isMember || false);
    } catch (err) {
        logger.error("Error verifying amplify group membership:", err);
        return false;
    }
}

// ── Status helpers ─────────────────────────────────────────────────────────────

function sendRouterStatus(responseStream, message, inProgress = true) {
    sendStatusEventToStream(responseStream, newStatus({
        id: "RouterAst",
        inProgress,
        message,
        icon: "assistant",
        sticky: true,
    }));
    forceFlush(responseStream);
}

// ── LLM routing ───────────────────────────────────────────────────────────────

/**
 * For a LeafNode with routerContext fields set, fetch the actual assistant record
 * and append the requested extra details (dataSources / tools / workflow) to its
 * description so the routing LLM has richer signal when choosing between children.
 *
 * routerContext values: 'dataSources' | 'tools' | 'workflow'
 */
async function enrichLeafDescription(leafNode) {
    const base = leafNode.description || "(no description)";
    const context = leafNode.routerContext;

    // ── BREAKPOINT 1: entry — did this leaf have routerContext configured?
    logger.info(`[enrichLeaf] leaf="${leafNode.name}" (${leafNode.assistantId}) | routerContext=${JSON.stringify(context)}`);

    if (!context || context.length === 0) {
        logger.info(`[enrichLeaf] leaf="${leafNode.name}" — no routerContext, using base description only`);
        return base;
    }

    let ast = null;
    try {
        ast = await getLatestAssistant(leafNode.assistantId);
    } catch (err) {
        logger.warn(`Could not fetch assistant data for leaf "${leafNode.name}" (${leafNode.assistantId}):`, err);
        return base;
    }

    // ── BREAKPOINT 2: did we get the assistant record back at all?
    if (!ast) {
        logger.warn(`[enrichLeaf] leaf="${leafNode.name}" — getLatestAssistant returned null/undefined, falling back to base description`);
        return base;
    }
    logger.info(`[enrichLeaf] leaf="${leafNode.name}" — assistant record fetched OK. Keys present: ${Object.keys(ast).join(", ")}`);

    const extras = [];

    if (context.includes("dataSources")) {
        const sources = ast.dataSources || [];
        // ── BREAKPOINT 3a: dataSources pulled from assistant record
        logger.info(`[enrichLeaf] leaf="${leafNode.name}" — dataSources raw count: ${sources.length}`, sources.map(d => ({ name: d.name, id: d.id })));
        if (sources.length > 0) {
            const names = sources.map(d => d.name || d.id).filter(Boolean).join(", ");
            extras.push(`Data sources: ${names}`);
            logger.info(`[enrichLeaf] leaf="${leafNode.name}" — dataSources appended: "${names}"`);
        } else {
            logger.info(`[enrichLeaf] leaf="${leafNode.name}" — dataSources requested but none found on assistant record`);
        }
    }

    if (context.includes("tools")) {
        const ops = ast.data?.operations || [];
        // ── BREAKPOINT 3b: tools/operations pulled from assistant record
        logger.info(`[enrichLeaf] leaf="${leafNode.name}" — tools/operations raw count: ${ops.length}`, ops.map(o => ({ name: o.name, id: o.id })));
        if (ops.length > 0) {
            const names = ops.map(o => o.name || o.id).filter(Boolean).join(", ");
            extras.push(`Tools/Operations: ${names}`);
            logger.info(`[enrichLeaf] leaf="${leafNode.name}" — tools appended: "${names}"`);
        } else {
            logger.info(`[enrichLeaf] leaf="${leafNode.name}" — tools requested but none found on ast.data.operations`);
        }
    }

    if (context.includes("workflow")) {
        const wfId = ast.data?.workflowTemplateId || ast.data?.baseWorkflowTemplateId;
        // ── BREAKPOINT 3c: workflow id pulled from assistant record
        logger.info(`[enrichLeaf] leaf="${leafNode.name}" — workflowTemplateId="${ast.data?.workflowTemplateId}" baseWorkflowTemplateId="${ast.data?.baseWorkflowTemplateId}"`);
        if (wfId) {
            extras.push(`Workflow template: ${wfId}`);
            logger.info(`[enrichLeaf] leaf="${leafNode.name}" — workflow appended: "${wfId}"`);
        } else {
            logger.info(`[enrichLeaf] leaf="${leafNode.name}" — workflow requested but no workflowTemplateId found on assistant record`);
        }
    }

    const finalDescription = extras.length > 0 ? `${base}\n${extras.join("\n")}` : base;

    // ── BREAKPOINT 4: final enriched description that will go into the LLM prompt
    logger.info(`[enrichLeaf] leaf="${leafNode.name}" — FINAL enriched description:\n---\n${finalDescription}\n---`);

    return finalDescription;
}

/**
 * Ask the LLM to pick one child from a RouterNode.
 * Returns the child node object, or null if selection fails.
 */
async function pickChildNode(routerNode, userMessage, account, model, requestId, conversationHistory = []) {
    const children = routerNode.children || [];
    if (children.length === 0) return null;

    // If there's only one child, skip the LLM call entirely
    if (children.length === 1) {
        logger.info(`Router "${routerNode.name}" has a single child — auto-selecting.`);
        return children[0];
    }

    // Build the child list — for leaf nodes with routerContext, fetch and append
    // their assistant's actual data (dataSources, tools, workflow) so the LLM
    // has richer signal when picking the best child for the user's request.
    const childDescriptions = await Promise.all(
        children.map(async (c) => {
            const desc = (c.type === "leaf" && c.routerContext?.length > 0)
                ? await enrichLeafDescription(c)
                : (c.description || "(no description)");
            return `ID: ${c.id}\nName: ${c.name}\nDescription: ${desc}`;
        })
    );
    const childList = childDescriptions.join("\n\n");

    // ── BREAKPOINT 5: full child list that goes into the LLM routing prompt
    logger.info(`[pickChildNode] router="${routerNode.name}" — LLM child list:\n===\n${childList}\n===`);

    // Include prior conversation turns so the router can handle follow-up messages
    // that only make sense in context (e.g. "do the same for Python").
    // Filters applied:
    //  - Drop system messages (routing prompt handles that)
    //  - Drop tool/function role messages (raw API results confuse the routing LLM)
    //  - Drop assistant messages that are purely tool invocations (```auto ...``` blocks)
    //    because Bedrock sees those as incomplete tool turns and refuses to produce JSON
    //  - Cap at the 10 most recent qualifying messages to keep the prompt lean
    const TOOL_INVOCATION_RE = /^\s*```[\w]*\n[\s\S]*?\n```\s*$/;
    const historyMessages = conversationHistory
        .filter(m => {
            if (!m.content) return false;
            if (m.role === "system" || m.role === "tool" || m.role === "function") return false;
            const text = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
            // Drop assistant messages that are only a tool-call code block — no prose
            if (m.role === "assistant" && TOOL_INVOCATION_RE.test(text)) return false;
            return true;
        })
        .slice(-10)
        .map(m => ({ role: m.role, content: typeof m.content === "string" ? m.content : JSON.stringify(m.content) }));

    const messages = [
        {
            role: "system",
            content:
                `You are an intelligent routing assistant. Your job is to select the single best option ` +
                `from the list below to handle the user's request.\n\n` +
                `Routing instructions:\n${routerNode.instructions || "Choose the most relevant assistant."}\n\n` +
                `Available options:\n${childList}\n\n` +
                `${historyMessages.length > 0 ? "The user's conversation history is provided for context — use it to understand follow-up or ambiguous messages, but focus primarily on the most recent user message when routing.\n\n" : ""}` +
                `You MUST select exactly one option. ` +
                `Return only the ID field of your chosen option.`,
        },
        ...historyMessages,
        {
            role: "user",
            content: userMessage,
        },
    ];

    try {
        const result = await promptUnifiedLLMForData(
            {
                account,
                options: {
                    model,
                    requestId: `${requestId}_route_${Date.now()}`,
                    disableReasoning: true,
                    skipHistoricalContext: true,
                },
            },
            messages,
            {
                type: "object",
                properties: {
                    selectedId: {
                        type: "string",
                        description: "The exact 'ID' value of the chosen child node.",
                    },
                },
                required: ["selectedId"],
            },
            null // no streaming
        );

        const selectedId = result?.selectedId;
        if (!selectedId) {
            logger.warn(`LLM returned no selectedId for router "${routerNode.name}" — defaulting to first child`);
            return children[0];
        }

        const chosen = children.find(c => c.id === selectedId);
        if (!chosen) {
            logger.warn(
                `LLM returned unknown id "${selectedId}" for router "${routerNode.name}" — defaulting to first child`
            );
            return children[0];
        }

        logger.info(`Router "${routerNode.name}" → selected child "${chosen.name}" (${chosen.id})`);
        return chosen;

    } catch (err) {
        logger.error(`LLM routing error at router "${routerNode.name}":`, err);
        return children[0]; // graceful fallback
    }
}

/**
 * Recursively walk the LayeredAssistant tree.
 * Returns the final leaf's assistantId, or null if the tree is invalid.
 */
async function walkTree(node, userMessage, account, model, requestId, responseStream, depth = 0, conversationHistory = []) {
    if (depth > MAX_DEPTH) {
        logger.error("Layered assistant tree exceeded max depth — aborting routing");
        sendRouterStatus(responseStream, "Routing depth limit reached. Using default assistant.", false);
        return null;
    }

    if (node.type === "leaf") {
        // We have our answer
        logger.info(`Resolved to leaf assistant: "${node.name}" → assistantId: ${node.assistantId}`);
        sendRouterStatus(
            responseStream,
            `Selected assistant: ${node.name}`,
            false
        );
        return node.assistantId;
    }

    if (node.type === "router") {
        const childCount = (node.children || []).length;
        sendRouterStatus(
            responseStream,
            `${node.name}: choosing from ${childCount} assistant${childCount !== 1 ? "s" : ""}…`,
            true
        );

        const chosenChild = await pickChildNode(node, userMessage, account, model, requestId, conversationHistory);
        if (!chosenChild) {
            logger.error(`Router "${node.name}" has no children — cannot route`);
            sendRouterStatus(responseStream, "Routing failed: empty router node.", false);
            return null;
        }

        return walkTree(chosenChild, userMessage, account, model, requestId, responseStream, depth + 1, conversationHistory);
    }

    logger.error("Unknown node type encountered during routing:", node.type);
    return null;
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Determine whether a given assistantId is a Layered Assistant public ID.
 */
export function isLayeredAssistantId(assistantId) {
    return (
        typeof assistantId === "string" &&
        (assistantId.startsWith(PERSONAL_PREFIX) || assistantId.startsWith(GROUP_PREFIX))
    );
}

/**
 * Resolve a LayeredAssistant publicId down to a concrete leaf assistantId.
 *
 * @param {object} account        - { user, accessToken }
 * @param {object} model          - the current chat model object
 * @param {object} body           - the chat request body (needs body.messages)
 * @param {string} publicId       - "astr/<uuid>" or "astgr/<uuid>"
 * @param {object} responseStream - the response stream for status events
 * @returns {Promise<string|null>} - the leaf assistantId, or null if access denied / not found / error
 */
export async function resolveLayeredAssistant(account, model, body, publicId, responseStream) {
    const { user, accessToken: token } = account;

    logger.info(`Resolving layered assistant: ${publicId} for user: ${user}`);

    sendRouterStatus(responseStream, "Routing your request…", true);


    // ── 1. Fetch the record ────────────────────────────────────────────────────
    const item = await getLayeredAssistantByPublicId(publicId);
    if (!item) {
        sendRouterStatus(responseStream, "Layered assistant not found.", false);
        return null;
    }

    // ── 2. Access check ────────────────────────────────────────────────────────
    const isGroup = publicId.startsWith(GROUP_PREFIX);

    if (isGroup) {
        const hasAccess = await canAccessGroup(item, user, token);
        if (!hasAccess) {
            sendRouterStatus(responseStream, "You don't have access to this layered assistant.", false);
            return null;
        }
    } else {
        if (!canAccessPersonal(item, user)) {
            sendRouterStatus(responseStream, "You don't have access to this layered assistant.", false);
            return null;
        }
    }

    // ── 3. Validate the tree ───────────────────────────────────────────────────
    const rootNode = item.rootNode;
    if (!rootNode) {
        logger.error("Layered assistant has no rootNode:", publicId);
        sendRouterStatus(responseStream, "Layered assistant is not configured correctly.", false);
        return null;
    }

    // ── 4. Extract the user's latest message + prior history for routing ────────
    const messages = body.messages || [];
    const lastUserMsg = messages
        .slice()
        .reverse()
        .find(m => m.role === "user");
    const userMessage = lastUserMsg?.content || "";

    // All messages except the last user turn — used as conversation history
    // context so the router can handle follow-up messages correctly.
    const lastUserIdx = messages.map(m => m.role).lastIndexOf("user");
    const historyMessages = lastUserIdx > 0 ? messages.slice(0, lastUserIdx) : [];

    // ── 5. Walk the tree ────────────────────────────────────────────────────────
    const requestId = body.options?.requestId || `layered_${Date.now()}`;
    const resolvedAssistantId = await walkTree(
        rootNode,
        userMessage,
        account,
        model,
        requestId,
        responseStream,
        0,
        historyMessages
    );

    if (!resolvedAssistantId) {
        sendRouterStatus(responseStream, "Routing failed. Using default assistant.", false);
        return null;
    }

    logger.info(`Layered assistant ${publicId} resolved to: ${resolvedAssistantId}`);
    return resolvedAssistantId;
}
