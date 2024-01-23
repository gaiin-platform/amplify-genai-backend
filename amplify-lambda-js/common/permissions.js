import {extractKey} from "../datasource/datasources.js";


export const canReadDatasource = (userId, datasourceId) => {
    return extractKey(datasourceId).startsWith(userId+"/");
}
