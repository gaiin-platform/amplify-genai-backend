import {extractKey} from "../datasource/datasources.js";

const permissionsEndpoint = process.env.OBJECT_ACCESS_PERMISSIONS_ENDPOINT;

export const canReadDatasource = (userId, datasourceId) => {
    return extractKey(datasourceId).startsWith(userId+"/");
}


export const canReadDataSources = async (accessToken, dataSources) => {
    const accessLevels = {}
    dataSources.forEach(ds => {
        accessLevels[ds.id] = 'read'
    });

    const requestData = {
        data: {
            dataSources: accessLevels
        }
    }

    try {
        const response = await fetch(permissionsEndpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${accessToken}`
            },
            body: JSON.stringify(requestData)
        });

        if (response.status !== 200) {
            console.error("User does not have access to datasources: " + response.status);
            return false;
        }
        else if(response.status === 200) {
            return true;
        }
    }
    catch (e) {
        console.error("Error checking access on data sources: " + e);
        return false;
    }

    return false;
}