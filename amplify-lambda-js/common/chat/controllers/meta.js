//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

export const aliasContexts = (metadata, contexts) => {
    return contexts.map(context => {
        return {
            ...context,
            id: metadata.sources[context.id]
        }
    });
}

export const getSourceMetadata = ({contexts}) => {
    const metadata = {}
    const ids = contexts.map(obj => obj.id);
    const uniqueIds = [...new Set(ids)];
    const lookupById = uniqueIds.reduce((acc, id, index) => {
        acc[id] = index;
        return acc;
    }, {});
    metadata.sources = lookupById;
    return metadata;
}