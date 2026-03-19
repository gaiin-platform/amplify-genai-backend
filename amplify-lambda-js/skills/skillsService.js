//Copyright (c) 2026 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas, Jagadeesh Reddy Vanga, Tina Seviert

import { DynamoDBClient, PutItemCommand, GetItemCommand, QueryCommand, DeleteItemCommand, UpdateItemCommand, ScanCommand } from "@aws-sdk/client-dynamodb";
import { marshall, unmarshall } from "@aws-sdk/util-dynamodb";
import { v4 as uuidv4 } from 'uuid';
import { getLogger } from "../common/logging.js";

const logger = getLogger("skills-service");
const dynamodbClient = new DynamoDBClient({});

// Cache for user skills to reduce DynamoDB calls
import { lru } from "tiny-lru";
const userSkillsCache = lru(500, 60000, false); // 1 minute cache

// Helper to invalidate all cache entries for a user
const invalidateUserCache = (user) => {
    userSkillsCache.delete(`${user}:true`);
    userSkillsCache.delete(`${user}:false`);
};

/**
 * Normalize skill object for API response
 * Converts isPublic from string to boolean for frontend compatibility
 */
const normalizeSkill = (skill) => {
    if (!skill) return skill;
    return {
        ...skill,
        isPublic: skill.isPublic === "true"
    };
};

/**
 * Parse skill markdown frontmatter to extract configuration
 * @param {string} content - Full markdown content with frontmatter
 * @returns {Object} Parsed config and body
 */
export const parseSkillContent = (content) => {
    if (!content) return { config: {}, body: '', raw: '' };

    const frontmatterRegex = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/;
    const match = content.match(frontmatterRegex);

    if (match) {
        const yamlSection = match[1];
        const body = match[2];
        const config = parseYamlFrontmatter(yamlSection);
        return { config, body, raw: content };
    }

    return { config: {}, body: content, raw: content };
};

/**
 * Simple YAML frontmatter parser for skill metadata
 * @param {string} yaml - YAML string
 * @returns {Object} Parsed configuration
 */
