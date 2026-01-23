//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

/**
 * Smart Messages Processing
 *
 * This module handles intelligent conversation context optimization by:
 * 1. Analyzing conversation topics and detecting topic changes
 * 2. Determining which historical messages are relevant to the current query
 * 3. Filtering out irrelevant messages to reduce token usage and improve response quality
 * 4. Managing artifact relevance when artifacts are enabled
 *
 * This runs asynchronously on the backend, similar to RAG processing.
 */

import { getLogger } from './logging.js';
import { lzwUncompress } from './lzwCompression.js';
import { promptUnifiedLLMForData, callUnifiedLLM } from '../llm/UnifiedLLMClient.js';
import { ModelTypes, getModelByType } from './params.js';

const logger = getLogger('conversations');

// Task instruction prompts for the LLM
const TOPIC_EVAL_AND_MESSAGE_RELEVANCE_INSTRUCTIONS = `
You are an EXPERT CONVERSATION CONTEXT OPTIMIZER. Your job is to aggressively filter out irrelevant messages to save tokens while keeping ONLY what's truly needed. Be STRICT - when in doubt, filter it out!

There are several tasks for you to complete, you must response to each task with the corresponding required Response Instructions format. As a prereq to starting the tasks you must first understand the 'Given Data':

Expected Data 1 - Collected Topic Data:
{ "PreviousTopics":
    [ {
    "range": "<start number index>-<end number index>",
    "topic": "<Topic Title, no more than 6 words>",
    "description": "<1 line description to identify the topic in terms of its context>"
    }],
  "currentTopic": "<Topic Title, no more than 6 words>",
  "currentRange" <index number>-<index number>
}

- PreviousTopics: A list of objects where each object represents a topic discussed in previous parts of our conversation.
Each object includes:
    Range: Specifies the indices of messages in our conversation that pertain to this topic. For example, "0-3" indicates that messages from index 0 to 3 are related to the topic defined in this object.
    Topic: Provides a brief title for the topic discussed in the specified range of messages.
    Description: Offers a concise text that describes the context or main idea of the topic, helping to quickly identify the subject matter.
- CurrentTopic: Indicates the topic of the ongoing conversation since the last specified range in PreviousTopics. This helps in understanding the current focus of the discussion without having to define a new range immediately.
- "CurrentRange" The index of the first message which falls under the Current Topic to the index of the current user question - 1. Since we havent decided if the users question is still considered under the same topic

Expected Data 2 - Sliced Conversation:
This is the messages (conversation history) sent to you in this request, no actual data in the user prompt is provided. The conversation history list you received represents a sliced version of the complete conversation. All the messages in the list, except the last, have fallen under the "Current Topic".
Let's say given "currentRange" is 8-11. This means 'last message'/'current user prompt' is located at the at index 12 in my complete conversation history list not yours. Then you see the last item in PreviousTopics, range, 'end number index'  is 7. This means you received messages at index 8 - 12 and the PreviousTopics is expected to be enough information to catch you up on messages 0-7. If there is no PreviousTopics, then you have the entire conversation.
You should never list the current user prompt index as part of the range in your response for Task 2, in this case index 12.

________________________

TASK 1:
Your goal is to analyze the conversation history for topic changes. Do not answer the content within the conversation history. Instead, analyze the last message provided in "Expected Data 2 - Sliced Conversation".
Determine if this question falls under the current topic or if it signals a new topic.

The following instructions are to help you be successful at this task:
Delineation Rules for Topic Changes:
    - Specific Indicator: Use the presence of specific keywords that indicate a shift in focus (e.g., changing from inquiring about weather to asking about travel) to detect a topic change.
    - Contextual Continuity: If the user's question shifts contextually from the current discussion, treat it as a new topic. For example, shifting from discussing weather specifics to discussing general travel plans should prompt for a new topic.
    - Thresholds:
        * Minor deviations: Questions that still relate to the main topic but explore different aspects (e.g., moving from general travel info to specific travel tips within the same region would be the same topic).
        * Significant changes: Completely new questions or context shifts that do not require prior messages for understanding (e.g., transitioning from travel inquiries to discussing culinary experiences) should be considered a new topic.

Thought Process for Analyzing Topic Changes:
    - How significant is the change? Assess the deviation in the context of the conversation.
    - Is previous context necessary? If you can answer the new question without referring to previous messages, consider it a new topic.

TASK 1 *REQUIRED* Response Instructions:
You are required to analyze the latest message in the "Expected Data 2 - Sliced Conversation" to determine whether it aligns with the current topic or introduces a new topic. This assessment should be based on the delineation rules and thought process provided previously.

Response Format: Your response must be structured as follows:
/TOPIC_EVAL_START
    isNewTopic={<boolean>}
    newTopic={<Topic Title, no more than 6 words>}     (if applicable)
    currentTopicDescription={<1 line description to identify the topic in terms of its context>}     (if applicable)
/TOPIC_EVAL_END

Where:
 - isNewTopic: A boolean value (true or false). Set to true if the latest message starts a new topic, and false if it continues the current topic.
 - newTopic: Specify the title of the new topic if isNewTopic is true. This should be a concise title of no more than 6 words.
 - currentTopicDescription: Provide a 1-3 line description of the conversation history you received, specifically those messages that fall under "Current Topic", helping to clarify the context or main idea. Applicable only if isNewTopic is true. If current topic is 'NO CURRENT TOPIC' then omit currentTopicDescription

Example Valid Responses:

Example 1 (Introducing a New Topic):
/TOPIC_EVAL_START
    isNewTopic={true}
    newTopic={new topic title}
    currentTopicDescription={this is a description of the messages that were under 'current topic'}
/TOPIC_EVAL_END

Example 2 (Current Topic still applies):
/TOPIC_EVAL_START
    isNewTopic={false}
/TOPIC_EVAL_END
_______________________________

TASK 2: AGGRESSIVE MESSAGE FILTERING
Your job is to AGGRESSIVELY filter out irrelevant conversation history. Your default mindset should be: "Does this DIRECTLY help answer the current question?" If the answer is not a clear YES, then EXCLUDE it.

CRITICAL RULE: Be STRICT about relevance. When in doubt, FILTER IT OUT. Most conversations have topic shifts - don't be afraid to return empty ranges or very selective ranges.

MANDATORY STEP-BY-STEP REASONING PROCESS:
You MUST follow these steps in order:

STEP 1: Identify the current topic
- If Task 1 shows isNewTopic=true, use the NEW topic
- If Task 1 shows isNewTopic=false, use the CURRENT topic from the data

STEP 2: Evaluate EACH previous topic range individually
For EACH item in PreviousTopics array, ask yourself:
- "Is this topic DIRECTLY related to the current topic?" (e.g., both about Santa, both about weather, both about same subject)
- "Would someone need information from this topic to understand or answer the current question?"
- "Are these the SAME topic or CLOSELY RELATED topics?"

If the answer to ANY of these questions is YES, keep the range. If ALL are NO, then EXCLUDE it.

IMPORTANT: "Santa Claus Overview" IS related to "Santa's outfit colors" - these are the same general topic (Santa)! Similarly, "SoCal Weather" IS related to "SoCal Winter Attire" - these are closely related topics.

STEP 3: Evaluate the currentRange
- If Task 1 shows the topic did NOT change (isNewTopic=false), then currentRange is obviously relevant
- If Task 1 shows a NEW topic (isNewTopic=true), then currentRange is the OLD topic and is probably NOT relevant

STEP 4: Make your final decision
- List ONLY the ranges that passed your evaluation
- If NO ranges are relevant, return ranges={}
- Only use ranges={ALL} if the topic has NOT changed at all

CONCRETE EXAMPLES OF CORRECT FILTERING:

Example A - COMPLETELY UNRELATED TOPICS (Correct: Filter aggressively):
PreviousTopics: [{range: "0-4", topic: "Weather in California", description: "Discussion about Southern California weather"}]
currentRange: "5-8"
Current user question: "Tell me about cats and why Santa Claus eats cookies"
Task 1 result: isNewTopic=true, newTopic="Cats and Santa Claus"

YOUR THOUGHT PROCESS:
- Current topic is "Cats and Santa Claus"
- Previous topic was "Weather in California"
- These are COMPLETELY UNRELATED
- Weather information does NOT help answer questions about cats or Santa
- The currentRange (5-8) contains the OLD weather topic discussion, which is NOT relevant
CORRECT RESPONSE: ranges={}

Example B - RETURNING TO OLD TOPIC (Correct: Include only relevant old topic):
PreviousTopics: [
  {range: "0-3", topic: "Weather in California", description: "Discussion about weather and climate"},
  {range: "4-7", topic: "Cats and Santa", description: "Random discussion about cats and Santa Claus"}
]
currentRange: "8-10"
Current user question: "So back to the weather, what about San Diego temperatures?"
Task 1 result: isNewTopic=true, newTopic="San Diego Weather"

YOUR THOUGHT PROCESS:
- Current topic is "San Diego Weather"
- Range 0-3 is "Weather in California" - DIRECTLY RELATED, user is returning to this topic
- Range 4-7 is "Cats and Santa" - COMPLETELY UNRELATED to weather
- The currentRange (8-10) discussed cats/Santa, which is NOT relevant to weather questions
CORRECT RESPONSE: ranges={0-3}

Example C - BLENDING RELATED TOPICS (Keep relevant ranges):
PreviousTopics: [
  {range: "0-2", topic: "Machine Learning Basics", description: "Introduction to ML concepts"},
  {range: "3-5", topic: "Neural Networks", description: "Deep dive into neural network architecture"}
]
currentRange: "6-8"
Current user question: "How do neural networks apply the ML concepts we discussed?"
Task 1 result: isNewTopic=false

YOUR THOUGHT PROCESS:
- Topic has NOT changed (isNewTopic=false)
- User is explicitly referencing BOTH previous topics ("neural networks" and "ML concepts we discussed")
- Both ranges are relevant to answering this question
- currentRange is still on the same topic
CORRECT RESPONSE: ranges={ALL}

Example D - RETURNING TO TOPIC (isNewTopic=false but FILTER unrelated tangent):
PreviousTopics: [
  {range: "0-4", topic: "SoCal Weather", description: "Discussion about Southern California weather"},
  {range: "5-15", topic: "Santa Claus", description: "Random tangent about Santa, cookies, and Christmas"},
  {range: "16-18", topic: "SoCal Winter Attire", description: "Discussion about what to wear in SoCal winter"}
]
currentRange: "20-20"
Current user question: "I think I should bring a jacket to SoCal"
Task 1 result: isNewTopic=false (continuing SoCal weather/attire topic)

YOUR THOUGHT PROCESS:
- Topic has NOT changed (isNewTopic=false) - still talking about SoCal weather/clothing
- Range 0-4 "SoCal Weather" - DIRECTLY RELATED to current question about SoCal jacket
- Range 5-15 "Santa Claus" - COMPLETELY UNRELATED tangent, doesn't help answer about SoCal clothing
- Range 16-18 "SoCal Winter Attire" - DIRECTLY RELATED, just discussed SoCal winter clothing
- currentRange (20-20) is continuing the same topic
- Even though isNewTopic=false, I must EXCLUDE the Santa tangent!
CORRECT RESPONSE: ranges={0-4, 16-18, 20-20}

Example E - RELATED TOPIC QUESTION (Keep relevant topic ranges):
PreviousTopics: [
  {range: "0-4", topic: "SoCal Weather", description: "Discussion about Southern California weather"},
  {range: "5-15", topic: "Santa Claus Overview", description: "Discussion about Santa Claus, his lifestyle, and food preferences"},
  {range: "16-18", topic: "SoCal Winter Attire", description: "Discussion about what to wear in SoCal winter"}
]
currentRange: "20-22"
Current user question: "Would Santa look good in any other color besides red?"
Task 1 result: isNewTopic=true, newTopic="Santa's Outfit Colors"

YOUR THOUGHT PROCESS:
- Current topic is "Santa's Outfit Colors"
- Range 0-4 "SoCal Weather" - UNRELATED to Santa
- Range 5-15 "Santa Claus Overview" - DIRECTLY RELATED! This discussed Santa, and the current question is about Santa's appearance
- Range 16-18 "SoCal Winter Attire" - UNRELATED to Santa
- currentRange (20-22) discussed SoCal attire, NOT related to Santa question
- Even though it's a "new topic", range 5-15 is clearly about the SAME subject (Santa)
CORRECT RESPONSE: ranges={5-15}

ANTI-PATTERNS TO AVOID (What NOT to do):
❌ DON'T return ranges={ALL} just because you're unsure
❌ DON'T include ranges "just in case they might be useful"
❌ DON'T assume topics are related just because they were discussed in the same conversation
❌ DON'T include currentRange when Task 1 shows isNewTopic=true AND the old topic is unrelated
❌ DON'T return ranges={} when there ARE related previous topics - if the question is about Santa and there's a "Santa Overview" range, include it!

DECISION RULES:
1. If currentTopic is "NO CURRENT TOPIC" → Return ranges={ALL}
2. If isNewTopic=false → You MUST still evaluate previousTopics! Just because the topic continues doesn't mean ALL previous topics are relevant. Apply STEP 2 evaluation to each previousTopic range. If the conversation went Topic A → Topic B → back to Topic A, you should EXCLUDE Topic B ranges even though isNewTopic=false!
3. If isNewTopic=true AND no previous topics relate to new topic AND currentRange is old topic → Return ranges={}
4. If isNewTopic=true AND some previous topics relate to new topic → Return only those relevant ranges
5. Default mindset: EXCLUDE unless clearly relevant

CRITICAL: Rule #2 does NOT mean "return {ALL}"! It means "topic is continuing, so keep currentRange, but still filter previousTopics aggressively!"

Relevance Criteria: A past message is considered relevant if:
    - It contains foundational information that contributes to a fuller understanding of the current topic.
    - It provides context or background that enhances the current discussion.
    - It directly relates to the current user query or topic at hand.
TASK 2 *REQUIRED* Response Instructions:

Response Format: Once relevance is determined, format your response as follows:
/INCLUDE_MESSAGES_START
    ranges={specified ranges of relevant messages separated by commas}
/INCLUDE_MESSAGES_END

Example Valid Responses:

Example 1 (Blending Topics):
Context: Discussion transitions from general university information to study techniques and then to how specific majors might affect study habits.
Relevance Determination: Messages discussing university details and study techniques are both relevant to the new question about majors and study habits.

Your Response to Example 1:
/INCLUDE_MESSAGES_START
    ranges={ALL}
/INCLUDE_MESSAGES_END

IMPORTANT: if the currentTopic in the Collected Topic Data is "NO CURRENT TOPIC", then you MUST respond with ranges={ALL}. This means it's the first message or start of conversation.

Example 2 (Selection of ranges):
Following the Think step by step:
    1. Looking at the ranges in each item of the PreviousTopics lists and other data. Lets say the ranges are 0-3, 4-7, 8-11, 12-18   and  currentRange=19-21
    2. Determining which of those ranges are relevant. Lets say the relevant ranges are 0-3, 12-18, and the Current Topic is relevant as well.
    3. Gather the ranges straight from the Given Data and the currentRange since, we deemed the Current Topic messages are important.

Your Response to Example 2:
/INCLUDE_MESSAGES_START
    ranges={0-3, 12-18, 19-21}
/INCLUDE_MESSAGES_END

Example 3 (Topic evaluation with currentRange):
Based on the decision of Task 1, you will decide to include or omit the currentRange.
    - The topic is new and the Current Topic is NOT relevant. Your Response:
        /INCLUDE_MESSAGES_START
            ranges={}
        /INCLUDE_MESSAGES_END
    - The topic is new and the Current Topic is relevant. The currentRange is 3-11. Your Response:
        /INCLUDE_MESSAGES_START
                ranges={3-11}
        /INCLUDE_MESSAGES_END
    - The topic is NOT new AND there are NO previousTopics (first few messages). Your Response:
        /INCLUDE_MESSAGES_START
                ranges={ALL}
        /INCLUDE_MESSAGES_END
    - The topic is NOT new BUT there ARE previousTopics: You MUST evaluate each previousTopic range for relevance! Do NOT automatically return {ALL}. See Example D above.

Guidelines for Evaluation:
- Consider how the information in previous messages can enhance or clarify the current topic.
- Assess the continuity and flow of the discussion to ensure that the selected messages provide a cohesive and comprehensive understanding of the topic.
- Use the context provided by both the outcome of Task 1 and the "Collected Topic Data" to make informed decisions about relevance.

For Task 1 and 2 respond with ONLY the /TOPIC_EVAL and /INCLUDE_MESSAGES data. You MUST include both responses.
`;

