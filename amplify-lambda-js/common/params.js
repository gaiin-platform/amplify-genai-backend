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
    let budget_tokens = 1024;
    switch (reasoning_effort) {
        case "medium":
            budget_tokens = 2048;
            break;
        case "high":
            budget_tokens = 4096;
            break;
    }
    if (budget_tokens > maxTokens) {
        budget_tokens = Math.max(maxTokens / 2, 1024);
    }
    return budget_tokens;

}

export const isOpenAIModel = (modelId) => {
    return modelId && (modelId.includes("gpt") || /^o\d/.test(modelId));
}

export const isGeminiModel = (modelId) => {
    return modelId && modelId.includes("gemini");
}