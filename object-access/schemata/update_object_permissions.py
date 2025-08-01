update_object_permissions = {
    "type": "object",
    "properties": {
        "emailList": {
            "type": "array",
            "description": "An array of userids to update permissions for.",
        },
        "dataSources": {
            "type": "array",
            "description": "A list of data sources to for permission updates.",
        },
        "permissionLevel": {
            "type": "string",
            "description": "The permission level to set for the users.",
        },
        "principalType": {
            "type": "string",
            "description": "The principal type to set for the users.",
        },
        "objectType": {
            "type": "string",
            "description": "The object type to set for the object.",
        },
        "policy": {
            "type": "string",
            "description": "Placehold for future fine grained policy map",
        },
    },
    "required": ["dataSources", "emailList", "permissionLevel"],
}