const ARTIFACT_RELEVANCE_INSTRUCTIONS = `
Expected Data 3 - List of Artifact Definitions:
[ <Artifact Definitions - objects separated by commas> ]

Artifact Definition: Each artifact is defined with the following attributes:
{
"id": "<unique identifier>",
"name": "<artifact name>",
"description": "<brief description of the artifact>",
"type": "<type of the artifact>",
"version": <version number of this artifact>,
"totalVersions": <total number of versions that exist for this artifact>,
"messageIndex": <index indicating when the artifact was introduced in the conversation>
}

IMPORTANT - Understanding Artifact Versions:
- Multiple artifacts may share the same "id" but have different "version" numbers (e.g., id="MyApp", version=1 vs version=3)
- These represent iterations/updates of the same artifact over time
- The list shows ONLY versions that were explicitly referenced in messages (you won't see all versions)
- When deciding relevance, consider:
  * If user asks about the "latest" or "current" version → choose the highest version number for that id
  * If user refers to an older version explicitly (e.g., "the first version", "original", "before we changed X") → choose the lower version
  * If user asks to compare versions → include multiple versions of the same id
  * Default: Include only the LATEST version of each artifact id unless there's a specific reason to include older versions

Task 3 Instructions:
This task involves assessing the relevance of predefined artifacts within the context of the ongoing conversation. These artifacts, which vary in type and content, are linked to specific messages within the conversation timeline, allowing for a contextual understanding of their significance based on the current topic and relevant message ranges identified in earlier tasks.

Objective: Determine which artifact versions should be included in the conversation. Relevance will be assessed based on the artifact's content, type, version history, and its introduction in relation to the current focus of the conversation.

Steps to Follow:
    - Review Artifacts: Examine each artifact's description, type, version, and the message index to understand its context.
    - Identify Version Relationships: Group artifacts by their "id" to see which ones are different versions of the same artifact.
    - Refer to Task Outcomes: Use the results from Tasks 1 and 2 to ascertain which parts of the conversation are still relevant.
    - Evaluate Version Relevance: For artifacts with multiple versions, determine if the user needs the latest version, an older version, or multiple versions for comparison.
    - Evaluate Artifact Relevance: Decide if the artifact's content supports, enhances, or is essential to the current discussion.

Relevance Criteria: An artifact version is deemed relevant if:
    - It supports or adds significant context or background information to the current topic and final user message.
    - It is closely related to the message ranges identified as relevant in Task 2.
    - The type of artifact directly contributes to a better understanding or resolution of the current user query (final user message).
    - It is the latest version of an artifact (unless the user specifically references an older version).
    - If the user prompt is referring to any past artifact in any form then assume it is relevant and should be included.

TASK 3 *REQUIRED* Response Format: After evaluating the artifacts, format your response as follows:
/ARTIFACT_RELEVANCE_START
    artifactIds={<list of relevant artifact identifiers with versions>}
/ARTIFACT_RELEVANCE_END

IMPORTANT - Artifact ID Format in Response:
- For version 1: use just the id (e.g., "MyApp")
- For versions > 1: use "id:vN" format (e.g., "MyApp:v3")
- Example response: artifactIds={MyApp, OtherArtifact:v2}

Guidelines for Evaluation:
- Analyze the impact of each artifact on the conversation's current trajectory.
- Consider both the artifact's content and the timing of its introduction relative to the current focus areas identified in Task 2.
- When multiple versions exist, prefer the latest unless the user explicitly references older versions.
- Ensure that the relevance assessment is aligned with the overarching goals and flow of the conversation.
- Be selective - only include artifacts that are truly needed for the current query to save tokens.
`;

