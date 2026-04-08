//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { extractKey } from "../datasource/datasources.js";
import { getLogger } from "./logging.js";

const logger = getLogger("permissions");

const permissionsEndpoint = process.env.API_BASE_URL + "/utilities/can_access_objects";

export const canReadDatasource = (userId, datasourceId) => {
    return extractKey(datasourceId).startsWith(userId + "/");
}


export const canReadDataSources = async (accessToken, dataSources) => {
    // Bypass permissions check in local development since S3 event triggers
    // (which call update_object_permissions) don't fire with serverless offline
    // Uncomment for local development if needed 
    // if (process.env.LOCAL_DEVELOPMENT) {
    //     logger.debug("Local dev detected, bypassing permissions check");
    //     return true;
    // }

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

        const responseBody = await response.json();
        const statusCode = responseBody.statusCode || response.status;
        logger.debug("Response body:", responseBody);

        // Parse the body which contains the actual data
        const bodyData = typeof responseBody.body === 'string' ? JSON.parse(responseBody.body) : responseBody.body;

        // Handle 200 (all allowed) or 207 (partial - stopped at first denial)
        if (statusCode === 200 || statusCode === 207) {
            const allowedSources = bodyData.allowedSources || [];
            const stoppedEarly = bodyData.stoppedEarly || false;

            // For 207, denied source info is at top level (maintains backward compatibility)
            // objectId may or may not be present — also handle the case where 207 has no objectId
            // but allowedSources is empty (full denial with no explicit objectId field)
            if (statusCode === 207) {
                const deniedSources = [];

                if (bodyData.objectId) {
                    // Standard denial format: objectId at top level
                    deniedSources.push({
                        objectId: bodyData.objectId,
                        reason: bodyData.reason,
                        accessType: bodyData.accessType,
                        userLevel: bodyData.userLevel
                    });
                } else {
                    // Fallback: no explicit objectId — find which sources weren't in allowedSources
                    const allowedIds = new Set(allowedSources.map(s => s.objectId || s));
                    for (const ds of dataSources) {
                        if (!allowedIds.has(ds.id)) {
                            deniedSources.push({
                                objectId: ds.id,
                                reason: bodyData.reason || 'no_permission_record',
                                accessType: 'read'
                            });
                        }
                    }
                }

                logger.info(`Permission check stopped at denial: ${allowedSources.length} allowed so far, ${deniedSources.length} denied`);
                return {
                    hasAccess: allowedSources.length > 0,
                    allowedSources: allowedSources.map(s => s.objectId || s),
                    deniedSources,
                    statusCode: 207,
                    stoppedEarly: stoppedEarly || deniedSources.length > 0
                };
            }

            // All checked and allowed (statusCode 200)
            logger.info(`Permission check complete: all ${allowedSources.length} sources allowed`);
            return {
                hasAccess: true,
                allowedSources: allowedSources.map(s => s.objectId || s),
                deniedSources: [],
                statusCode: 200,
                stoppedEarly: false
            };
        }

        // Handle errors (403, 500, etc.)
        logger.warn("User does not have access to datasources: " + statusCode);
        return {
            hasAccess: false,
            allowedSources: [],
            deniedSources: [],
            statusCode: statusCode,
            error: bodyData?.message || "Access denied"
        };
    }
    catch (e) {
        logger.error("Error checking access on data sources: " + e);
        return {
            hasAccess: false,
            allowedSources: [],
            deniedSources: [],
            statusCode: 500,
            error: e.message
        };
    }

    return {
        hasAccess: false,
        allowedSources: [],
        deniedSources: [],
        statusCode: 500
    };
}