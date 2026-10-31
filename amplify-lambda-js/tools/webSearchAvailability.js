export const webSearchProviderNames = [
    'brave_search',
    'tavily',
    'serper',
    'serpapi',
    'bedrock_agentcore'
];

const WEB_SEARCH_PROVIDERS = new Set(webSearchProviderNames);

export function hasConfiguredWebSearchProvider(apiKeys = {}) {
    return Object.entries(apiKeys).some(([provider, key]) => WEB_SEARCH_PROVIDERS.has(provider) && Boolean(key));
}

/** Resolve usable search credentials without letting missing admin config shadow user keys. */
export function resolveWebSearchAvailability({
    requested,
    allowed,
    apiKeys = {},
    adminKey = null
}) {
    let resolvedApiKeys = apiKeys;
    let keylessAvailable = false;

    if (adminKey?.provider && adminKey.api_key) {
        resolvedApiKeys = { ...apiKeys, [adminKey.provider]: adminKey.api_key };
    } else if (adminKey?.provider === 'bedrock_agentcore' && adminKey.config?.gatewayUrl) {
        keylessAvailable = true;
    }

    const enabled = requested === true && allowed === true &&
        (hasConfiguredWebSearchProvider(resolvedApiKeys) || keylessAvailable);
    return { enabled, apiKeys: resolvedApiKeys, keylessAvailable };
}
