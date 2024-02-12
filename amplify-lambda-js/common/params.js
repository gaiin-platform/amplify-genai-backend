
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
