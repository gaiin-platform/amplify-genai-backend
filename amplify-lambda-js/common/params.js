

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

export const getOptions = (params) => {
    return params.options;
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
