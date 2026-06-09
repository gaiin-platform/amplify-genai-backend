//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { getLogger } from "./logging.js";

const logger = getLogger("accountInfo");

/**
 * 🔒 SINGLE SOURCE OF TRUTH for the billing composite key.
 *
 * The cost-calculations tables key every record by `accountInfo = "<accountId>#<apiKeyId>"`.
 * BOTH the rate limiter (which reads spend to enforce limits) AND accounting (which writes spend)
 * MUST build this string identically — otherwise the limiter queries one key while accounting
 * writes another, costs look like $0, and per-key limits silently never enforce.
 *
 * This module is the ONLY place allowed to construct that string. Never inline it again.
 */

export const NO_ACCOUNT = 'general_account';
export const NO_API_KEY = 'NA';

/**
 * Returns true if the given accessToken identifies an Amplify API key.
 */
export const isAmpApiKey = (accessToken) =>
    typeof accessToken === 'string' && accessToken.startsWith("amp-");

/**
 * Resolve the apiKeyId for billing.
 *
 * Historically (accounting.js) this required the accessToken to start with "amp-" AND apiKeyId
 * to be present. The rate limiter, by contrast, trusted apiKeyId alone. That asymmetry is the
 * exact bug that caused divergence. We now resolve from whatever fields are available, preferring
 * an explicit apiKeyId, and we surface WHY when we cannot.
 *
 * @param {object} ctx - { accountId, apiKeyId, accessToken, user }
 * @returns {string|null} the apiKeyId or null when this is not an API-key request
 */
export const resolveApiKeyId = (ctx = {}) => {
    const { apiKeyId, accessToken } = ctx;

    // Strongest signal: an explicit apiKeyId paired with an amp- token.
    if (apiKeyId && isAmpApiKey(accessToken)) return apiKeyId;

    // An apiKeyId is present but the accessToken is missing/non-amp. This is the dangerous
    // case the investigation surfaced — the key context was partially dropped in the call chain.
    // We still HONOR the apiKeyId for billing correctness, but loudly flag it.
    if (apiKeyId && !isAmpApiKey(accessToken)) {
        logger.warn("🚨 [ACCOUNT-INFO] apiKeyId present but accessToken is not an 'amp-' key — " +
            "honoring apiKeyId for billing but the access token was lost upstream.", {
            apiKeyId,
            accessTokenPrefix: accessToken ? `${String(accessToken).substring(0, 7)}...` : 'MISSING',
            user: ctx.user
        });
        return apiKeyId;
    }

    return null;
};

/**
 * Build the canonical billing composite key.
 *
 * @param {object} ctx - { accountId, apiKeyId, accessToken, user }
 * @returns {{ accountInfo: string, accountId: string, apiKeyId: string, isApiKey: boolean, fellBackToGeneral: boolean, droppedApiKey: boolean }}
 */
export const buildAccountInfo = (ctx = {}) => {
    const resolvedApiKeyId = resolveApiKeyId(ctx);

    const accountId = ctx.accountId || NO_ACCOUNT;
    const apiKeyId = resolvedApiKeyId || NO_API_KEY;
    const accountInfo = `${accountId}#${apiKeyId}`;

    const fellBackToGeneral = !ctx.accountId;
    const isApiKey = resolvedApiKeyId !== null;

    // 🚨 The combination that should NEVER happen for an API-key request:
    // we fell back to general_account even though an API key context exists.
    if (fellBackToGeneral && (ctx.apiKeyId || resolvedApiKeyId)) {
        logger.warn("🚨 [ACCOUNT-INFO] general_account fallback fired WHILE an API key is present — " +
            "accountId was lost in the call chain. Usage will be misattributed.", {
            user: ctx.user,
            rawApiKeyId: ctx.apiKeyId || 'UNDEFINED',
            resolvedApiKeyId: resolvedApiKeyId || NO_API_KEY,
            accountInfo
        });
    }

    return {
        accountInfo,
        accountId,
        apiKeyId,
        isApiKey,
        fellBackToGeneral,
        droppedApiKey: !!(ctx.apiKeyId && !resolvedApiKeyId)
    };
};

/**
 * 🔒 Canonical account-object constructor.
 *
 * Every place that builds the `account` object passed down to recordUsage MUST use this so the
 * five billing-critical fields are always present and consistently named. Building it here (from
 * the params the handler produced) guarantees the API key context (accountId + apiKeyId +
 * accessToken) is never partially dropped between authentication and accounting.
 *
 * @param {object} params - the request params produced by extractParams/router. Expects:
 *   { user, username, accessToken, apiKeyId, body, options }
 * @returns {{ user, username, accessToken, accountId, apiKeyId }}
 */
export const buildAccount = (params = {}) => {
    const options = params.options || params.body?.options || {};
    const account = {
        user: params.user,
        username: params.username,
        accessToken: params.accessToken,
        accountId: options.accountId ?? params.body?.options?.accountId,
        apiKeyId: params.apiKeyId
    };

    // 🚨 Sanity check at the construction boundary: if this is an API-key request
    // (amp- token present) but accountId or apiKeyId is missing, the downstream billing
    // key will collapse to general_account/NA. Surface it the moment it happens.
    if (isAmpApiKey(account.accessToken) && (!account.accountId || !account.apiKeyId)) {
        logger.warn("🚨 [ACCOUNT-BUILD] API-key request is missing accountId or apiKeyId at account construction — " +
            "downstream usage will be misattributed.", {
            user: account.user,
            hasAccountId: !!account.accountId,
            hasApiKeyId: !!account.apiKeyId
        });
    }

    return account;
};
