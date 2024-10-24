
export const DATASOURCE_TYPE = "#$"
export const OP_TYPE = "#!"
export const ASSISTANT_TYPE = "#@"

export const addAllReferences = (references, refType, refs) => {
    for(let ref of refs) {
        addReference(references, refType, ref);
    }
}

export const addReference = (references, refType, ref) => {
    if (!references[refType]) {
        references[refType] = [];
    }

    references[refType].push({type:refType, object:ref});
}

export const getReferencesByType = (references, refType) => {
    const refs = references[refType] || [];
    return refs.map((r,idx) => {return {...r, id: idx}});
}

export const getReferences = (references) => {
    return Object.keys(references).flatMap((k) => {
        return getReferencesByType(references, k);
    });
}