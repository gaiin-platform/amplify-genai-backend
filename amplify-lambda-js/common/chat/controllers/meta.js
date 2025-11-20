//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {PassThrough} from "stream";
import {metaSource} from "../../sources.js";

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

export const sendSourceMetadata = (multiplexer, metadata) => {
    const srcList = Array.from(Object.keys(metadata.sources)).reduce((arr, key) => ((arr[metadata.sources[key]] = key), arr), []);
    const metadataForClient = {
        ...metadata,
        sources: srcList
    }

    const metadataStream = new PassThrough();
    multiplexer.addSource(metadataStream, metaSource, null);
    metadataStream.write("data: "+JSON.stringify({d:metadataForClient})+"\n");
    metadataStream.end();
}