const SMART_INCLUDE_ARTIFACT_INSTRUCTIONS = (prompt, triggerConditions) => `
Task 4 Instructions:
Determine if we should include the artifact creation instructions based on the current user prompt.

CRITICAL: Only set includeInstructions=true if the user is requesting to CREATE, GENERATE, or BUILD something new.
Do NOT include instructions if the user is:
- Asking questions ABOUT existing artifacts (e.g., "what do these do?", "do these have X in common?")
- Requesting analysis or comparison of existing artifacts
- Simply referencing or discussing artifacts

Artifact Trigger Conditions:
${triggerConditions}

Guidelines:
- Evaluate User Input to determine if it matches the artifact trigger conditions based on keywords such as "create," "build," "generate," "make," "write," "develop," "outline," "full project," "detailed analysis," or "extensive documentation."
- Consider the likelihood of the user wanting to CREATE A NEW artifact based on the request. Only likelihood above 60% should be included.
- If the user is asking about or analyzing EXISTING artifacts, respond with false.
- If you are unsure, err on the side of false to save tokens.

User Prompt:
${prompt}

Response Format:
/INCLUDE_ARTIFACT_INSTRUCTIONS_START
    includeInstructions={<boolean(true or false)>}
/INCLUDE_ARTIFACT_INSTRUCTIONS_END

IMPORTANT: You must respond to BOTH Task 3 (if artifacts exist) AND Task 4. Provide both response blocks.
`;

