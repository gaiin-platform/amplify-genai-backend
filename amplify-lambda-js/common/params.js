//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import { Models } from "../models/models.js";
// This needs to be refactored into a class and all
// of the places that use params need to be updated
//
// const assistantParams = {
//     account: {
//         user: params.user,
//         accessToken: params.accessToken,
//         accountId: options.accountId,
//     },
//     model,
//     requestId,
//     options
// };

export const getRequestId = (params) => {
    return params.requestId;
}

export const getModel = (params) => {
    return params.model;
}

export const setModel = (params, model) => {
    return {...params, model};
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


export const getCheapestModelEquivalent = (model)  => {
    if (model.id.includes("gpt")) {
        return Models["gpt-35-turbo"];
    } else if (model.id.includes("anthropic")) { 
        return Models["anthropic.claude-3-haiku-20240307-v1:0"];
    } else if (model.id.includes("mistral")) { 
        return Models['mistral.mistral-7b-instruct-v0:2'];
    }
}

export const getMostAdvancedModelEquivalent = (model) => {
    if (model.id.includes("gpt")) {
        return Models["gpt-4-1106-Preview"];
    } else if (model.id.includes("anthropic")) { 
        return Models["us.anthropic.claude-3-5-sonnet-20241022-v2:0"];
    } else if (model.id.includes("mistral")) { 
        return Models['mistral.mistral-large-2402-v1:0'];
    }
}