const parseYamlFrontmatter = (yaml) => {
    const config = {};

    try {
        // Parse name
        const nameMatch = yaml.match(/name:\s*["']?([^"'\n]+)["']?/);
        if (nameMatch) config.name = nameMatch[1].trim();

        // Parse description
        const descMatch = yaml.match(/description:\s*["']?([^"'\n]+)["']?/);
        if (descMatch) config.description = descMatch[1].trim();

        // Parse tags array
        const tagsMatch = yaml.match(/tags:\s*\[([^\]]*)\]/);
        if (tagsMatch) {
            config.tags = tagsMatch[1]
                .split(',')
                .map(t => t.trim().replace(/["']/g, ''))
                .filter(Boolean);
        }

        // Parse triggerPhrases array
        const triggerMatch = yaml.match(/triggerPhrases:\s*\[([^\]]*)\]/);
        if (triggerMatch) {
            config.triggerPhrases = triggerMatch[1]
                .split(',')
                .map(t => t.trim().replace(/["']/g, ''))
                .filter(Boolean);
        }

        // Parse priority
        const priorityMatch = yaml.match(/priority:\s*(\d+)/);
        if (priorityMatch) config.priority = parseInt(priorityMatch[1]);

        // Parse category
        const categoryMatch = yaml.match(/category:\s*["']?([^"'\n]+)["']?/);
        if (categoryMatch) config.category = categoryMatch[1].trim();

    } catch (e) {
        logger.error("Error parsing YAML frontmatter:", e);
    }

    return config;
};

/**
 * Create a new skill for a user
 * @param {string} user - User ID
 * @param {Object} skillData - Skill data
 * @returns {Object} Created skill
 */
export const createSkill = async (user, skillData) => {
    const skillId = `skill_${uuidv4()}`;
    const now = new Date().toISOString();
    const parsed = parseSkillContent(skillData.content);

    const skill = {
        id: skillId,
        user: user,
        name: skillData.name || parsed.config.name || "Untitled Skill",
        description: skillData.description || parsed.config.description || "",
        content: skillData.content || "",
        tags: skillData.tags || parsed.config.tags || [],
        triggerPhrases: skillData.triggerPhrases || parsed.config.triggerPhrases || [],
        priority: skillData.priority || parsed.config.priority || 5,
        category: skillData.category || parsed.config.category || "general",
        isEnabled: skillData.isEnabled !== undefined ? skillData.isEnabled : true,
        isPublic: skillData.isPublic ? "true" : "false",
        metadata: {
            version: 1,
            createdAt: now,
            updatedAt: now,
            usageCount: 0,
            lastUsedAt: null
        }
    };

    const tableName = process.env.SKILLS_DYNAMODB_TABLE;
    if (!tableName) {
        throw new Error("SKILLS_DYNAMODB_TABLE environment variable not set");
    }

    await dynamodbClient.send(new PutItemCommand({
        TableName: tableName,
        Item: marshall(skill, { removeUndefinedValues: true })
    }));

    // Invalidate cache
    invalidateUserCache(user);

    logger.info(`Created skill ${skillId} for user ${user}`);
    return normalizeSkill(skill);
};

/**
 * Get all skills for a user (owned + shared)
 * @param {string} user - User ID
 * @param {boolean} includeShared - Whether to include shared skills
 * @returns {Array} List of skills
 */
export const getUserSkills = async (user, includeShared = true) => {
    // Check cache first
    const cacheKey = `${user}:${includeShared}`;
    const cached = userSkillsCache.get(cacheKey);
    if (cached) {
        logger.debug(`Cache HIT: Skills for user ${user}`);
        return cached;
    }

    const tableName = process.env.SKILLS_DYNAMODB_TABLE;
    if (!tableName) {
        throw new Error("SKILLS_DYNAMODB_TABLE environment variable not set");
    }

    // Get owned skills
    const ownedResponse = await dynamodbClient.send(new QueryCommand({
        TableName: tableName,
        IndexName: "user-index",
        KeyConditionExpression: "#user = :user",
        ExpressionAttributeNames: { "#user": "user" },
        ExpressionAttributeValues: marshall({ ":user": user })
    }));

    let skills = ownedResponse.Items?.map(item => normalizeSkill(unmarshall(item))) || [];

    // Get shared skills if requested
    if (includeShared) {
        const sharedSkillIds = await getSharedSkillIds(user);
        for (const skillId of sharedSkillIds) {
            const skill = await getSkillById(skillId);
            if (skill && !skills.find(s => s.id === skillId)) {
                skills.push({ ...skill, isShared: true });
            }
        }
    }

    // Sort by priority (descending) then by name
    skills.sort((a, b) => {
        if (b.priority !== a.priority) return b.priority - a.priority;
        return a.name.localeCompare(b.name);
    });

    // Cache the result
    userSkillsCache.set(cacheKey, skills);

    logger.debug(`Found ${skills.length} skills for user ${user}`);
    return skills;
};

/**
 * Get a single skill by ID
 * @param {string} skillId - Skill ID
 * @param {string} requestingUser - User requesting (for permission check)
 * @returns {Object|null} Skill or null if not found/unauthorized
 */
export const getSkillById = async (skillId, requestingUser = null) => {
    const tableName = process.env.SKILLS_DYNAMODB_TABLE;
    if (!tableName) {
        throw new Error("SKILLS_DYNAMODB_TABLE environment variable not set");
    }

    const response = await dynamodbClient.send(new GetItemCommand({
        TableName: tableName,
        Key: marshall({ id: skillId })
    }));

    if (!response.Item) return null;

    const skill = unmarshall(response.Item);

    // Check permissions if user specified
    if (requestingUser && skill.user !== requestingUser) {
        const hasAccess = await checkSkillAccess(skillId, requestingUser);
        if (!hasAccess && skill.isPublic !== "true") {
            logger.warn(`User ${requestingUser} does not have access to skill ${skillId}`);
            return null;
        }
    }

    return normalizeSkill(skill);
};

/**
 * Update an existing skill
 * @param {string} user - User ID (must be owner)
 * @param {string} skillId - Skill ID
 * @param {Object} updates - Fields to update
 * @returns {Object} Updated skill
 */
export const updateSkill = async (user, skillId, updates) => {
    // Verify ownership
    const existing = await getSkillById(skillId);
    if (!existing || existing.user !== user) {
        throw new Error("Skill not found or unauthorized");
    }

    const tableName = process.env.SKILLS_DYNAMODB_TABLE;
    const now = new Date().toISOString();

    // Re-parse content if updated
    let parsedConfig = {};
    if (updates.content) {
        const parsed = parseSkillContent(updates.content);
        parsedConfig = parsed.config;
    }

    // Build update expression
    const updateExpressions = [];
    const expressionAttributeNames = {};
    const expressionAttributeValues = {};

    const updatableFields = ['name', 'description', 'content', 'tags', 'triggerPhrases', 'priority', 'category', 'isEnabled', 'isPublic'];

    for (const field of updatableFields) {
        if (updates[field] !== undefined) {
            updateExpressions.push(`#${field} = :${field}`);
            expressionAttributeNames[`#${field}`] = field;
            // Convert isPublic boolean to string for GSI compatibility
            if (field === 'isPublic') {
                expressionAttributeValues[`:${field}`] = updates[field] ? "true" : "false";
            } else {
                expressionAttributeValues[`:${field}`] = updates[field];
            }
        }
    }

    // Always update metadata
    updateExpressions.push("metadata.updatedAt = :updatedAt");
    updateExpressions.push("metadata.version = metadata.version + :one");
    expressionAttributeValues[":updatedAt"] = now;
    expressionAttributeValues[":one"] = 1;

    await dynamodbClient.send(new UpdateItemCommand({
        TableName: tableName,
        Key: marshall({ id: skillId }),
        UpdateExpression: `SET ${updateExpressions.join(", ")}`,
        ExpressionAttributeNames: Object.keys(expressionAttributeNames).length > 0 ? expressionAttributeNames : undefined,
        ExpressionAttributeValues: marshall(expressionAttributeValues)
    }));

    // Invalidate cache
    invalidateUserCache(user);

    logger.info(`Updated skill ${skillId} for user ${user}`);
    return await getSkillById(skillId);
};

/**
 * Delete a skill
 * @param {string} user - User ID (must be owner)
 * @param {string} skillId - Skill ID
 * @returns {Object} Success status
 */
export const deleteSkill = async (user, skillId) => {
    const existing = await getSkillById(skillId);
    if (!existing || existing.user !== user) {
        throw new Error("Skill not found or unauthorized");
    }

    const tableName = process.env.SKILLS_DYNAMODB_TABLE;

    // Delete all shares first
    await deleteSkillShares(skillId);

    await dynamodbClient.send(new DeleteItemCommand({
        TableName: tableName,
        Key: marshall({ id: skillId })
    }));

    // Invalidate cache
    invalidateUserCache(user);

    logger.info(`Deleted skill ${skillId} for user ${user}`);
    return { success: true };
};

/**
 * Auto-select skills based on user message and context
 * @param {string} user - User ID
 * @param {string} userMessage - Current user message
 * @param {Object} context - Conversation context (tags, assistant info, etc.)
 * @returns {Array} Selected skills
 */
export const autoSelectSkills = async (user, userMessage, context = {}) => {
    const availableSkills = await getUserSkills(user, true);
    const enabledSkills = availableSkills.filter(s => s.isEnabled);

    if (enabledSkills.length === 0) return [];

    const selectedSkills = [];
    const messageLower = userMessage.toLowerCase();
    const messageWords = messageLower.split(/\s+/).filter(w => w.length > 2);

    for (const skill of enabledSkills) {
        let matchScore = 0;
        let matchType = null;

        // 1. Check trigger phrases (highest priority - exact match)
        const triggered = skill.triggerPhrases?.some(phrase =>
            messageLower.includes(phrase.toLowerCase())
        );
        if (triggered) {
            matchScore = 0.9;
            matchType = 'trigger';
        }

        // 2. Check if message contains any of the skill's tags
        if (!matchType && skill.tags?.length > 0) {
            const tagInMessage = skill.tags.some(tag => {
                const tagLower = tag.toLowerCase();
                return messageLower.includes(tagLower);
            });

            if (tagInMessage) {
                matchScore = 0.8;
                matchType = 'tag-in-message';
            }
        }

        // 3. Check if message contains keywords from skill name (skip common words)
        if (!matchType && skill.name) {
            const skipWords = ['skill', 'helper', 'assistant', 'tool', 'the', 'for', 'and', 'with'];
            const nameWords = skill.name.toLowerCase()
                .split(/[\s\-_]+/)
                .filter(w => w.length > 3 && !skipWords.includes(w));

            const nameMatch = nameWords.some(nameWord =>
                messageWords.some(msgWord =>
                    msgWord.includes(nameWord) || nameWord.includes(msgWord)
                )
            );

            if (nameMatch) {
                matchScore = 0.7;
                matchType = 'name-match';
            }
        }

        // 4. Check tags against conversation/assistant context tags
        if (!matchType && skill.tags?.length > 0) {
            const contextTags = [
                ...(context.tags || []),
                ...(context.assistantTags || []),
                ...(context.conversationTags || [])
            ].map(t => t.toLowerCase());

            const tagMatch = skill.tags.some(tag =>
                contextTags.includes(tag.toLowerCase())
            );

            if (tagMatch) {
                matchScore = 0.6;
                matchType = 'context-tag';
            }
        }

        // 5. Check category match
        if (!matchType && skill.category && context.category) {
            if (skill.category.toLowerCase() === context.category.toLowerCase()) {
                matchScore = 0.5;
                matchType = 'category';
            }
        }

        if (matchType) {
            selectedSkills.push({
                skill,
                matchType,
                confidence: matchScore,
                score: (skill.priority || 5) * matchScore
            });
            logger.debug(`Skill "${skill.name}" matched via ${matchType} with score ${matchScore}`);
        }
    }

    // Sort by combined score (priority * confidence)
    selectedSkills.sort((a, b) => b.score - a.score);

    // Return top 3 skills to avoid context overflow
    const topSkills = selectedSkills.slice(0, 3).map(s => s.skill);

    logger.debug(`Auto-selected ${topSkills.length} skills for user ${user}:`,
        topSkills.map(s => s.name).join(', '));

    return topSkills;
};

/**
 * Share a skill with another user or group
 * @param {string} user - Owner user ID
 * @param {string} skillId - Skill ID
 * @param {Object} shareConfig - Share configuration
 * @returns {Object} Share record
 */
export const shareSkill = async (user, skillId, shareConfig) => {
    const skill = await getSkillById(skillId);
    if (!skill || skill.user !== user) {
        throw new Error("Skill not found or unauthorized");
    }

    const sharesTable = process.env.SKILL_SHARES_DYNAMODB_TABLE;
    if (!sharesTable) {
        throw new Error("SKILL_SHARES_DYNAMODB_TABLE environment variable not set");
    }

    const shareId = `share_${uuidv4()}`;
    const share = {
        shareId,
        skillId,
        sharedBy: user,
        sharedWith: shareConfig.sharedWith,
        shareType: shareConfig.shareType || 'user',
        permissions: shareConfig.permissions || 'read',
        createdAt: new Date().toISOString(),
        expiresAt: shareConfig.expiresAt || null
    };

    await dynamodbClient.send(new PutItemCommand({
        TableName: sharesTable,
        Item: marshall(share, { removeUndefinedValues: true })
    }));

    logger.info(`Shared skill ${skillId} from ${user} to ${shareConfig.sharedWith}`);
    return share;
};

/**
 * Get skill IDs shared with a user
 * @param {string} user - User ID
 * @returns {Array} List of skill IDs
 */
export const getSharedSkillIds = async (user) => {
    const sharesTable = process.env.SKILL_SHARES_DYNAMODB_TABLE;
    if (!sharesTable) {
        return []; // Table not configured, no shares
    }

    try {
        const response = await dynamodbClient.send(new QueryCommand({
            TableName: sharesTable,
            IndexName: "sharedWith-index",
            KeyConditionExpression: "sharedWith = :user",
            ExpressionAttributeValues: marshall({ ":user": user })
        }));

        const shares = response.Items?.map(item => unmarshall(item)) || [];

        // Filter expired shares
        const now = new Date().toISOString();
        const validShares = shares.filter(s => !s.expiresAt || s.expiresAt > now);

        return validShares.map(s => s.skillId);
    } catch (e) {
        logger.error("Error getting shared skill IDs:", e);
        return [];
    }
};

/**
 * Check if a user has access to a skill
 * @param {string} skillId - Skill ID
 * @param {string} user - User ID
 * @returns {boolean} Has access
 */
export const checkSkillAccess = async (skillId, user) => {
    const sharesTable = process.env.SKILL_SHARES_DYNAMODB_TABLE;
    if (!sharesTable) {
        return false;
    }

    try {
        const response = await dynamodbClient.send(new QueryCommand({
            TableName: sharesTable,
            IndexName: "skillId-index",
            KeyConditionExpression: "skillId = :skillId",
            FilterExpression: "sharedWith = :user",
            ExpressionAttributeValues: marshall({
                ":skillId": skillId,
                ":user": user
            })
        }));

        const shares = response.Items?.map(item => unmarshall(item)) || [];

        // Check for valid (non-expired) share
        const now = new Date().toISOString();
        return shares.some(s => !s.expiresAt || s.expiresAt > now);
    } catch (e) {
        logger.error("Error checking skill access:", e);
        return false;
    }
};

/**
 * Delete all shares for a skill
 * @param {string} skillId - Skill ID
 */
const deleteSkillShares = async (skillId) => {
    const sharesTable = process.env.SKILL_SHARES_DYNAMODB_TABLE;
    if (!sharesTable) return;

    try {
        const response = await dynamodbClient.send(new QueryCommand({
            TableName: sharesTable,
            IndexName: "skillId-index",
            KeyConditionExpression: "skillId = :skillId",
            ExpressionAttributeValues: marshall({ ":skillId": skillId })
        }));

        const shares = response.Items?.map(item => unmarshall(item)) || [];

        for (const share of shares) {
            await dynamodbClient.send(new DeleteItemCommand({
                TableName: sharesTable,
                Key: marshall({ shareId: share.shareId })
            }));
        }

        logger.debug(`Deleted ${shares.length} shares for skill ${skillId}`);
    } catch (e) {
        logger.error("Error deleting skill shares:", e);
    }
};

/**
 * Get public skills for discovery
 * @param {number} limit - Maximum number of skills to return
 * @param {Array} tags - Optional tags to filter by
 * @returns {Array} Public skills
 */
export const getPublicSkills = async (limit = 50, tags = null) => {
    const tableName = process.env.SKILLS_DYNAMODB_TABLE;
    if (!tableName) {
        throw new Error("SKILLS_DYNAMODB_TABLE environment variable not set");
    }

    try {
        // Query public skills using GSI
        const response = await dynamodbClient.send(new QueryCommand({
            TableName: tableName,
            IndexName: "public-skills-index",
            KeyConditionExpression: "isPublic = :isPublic",
            ExpressionAttributeValues: marshall({ ":isPublic": "true" }),
            Limit: limit,
            ScanIndexForward: false // Descending by usage count
        }));

        let skills = response.Items?.map(item => normalizeSkill(unmarshall(item))) || [];

        // Filter by tags if specified
        if (tags && tags.length > 0) {
            const tagsLower = tags.map(t => t.toLowerCase());
            skills = skills.filter(skill =>
                skill.tags?.some(t => tagsLower.includes(t.toLowerCase()))
            );
        }

        return skills;
    } catch (e) {
        logger.error("Error getting public skills:", e);
        return [];
    }
};

/**
 * Increment usage count for a skill
 * @param {string} skillId - Skill ID
 */
export const incrementSkillUsage = async (skillId) => {
    const tableName = process.env.SKILLS_DYNAMODB_TABLE;
    if (!tableName) return;

    try {
        await dynamodbClient.send(new UpdateItemCommand({
            TableName: tableName,
            Key: marshall({ id: skillId }),
            UpdateExpression: "SET metadata.usageCount = if_not_exists(metadata.usageCount, :zero) + :one, metadata.lastUsedAt = :now",
            ExpressionAttributeValues: marshall({
                ":one": 1,
                ":zero": 0,
                ":now": new Date().toISOString()
            })
        }));
    } catch (e) {
        logger.debug("Error incrementing skill usage:", e);
    }
};

/**
 * Build skill context message for injection into LLM
 * @param {Array} skills - Skills to inject
 * @returns {string} Formatted skill context
 */
export const buildSkillContextMessage = (skills) => {
    if (!skills || skills.length === 0) return null;

    const skillsContent = skills.map(skill => {
        const parsed = parseSkillContent(skill.content);
        return `### Skill: ${skill.name}\n${parsed.body}`;
    }).join("\n\n---\n\n");

    return `You have the following skills available to help with this conversation. Apply these skills as appropriate to provide the best response:\n\n${skillsContent}`;
};