/**
 * Gather topic data from conversation history
 */
const gatherTopicData = (messages) => {
    let currentTopic = '';
    let currentTopicStart = 0;
    const previousTopics = [];

    logger.debug("🔍 [gatherTopicData] Starting to gather topic data:", {
        totalMessages: messages.length
    });

    // Loop backwards through messages (skip last one - that's current user question)
    for (let i = messages.length - 2; i >= 0; i--) {
        const msg = messages[i];
        // Topic data is stored in message.data.state.smartMessages.topicChange
        const msgTopicData = msg.data?.state?.smartMessages?.topicChange || msg.topicData;

        if (msgTopicData) {
            logger.debug(`🔍 [gatherTopicData] Found topicData at index ${i}:`, {
                currentTopic: msgTopicData.currentTopic,
                hasPastTopic: !!msgTopicData.pastTopic
            });

            if (msgTopicData.pastTopic) {
                previousTopics.unshift(msgTopicData.pastTopic);
                logger.debug(`  ✅ Added to previousTopics:`, msgTopicData.pastTopic);
            }
            if (!currentTopic && msgTopicData.currentTopic) {
                currentTopic = msgTopicData.currentTopic;
                currentTopicStart = i;
                logger.debug(`  ✅ Set currentTopic: "${currentTopic}" (start: ${i})`);
            }
        }
    }

    logger.debug("🔍 [gatherTopicData] Finished gathering:", {
        currentTopic,
        previousTopicsCount: previousTopics.length
    });

    const slicedMessages = messages.slice(currentTopicStart);
    const curIdx = messages.length - 1;
    const expectedData = {
        previousTopics: previousTopics,
        currentTopic: currentTopic ? currentTopic : "NO CURRENT TOPIC",
        currentRange: `${currentTopicStart}-${curIdx === 0 ? 0 : curIdx - 1}`
    };

    const messageData = `
Collected Topic Data:
${JSON.stringify(expectedData)}
    `;

    return {
        slicedMessages,
        topicMessageData: messageData,
        currentTopic,
        currentTopicStart
    };
};

/**
 * Gather artifact data from conversation
 */
const gatherArtifactData = (messages, artifacts) => {
    const artifactList = [];
    const artifactMap = {}; // Maps artifact ID -> compressed contents array
    const referencedVersions = {}; // Track which versions appear in messages: { artifactId: Set<version> }

    logger.debug(`🔍 [gatherArtifactData] Scanning ${Object.keys(artifacts || {}).length} artifact(s)`);

    // Scan messages to find which versions were explicitly attached
    for (let i = messages.length - 1; i >= 0; i--) {
        const msg = messages[i];
        if (msg.data && msg.data.artifacts) {
            msg.data.artifacts.forEach((a) => {
                if (!referencedVersions[a.artifactId]) {
                    referencedVersions[a.artifactId] = new Set();
                }
                referencedVersions[a.artifactId].add(a.version || 1);
            });
        }
    }

    // Build artifact list from conversation.artifacts
    // Only include versions that were referenced in messages, OR latest if none referenced
    Object.keys(artifacts || {}).forEach((id) => {
        const allVersions = artifacts[id] || [];
        const referenced = referencedVersions[id];

        // Which versions to include?
        let versionsToInclude = [];
        if (referenced && referenced.size > 0) {
            // Try to get the referenced versions
            versionsToInclude = allVersions.filter(a => referenced.has(a.version));
            // If no matching versions found (e.g., message wants v2 but only v1 exists), use latest
            if (versionsToInclude.length === 0) {
                versionsToInclude = [allVersions[allVersions.length - 1]];
            }
        } else {
            // No versions referenced in messages, use latest
            versionsToInclude = [allVersions[allVersions.length - 1]];
        }

        versionsToInclude.forEach((artifact) => {
            if (!artifact) return;

            const mapKey = artifact.version === 1 ? id : `${id}:v${artifact.version}`;

            // Add metadata to artifact list (for LLM)
            artifactList.push({
                id: id,
                name: artifact.name,
                description: artifact.description,
                type: artifact.type,
                version: artifact.version,
                totalVersions: allVersions.length
            });

            // Add contents to artifact map (for decompression)
            artifactMap[mapKey] = artifact.contents || [];

            // Log for debugging
            if (artifact.contents && artifact.contents.length > 0) {
                logger.debug(`  ✅ ${mapKey}: "${artifact.name}"`);
            } else {
                logger.warn(`  ⚠️  ${mapKey}: "${artifact.name}" - NO CONTENTS`);
            }
        });
    });

    logger.debug(`📊 [gatherArtifactData] Final: ${artifactList.length} artifact version(s)`);

    const messageData = artifactList.length > 0
        ? `\nList of Artifact Definitions:\n${JSON.stringify(artifactList)}`
        : "";

    return {
        artifactMessageData: messageData,
        artifactLen: artifactList.length,
        artifactList,
        artifactMap  // CRITICAL: Return the map of compressed contents with version-aware keys!
    };
};

