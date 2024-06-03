

const pdbsHandler = async (chatRequest, params, dataSource) => {
    const dbId = dataSource.id.slice('pdbs://'.length);
    const query = chatRequest.messages.slice(-1)[0].content;

    const url = "https://dev-api.vanderbilt.ai/pdb/sql/llmquery"
    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + params.account.accessToken
        },
        body: JSON.stringify({
            data: {
                "id":dbId,
                "query":query,
                "options": {
                    "model": "gpt-4o"
                }
            }
        })
    });

    const data = await response.json();

    const contents = [{
        "content": JSON.stringify(data.data),
        "tokens": 1000,
        "location": {
            "key": dataSource.id
        },
        "canSplit": true
    }];

    const content =
        {
            "name": dataSource.id,
            "content": contents
        }

    return content;
}


const getDatasourceHandler = async (sourceType, chatRequest, params, dataSource) => {
    if (sourceType === 'pdbs://') {
        return pdbsHandler;
    }

    return null;
}

export default getDatasourceHandler;