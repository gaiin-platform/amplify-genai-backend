//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

// Removed chatFn imports - now using LiteLLM unified interface

export const getRequestId = (params) => {
    return params.requestId;
}

export const getModel = (params) => {
    return params.model;
}

export const ModelTypes = {
    CHEAPEST: 'cheapestModel',
    ADVANCED: 'advancedModel',
    DOCUMENT_CACHING: 'documentCachingModel',
}
                                     // ModelTypes 
export const getModelByType = (params, identifier) => {
    return params[identifier] ?? (params.options[identifier] ?? getModel(params));
}


export const setModel = (params, model) => {
    const options = params.options || {};
    return {...params, options: {...options, model}, model};
}

export const getOptions = (params) => {
    return params.options;
}

export const setUser = (params, user) => {
    return {...params, account:{user}};
}

export const getUser = (params) => {
    return params.account.user;
}

export const getAccessToken = (params) => {
    return params.account.accessToken;
}

export const getAccountId = (params) => {
    return params.account.accountId;
}

export const getMaxTokens = (params) => {
    return params.options.maxTokens;
}

export const getBudgetTokens = (params, maxTokens) => {
    const reasoning_effort = params.options.reasoningLevel ?? "low";

    // Desired budget based on reasoning level (from original implementation)
    const BUDGET_BY_LEVEL = { low: 1024, medium: 2048, high: 4096 };
    const desiredBudget = BUDGET_BY_LEVEL[reasoning_effort] || 1024;

    // Bedrock constraint: maxTokens MUST be strictly greater than budget_tokens
    // Reserve a reasonable buffer for the actual response (20% of maxTokens, minimum 256)
    const MIN_OUTPUT_RESERVE = Math.max(Math.floor(maxTokens * 0.2), 256);
    const maxAllowedBudget = maxTokens - MIN_OUTPUT_RESERVE;

    // If desired budget fits within constraints, use it
    if (desiredBudget < maxAllowedBudget) {
        return desiredBudget;
    }

    // Otherwise scale down, but maintain minimum 1024 budget (original behavior)
    // If maxTokens is too small for minimum budget, caller should disable reasoning
    return Math.max(maxAllowedBudget, 1024);

}


export const isOpenAIModel = (modelId) => {
    return modelId && (modelId.includes("gpt") || /^o\d/.test(modelId));
}