/**
 * Extract content between markers
 */
const extractResponseContent = (response, startMarker, endMarker) => {
    let startIndex = response.indexOf(startMarker);
    if (startIndex === -1) return '';
    startIndex += startMarker.length;
    const endIndex = response.indexOf(endMarker, startIndex);
    return response.slice(startIndex, endIndex).trim();
};

/**
 * Parse topic evaluation response
 */
const parseTopicEvaluation = (response, currentTopic, currentTopicStart, currentUserQIndex) => {
    if (!response) {
        logger.debug("🔍 [parseTopicEvaluation] No response provided");
        return undefined;
    }

    const isNewTopicMatch = response.match(/isNewTopic=\{(true|false)\}/);
    const isNewTopic = isNewTopicMatch ? isNewTopicMatch[1] === 'true' : false;

    logger.debug("🔍 [parseTopicEvaluation] Parsing:", {
        isNewTopic,
        currentTopic
    });

    if (isNewTopic) {
        const topicData = { currentTopic: '' };
        const newTopicMatch = response.match(/newTopic=\{([^}]+)\}/);
        const topicDescriptionMatch = response.match(/currentTopicDescription=\{([^}]+)\}/);

        const newTopic = newTopicMatch ? newTopicMatch[1] : null;
        const topicDescription = topicDescriptionMatch ? topicDescriptionMatch[1] : null;

        logger.debug("🔍 [parseTopicEvaluation] Extracted:", {
            newTopic,
            hasDescription: !!topicDescription
        });

        if (topicDescription) {
            topicData.pastTopic = {
                range: `${currentTopicStart}-${currentUserQIndex - 1}`,
                topic: currentTopic,
                description: topicDescription
            };
        }

        if (newTopic) {
            topicData.currentTopic = newTopic;
            topicData.topicChanged = true;
            logger.debug("🔍 [parseTopicEvaluation] Returning topicData:", topicData);
            return topicData;
        }
    }

    logger.debug("🔍 [parseTopicEvaluation] Returning undefined (no topic change or missing newTopic)");
    return undefined;
};

/**
 * Parse message relevance ranges
 */
const parseMessageRanges = (response) => {
    if (!response) return null;

    // Match ranges={...} - using .* instead of .+ to allow empty braces
    const rangeMatch = response.match(/ranges=\{(.*)\}/);
    if (!rangeMatch) return null;

    const rangesStr = rangeMatch[1].trim();

    // Handle special cases
    if (rangesStr === 'ALL') return 'ALL';
    if (rangesStr === '' || rangesStr.length === 0) {
        logger.warn("⚠️ [parseMessageRanges] Empty ranges detected - falling back to ALL for safety");
        return 'ALL'; // Safety: if LLM returns empty, better to keep all context
    }

    return rangesStr.split(',').map(r => r.trim()).filter(r => r.length > 0);
};

/**
 * Filter messages based on relevance ranges
 */
const filterMessagesByRanges = (messages, ranges) => {
    if (ranges === 'ALL' || ranges === null) {
        logger.debug(" [Smart Messages] Keeping ALL messages");
        return { filteredMessages: messages, keptIndexes: messages.map((_, i) => i) };
    }

    if (!Array.isArray(ranges) || ranges.length === 0) {
        logger.debug("� [Smart Messages] Empty ranges, keeping only last message");
        return {
            filteredMessages: [messages[messages.length - 1]],
            keptIndexes: [messages.length - 1]
        };
    }

    const result = [];
    const keptIndexes = [];
    const lastIdx = messages.length - 1;

    ranges.forEach(range => {
        const [start, end] = range.split('-').map(Number);

        // Validate
        if (isNaN(start) || isNaN(end)) {
            logger.warn(`� [Smart Messages] Invalid range format: "${range}"`);
            return;
        }

        if (start < 0 || end >= messages.length || start > end) {
            logger.warn(`� [Smart Messages] Range out of bounds: ${start}-${end}`);
            return;
        }

        let adjustedEnd = end;
        if (end === lastIdx) adjustedEnd = end - 1;

        // Track indexes and add messages
        for (let i = start; i <= adjustedEnd; i++) {
            if (!keptIndexes.includes(i)) {
                keptIndexes.push(i);
                result.push({ ...messages[i] }); // Copy message
            }
        }
    });

    // Always include last message if not already included
    if (!keptIndexes.includes(lastIdx)) {
        keptIndexes.push(lastIdx);
        result.push({ ...messages[lastIdx] });
    }

    logger.debug("=� [Smart Messages] Filtered messages:", {
        originalCount: messages.length,
        keptCount: result.length,
        removedCount: messages.length - result.length,
        keptIndexes: keptIndexes.sort((a, b) => a - b)
    });

    return { filteredMessages: result, keptIndexes };
};

/**
 * Parse artifact IDs from response
 */
const parseArtifactIds = (response) => {
    if (!response) return [];

    const idsMatch = response.match(/artifactIds=\{([^}]+)\}/);
    const artifactIds = idsMatch ? idsMatch[1].split(', ').map(id => id.trim()) : [];
    return artifactIds;
};

/**
 * Inject relevant artifact content into messages
 * @param {Array} messages - Filtered messages
 * @param {Array} relevantArtifactIds - IDs of relevant artifacts
 * @param {Object} artifacts - All artifacts with compressed content
 * @returns {Array} Messages with artifact content injected into last message
 */
const injectArtifactContent = (messages, relevantArtifactIds, artifacts) => {
    if (!relevantArtifactIds || relevantArtifactIds.length === 0) {
        return messages;
    }

    let artifactContent = "\n\nYou may or may not find the following artifacts useful to answer the users prompt:\n";

    relevantArtifactIds.forEach((id) => {
        const artifactVersions = artifacts[id];
        if (artifactVersions && artifactVersions.length > 0) {
            // Get latest version
            const latestArtifact = artifactVersions[artifactVersions.length - 1];
            if (latestArtifact && latestArtifact.contents) {
                try {
                    // Decompress artifact content
                    const decompressedContent = lzwUncompress(latestArtifact.contents);
                    artifactContent += `\n\nArtifactId: ${id}\n${decompressedContent}`;
                } catch (error) {
                    logger.error(`Failed to decompress artifact ${id}:`, error);
                }
            }
        }
    });

    // Create a copy of messages and inject artifact content into last message
    const updatedMessages = [...messages];
    const lastIdx = updatedMessages.length - 1;
    updatedMessages[lastIdx] = {
        ...updatedMessages[lastIdx],
        content: updatedMessages[lastIdx].content + artifactContent
    };

    return updatedMessages;
};

