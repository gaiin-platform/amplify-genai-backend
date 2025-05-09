//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {extractKey} from "../datasource/datasources.js";

const permissionsEndpoint = process.env.API_BASE_URL + "/utilities/can_access_objects";

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

        const responseBody = await response.json();  // Extracting the response body as JSON as per Allens code returning statusCode in body
        const statusCode = responseBody.statusCode || undefined;
        console.log("Response body:", responseBody);

        if (response.status !== 200 || statusCode !== 200) {
            console.error("User does not have access to datasources: " + response.status);
            return false;
        }
        else if(response.status === 200 && statusCode === 200) {
            return true;
        }
    }
    catch (e) {
        console.error("Error checking access on data sources: " + e);
        return false;
    }

    return false;
}