/**
 * Parse include artifact instructions
 */
const parseIncludeArtifactInstructions = (response) => {
    if (!response) return true;

    const includeInstr = response.match(/includeInstructions=.*?(true|false)/i);
    return includeInstr ? includeInstr[1].toLowerCase() === 'true' : true;
};

/**
 * Main function: Process conversation with smart messages
 *
 * @param {Object} params - Processing parameters
 * @param {Array} params.messages - Conversation messages
 * @param {Object} params.artifacts - Conversation artifacts (optional)
 * @param {Object} params.options - Processing options
 * @param {boolean} params.options.smartMessages - Enable smart message filtering
 * @param {boolean} params.options.artifacts - Enable artifact relevance
 * @param {string} params.options.artifactTriggerConditions - Conditions for artifact creation
 * @param {Object} params.account - Account info for LLM calls
 * @param {string} params.requestId - Request ID for tracking
 * @param {Object} params.params - Request params (for getModelByType)
 *
 * @returns {Object} Processed conversation data
 */
export const processSmartMessages = async ({
    messages,
    options = {},
    account,
    requestId,
    params
}) => {
    const startTime = Date.now();
    const artifacts = options.artifacts;
    options = options.options;

    logger.info("▶️ [Processing]:", {
        messages: messages.length,
        smartMsg: options.smartMessages,
        artifacts: options.artifacts
    });

    // Early exit if both features are off
    if (!options.smartMessages && !options.artifacts) {
        logger.info("SKIP [Smart Messages] Both features disabled - returning original messages");
        return {
            filteredMessages: messages,
            metadata: {
                processed: false,
                reason: "features_disabled"
            }
        };
    }

    // Safety check
    if (messages.length === 0) {
        logger.error("[Smart Messages] Empty messages array");
        return {
            filteredMessages: [],
            metadata: { processed: false, reason: "empty_messages" }
        };
    }

    try {
        // PHASE 1: Gather data
        const gatherStart = Date.now();
        const topicData = options.smartMessages ? gatherTopicData(messages) : null;
        const artifactData = options.artifacts ? gatherArtifactData(messages, artifacts) : null;
        const gatherDuration = Date.now() - gatherStart;

        // LOG ARTIFACT DETECTION
        if (options.artifacts && artifactData?.artifactLen > 0) {
            logger.info(`🔍 [Artifacts] Detected ${artifactData.artifactLen} artifact(s)`);
        }

        logger.debug("� [Smart Messages] Phase 1 - Data Gathering:", {
            duration: `${gatherDuration}ms`
        });

        // OPTIMIZATION: Artifacts-only mode - skip topic analysis but still run TASK 4 (artifact creation detection)
        if (!options.smartMessages && options.artifacts) {
            logger.info("⚡ [Smart Messages] Artifacts-only mode - running artifact creation detection only");

            // Build TASK 4 instructions to determine if artifact instructions should be included
            let instructions = SMART_INCLUDE_ARTIFACT_INSTRUCTIONS(
                messages[messages.length - 1].content,
                ARTIFACT_TRIGGER_CONDITIONS
            );

            // If artifacts exist, add TASK 3 instructions for artifact relevance detection
            if (artifactData && artifactData.artifactLen > 0) {
                instructions = ARTIFACT_RELEVANCE_INSTRUCTIONS + "\n\n" + instructions;
                instructions = `Expected Data 3 - List of Artifact Definitions:\n${artifactData.artifactMessageData}\n\n` + instructions;
            }

            const llmMessages = [
                {
                    role: 'system',
                    content: instructions
                },
                {
                    role: 'user',
                    content: messages[messages.length - 1].content
                }
            ];

            logger.debug("🤖 [Artifacts-Only] Calling LLM for detection...");
            const llmStart = Date.now();

            // Call LLM for TASK 4 (and TASK 3 if artifacts exist)
            const model = getModelByType(params, ModelTypes.CHEAPEST);
            const llmResponse = await callUnifiedLLM(
                {
                    account,
                    requestId,
                    model,
                    options: {
                        temperature: 0.8,
                        max_tokens: 500,
                        disableReasoning: true
                    }
                },
                llmMessages,
                null
            );

            const llmDuration = Date.now() - llmStart;
            const llmResponseText = llmResponse.content || '';

            logger.debug(`✅ [Artifacts-Only] LLM response received (${llmDuration}ms)`);

            // Parse TASK 4 response
            const includeArtifactInstr = parseIncludeArtifactInstructions(llmResponseText);

            // Parse TASK 3 response (if artifacts exist)
            let relevantArtifactIds = [];
            if (artifactData && artifactData.artifactLen > 0) {
                relevantArtifactIds = parseArtifactIds(llmResponseText);
                logger.info(`📦 [Artifacts-Only] Relevant artifacts: ${relevantArtifactIds.join(', ')}`);
            }

            logger.info(`📋 [Artifacts-Only] Artifact instructions: ${includeArtifactInstr ? 'INCLUDED' : 'EXCLUDED'}`);

            // Inject relevant artifact content into messages
            let updatedMessages = messages;
            if (relevantArtifactIds.length > 0) {
                logger.info(`💉 [Artifacts-Only] Injecting: ${relevantArtifactIds.join(', ')}`);
                updatedMessages = injectArtifactContent(messages, relevantArtifactIds, artifacts);
            }

            // Return artifact instructions for router to append (don't modify options here)
            return {
                filteredMessages: updatedMessages,
                metadata: { processed: true },
                artifactInstructions: includeArtifactInstr ? ARTIFACTS_PROMPT : null,
                _internal: {
                    removedCount: 0,
                    relevantArtifactIds,
                    includeArtifactInstructions: includeArtifactInstr,
                    artifactsInjected: relevantArtifactIds.length > 0,
                    performance: {
                        totalMs: Date.now() - startTime,
                        llmMs: llmDuration
                    }
                }
            };
        }

        // Build LLM prompt
        let promptContent = `USERS CURRENT PROMPT:\n${messages[messages.length - 1].content}\n\n___________________________\n\n`;

        if (topicData) promptContent += topicData.topicMessageData;
        if (artifactData) promptContent += artifactData.artifactMessageData;

        // Build instructions FIRST
        let instructions = TOPIC_EVAL_AND_MESSAGE_RELEVANCE_INSTRUCTIONS;
        if (artifactData && artifactData.artifactLen > 0) {
            instructions += "\n\n" + ARTIFACT_RELEVANCE_INSTRUCTIONS;
        }
        // TASK 4: Always include artifact creation detection when artifacts feature is ON
        if (options.artifacts) {
            instructions += "\n\n" + SMART_INCLUDE_ARTIFACT_INSTRUCTIONS(
                messages[messages.length - 1].content,
                ARTIFACT_TRIGGER_CONDITIONS
            );
        }

        // Build messages array with system message containing instructions
        const messagesToAnalyze = topicData ? topicData.slicedMessages : messages;
        const llmMessages = [
            {
                role: 'system',
                content: instructions
            },
            ...messagesToAnalyze.map(msg => ({
                role: msg.role,
                content: msg.content
            }))
        ];
        llmMessages[llmMessages.length - 1].content = promptContent;

        // PHASE 2: LLM Analysis
        const llmStart = Date.now();

        // Use cheapest model for smart messages analysis
        const model = getModelByType(params, ModelTypes.CHEAPEST);

        logger.debug("🤖 [Smart Messages] Calling LLM:", {
            model: model.id,
            messageCount: llmMessages.length
        });

        // Call LLM with structured output
        const llmResponse = await promptUnifiedLLMForData(
            {
                account,
                options: {
                    model,
                    requestId,
                    disableReasoning: true,
                }
            },
            llmMessages,
            {
                type: "object",
                properties: {
                    response: {
                        type: "string",
                        description: "Full text response with task markers"
                    }
                },
                required: ["response"]
            },
            null // No streaming
        );

        const llmDuration = Date.now() - llmStart;
        const responseText = llmResponse.response || llmResponse.toString();

        logger.debug(`📥 [Smart Messages] LLM Response received (${llmDuration}ms)`);

        // PHASE 3: Parse response
        const processStart = Date.now();

        const topicEvalText = extractResponseContent(responseText, '/TOPIC_EVAL_START', '/TOPIC_EVAL_END');
        const messageRangesText = extractResponseContent(responseText, '/INCLUDE_MESSAGES_START', '/INCLUDE_MESSAGES_END');
        const artifactRelevanceText = extractResponseContent(responseText, '/ARTIFACT_RELEVANCE_START', '/ARTIFACT_RELEVANCE_END');
        const includeArtifactText = extractResponseContent(responseText, '/INCLUDE_ARTIFACT_INSTRUCTIONS_START', '/INCLUDE_ARTIFACT_INSTRUCTIONS_END');

        logger.debug("🔍 [Smart Messages] Extracted sections:", {
            hasTopicEval: !!topicEvalText,
            hasMessageRanges: !!messageRangesText
        });

        // Parse topic evaluation
        const topicEval = topicData ? parseTopicEvaluation(
            topicEvalText,
            topicData.currentTopic,
            topicData.currentTopicStart,
            messages.length - 1
        ) : null;

        // Log topic evaluation verdict
        if (topicEval && topicEval.topicChanged) {
            logger.info(`🔍 [Topic] Changed to: ${topicEval.currentTopic}`);
        }

        // Parse message ranges
        const ranges = parseMessageRanges(messageRangesText);
        logger.debug("📊 [Smart Messages] Filtering verdict:", {
            ranges: ranges === 'ALL' ? 'ALL' : ranges
        });

        let { filteredMessages, keptIndexes } = filterMessagesByRanges(messages, ranges);

        // Log which indexes were kept vs removed
        const removedIndexes = [];
        for (let i = 0; i < messages.length; i++) {
            if (!keptIndexes.includes(i)) {
                removedIndexes.push(i);
            }
        }

        logger.info(`✂️ [Messages] Kept ${keptIndexes.length}/${messages.length} (removed ${removedIndexes.length})`);

        // Parse artifact relevance
        const relevantArtifactIds = artifactData ? parseArtifactIds(artifactRelevanceText) : [];

        // Log artifact verdict
        if (artifactData && relevantArtifactIds.length > 0) {
            logger.info(`📦 [Artifacts] Relevant: ${relevantArtifactIds.join(', ')}`);
        }

        // Inject relevant artifact content into messages
        if (relevantArtifactIds.length > 0) {
            const before = filteredMessages[filteredMessages.length - 1]?.content?.length || 0;
            filteredMessages = injectArtifactContent(filteredMessages, relevantArtifactIds, artifacts);
            const after = filteredMessages[filteredMessages.length - 1]?.content?.length || 0;

            logger.info(`💉 [Artifacts] Injected ${relevantArtifactIds.length} artifact(s) (+${after - before} chars)`);
        }

        // Parse include artifact instructions
        const includeArtifactInstr = parseIncludeArtifactInstructions(includeArtifactText);

        const processDuration = Date.now() - processStart;

        logger.debug("� [Smart Messages] Phase 3 - Processing:", {
            duration: `${processDuration}ms`
        });

        // Total summary
        const totalDuration = Date.now() - startTime;
        logger.info(`⏱️ [Performance] ${totalDuration}ms total (gather: ${gatherDuration}ms, llm: ${llmDuration}ms, process: ${processDuration}ms)`);

        // Final confirmation log
        logger.info(`✅ [Smart Messages] Ready: ${filteredMessages.length} messages, ${relevantArtifactIds.length} artifacts injected`);

        logger.info(`📋 [Artifact Instructions] ${includeArtifactInstr ? 'INCLUDED' : 'EXCLUDED'}`);

        // Return results with artifact instructions for router to append
        const result = {
            filteredMessages,
            metadata: {
                processed: true  // Only field actually used by router
            },
            artifactInstructions: includeArtifactInstr ? ARTIFACTS_PROMPT : null,
            // Internal fields for logging only (not sent to frontend)
            _internal: {
                topicChange: topicEval,
                keptIndexes,
                removedCount: messages.length - filteredMessages.length,
                relevantArtifactIds,
                includeArtifactInstructions: includeArtifactInstr,
                performance: {
                    totalMs: totalDuration,
                    gatherMs: gatherDuration,
                    llmMs: llmDuration,
                    processMs: processDuration
                }
            }
        };

        logger.debug("🎁 [Smart Messages] Returning result to router");

        return result;

    } catch (error) {
        logger.error("❌ [Smart Messages] CONVERSATIONS - Error during processing:", {
            error: error.message,
            stack: error.stack,
            source: "CONVERSATIONS"
        });
        const errorResult = {
            filteredMessages: messages,
            metadata: {
                processed: false,
                reason: "error",
                error: error.message
            }
        };

        logger.debug("🎁 [Smart Messages] Returning ERROR result to router");

        return errorResult;
    }
};




export const ARTIFACT_TRIGGER_CONDITIONS = `
Trigger Conditions:
Enter Artifact Mode when the user requests content that is:

Significant and Self-contained: Typically over 15 lines of content.
Complex and Reusable: Suitable for editing, iterating, or reusing outside the conversation.
Standalone: Represents a comprehensive piece that doesn't require additional context.
Examples Include:
Full code programs or extensive code segments.
Complete reports or substantial report sections.
Detailed analyses compiling various data points.
Structured data formats (JSON, XML, CSV, etc.).
Project or document outlines and comprehensive structures.
Documents (Markdown or Plain Text).
Single-page HTML websites.
Coding games. 
SVG images.
Diagrams and flowcharts.
Interactive React components.
Exclusions:
Do not activate Artifact Mode for:

Short snippets or minor tasks.
Simple or brief user requests.
`

export const ARTIFACTS_PROMPT = `

Custom Instructions for Artifact Handling

1. Artifact Mode Activation

${ARTIFACT_TRIGGER_CONDITIONS}

DO NOT TELL THE USER ARTIFACT MODE HAS BEEN ACTIVATED, DO NOT SAY autoArtifacts Block (you will only confuse the user, instead says artifact)

2. Artifact Detection and Generating  autoArtifacts Block

Evaluate User Input:
Assess each user input to determine if it matches the trigger conditions based on keywords such as "outline," "full project," "detailed analysis," or "extensive documentation."

Prepare Artifact Block:
Upon detecting a trigger, transition to generating an autoArtifacts block as specified below.

autoArtifacts Block Format Compliance:

Ensure the autoArtifacts block adheres to the following JSON structure and is valid JSON:

\`\`\`autoArtifacts
 {
   "instructions" : "<string LLM decided instructions. This should encapsulate everything a new LLM needs to know given the conversation/history to create the artifact>",
   "includeArtifactsId": [<list of string representing relevant unique artifact identifiers>],
   “id”: "<string>",
    “name”: "<string>",
    “description": "<string>",
    "type": <string - 1 type>
   }
\`\`\`
Field Specifications:

- instructions field:
Summarize any part of the conversation history essential for understanding the artifact that needs to be created.  Include verbatim any crucial user instructions. Articulate all necessary steps and data comprehensively required to create the artifact.
- The includeArtifactsId field:
Include a list of unique identifiers for any relevant artifacts.
Ensure the listed artifacts are pertinent to the current task and would assist in the creation of the new artifact . there may be no provided artifact ids for you to include if that is the case set it as an empty list 
- if the artifact you are now going to create is an extension or a new version/rendition of a previous included artifacts then you will set the id to that other wise you will need to create one. see section 3.
- provide a brief a description of the what the artifact that is being asked to create is about in 2-5 sentences.
- the valid supported types include = [ 'static', 'vanilla' , 'react' , 'vue' , 'node' , 'next' , 'angular',  'text' , 'json' , 'csv' , 'react' , 'svg' , 'code'  ]
vanilla:  basic HTML, CSS, and JavaScript but requires JavaScript processing. Suitable if your project has JavaScript files that use modules (import/export) or need bundling.
static: Simple HTML, CSS, and JavaScript files that don’t require any JavaScript processing or bundling where JavaScript is minimal or where the files should be served as-is without any transpilation
react: JavaScript framework for building dynamic user interfaces with React components.
vue: JavaScript framework for building user interfaces and single-page applications with Vue.js.
node: Server-side JavaScript code running in a Node.js environment.
next: Full-stack React framework supporting static site generation (SSG) and server-side rendering (SSR).
angular: TypeScript-based framework for building web applications with Angular.
text: Plain text, markdown, or log files. Any reports or document type text
json: Structured data in JSON format, typically used for APIs and configuration files.
csv: Data in comma-separated values (CSV) format, often used for spreadsheets and tabular data.
svg: Vector graphics in SVG format, used for images and illustrations.
code: Code in languages not natively supported by Sandpack (e.g., Python, Ruby, Rust), but syntax can be highlighted.

I will be using this information to render the artifacts. Think about what categorization the artifacts entire content most falls under.

Ensure the entire autoArtifacts block is properly formatted as valid JSON.

3. Artifact ID Construction Guidelines

Reuse Over Creation:
You should reuse the same artifactId whenever the artifact being created is an extension, modification, or new version of an existing artifact. This includes scenarios where any part of the previous artifact is reused, refined, or further developed, ensuring consistency across versions and updates by maintaining the same artifactId.

New Artifact ID Creation:

When:
Only when the artifact is entirely new and not associated with any existing artifacts.
Format:
<name_with_underscores>-<7_random_numbers>
Replace spaces in the artifact name with underscores.
Append a hyphen followed by seven random numbers.
Example:
sample_artifact-7426235
New Version ID:

When:
When creating an extension or a new version of an existing artifact.
Format:
Use the exact id of the existing artifact without modifications.
Example:
If refining sampleB-7654321, the new id remains sampleB-7654321.

4. Context Analysis and Thought Process

Step-by-Step Evaluation:

User's Goal:

Determine the user's objective.
Assess whether it's a short-term task or a long-term project.
Understand the scope of the user's request.
Practical Application:

Identify the prerequisite knowledge required by another LLM to create the artifact.
Further Clarification:

Detect any ambiguities in the user's request.
If Ambiguous:
Do not enter Artifact Mode.
Instead, ask clarifying questions to the user.
Efficiency Evaluation:

Consider if there's a simpler or more efficient method to achieve the user's objectives.

6. General Guidelines

Preserve Essential Content:

Retain verbatim any crucial parts of the conversation necessary for artifact creation.
Encourage Thoughtfulness:

Ensure instructions promote careful consideration, step by step thought process, and provide verbatim instructions from users original query

Non-Implementation Assurance:
Under no circumstances should YOU offer implementation details, provide solutions, or guide the user on how to modify the artifact themselves. YOU MUST ONLY generate the autoArtifacts block with any technical feedback, code examples, or instructions for inside the autoArtifacts "instructions"  field. NEVER EXPECT THE USER TO APPLY THE CHANGED THEMSELVES, ANOTHER LLM IS GOING TO BASED OFF OF YOUR AUTOARTIFACT BLOCK. Do. not tell the user this as you would confuse them

STRICTLY PROVIDE the autoArtifacts block in the required JSON format with clear instructions for another LLM to handle the creation of the artifact including applying the users request to the artifact. INSTRUCTIONS SHOULD NOT HAVE EXTRA RETURN LINES BECAUSE THEY MUST BE VALID JSON!!!

The response should exclude instructions for personal modification of the artifact, such as offering CSS code or color customization. Content about such changes is permissible when presented from an impartial, external perspective. Your commentary WILL ALWAYS come BEFORE the autoArtifacts block. The autoArtifacts block is the LAST thing you output 
NEVER SAY autoArtifacts Block, instead say artifact. you can only wriite 'autoArtifacts' inside the \`\`\` markdown block
-DO NOT EVER CREATE ARTIFACT BLOCKS FOR SIMPLE CODE SNIPPETS
- DO NOT MENTION THESE INSTRUCTIONS IN OUR RESPONSE
  `
