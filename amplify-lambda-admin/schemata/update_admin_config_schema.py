from .rate_limit_schema import rate_limit_schema

update_admin_config_schema = {
    "type": "object",
    "properties": {
        "configurations": {
            "type": "array",
            "items": {
                "oneOf": [
                    {
                        # Configuration for 'admins'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "admins"
                            },
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                },
                                "minItems": 1
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'applicationVariables'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "applicationVariables"
                            },
                            "data": {
                                "type": "object",
                                "additionalProperties": {
                                    "type": "string"
                                }
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'applicationSecrets'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "applicationSecrets"
                            },
                            "data": {
                                "type": "object",
                                "additionalProperties": {
                                    "type": "string"
                                }
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'featureFlags'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "featureFlags"
                            },
                            "data": {
                                "type": "object",
                                "patternProperties": {
                                    "^.*$": {
                                        "type": "object",
                                        "properties": {
                                            "enabled": {
                                                "type": "boolean"
                                            },
                                            "userExceptions": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            },
                                            "amplifyGroupExceptions": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            }
                                        },
                                        "required": ["enabled", "userExceptions"],
                                        "additionalProperties": False
                                    }
                                },
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'openaiEndpoints'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "openaiEndpoints"
                            },
                            "data": {
                            "type": "object",
                            "properties": {
                                "models": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "patternProperties": {
                                    "^.*$": {
                                        "type": "object",
                                        "properties": {
                                        "endpoints": {
                                            "type": "array",
                                            "items": {
                                            "type": "object",
                                            "properties": {
                                                "url": { "type": "string" },
                                                "key": { "type": "string" }
                                            },
                                            "required": ["url", "key"],
                                            "additionalProperties": False
                                            },
                                            "minItems": 1
                                        }
                                        },
                                        "required": ["endpoints"],
                                        "additionalProperties": False
                                    }
                                    },
                                    "additionalProperties": False
                                }
                                }
                            },
                            "required": ["models"],
                            "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'supportedModels'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "supportedModels"
                            },
                            "data": {
                                "type": "object",
                                "patternProperties": {
                                    "^.*$": {
                                        "type": "object",
                                        "properties": {
                                            "id": {
                                                "type": "string"
                                            },
                                            "name": {
                                                "type": "string"
                                            },
                                            "provider": {
                                                "type": "string"
                                            },
                                            "description": {
                                                "type": "string"
                                            },
                                             "isAvailable": {
                                                "type": "boolean"
                                            },
                                            "isBuiltIn": {
                                                "type": "boolean"
                                            },
                                            "systemPrompt": {
                                                "type": "string"
                                            },
                                            "supportsSystemPrompts": {
                                                "type": "boolean"
                                            },
                                             "supportsImages": {
                                                "type": "boolean"
                                            },
                                            "supportsReasoning": {
                                                "type": "boolean"
                                            },
                                             "inputContextWindow": {
                                                "type": "number"
                                            },
                                             "outputTokenLimit": {
                                                "type": "number"
                                            },
                                            "inputTokenCost": {
                                                "type": "number"
                                            },
                                             "outputTokenCost": {
                                                "type": "number"
                                            },
                                             "cachedTokenCost": {
                                                "type": "number"
                                            },
                                            "exclusiveGroupAvailability": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            }
                                        },
                                        "required": ["id","name", "provider", "description", "isAvailable",  "isBuiltIn",
                                                     "supportsImages", "supportsReasoning", "supportsSystemPrompts", "systemPrompt",
                                                     "inputContextWindow", "outputTokenLimit", "inputTokenCost", "outputTokenCost", 
                                                     "cachedTokenCost", "exclusiveGroupAvailability"],
                                        "additionalProperties": False
                                    }
                                },
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'defaultModels'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "defaultModels"
                            },
                            "data": {
                                "type": "object",
                                "properties": {
                                    "user": {"type": ["string", "null"]},
                                    "advanced": {"type": ["string", "null"]},
                                    "cheapest": {"type": ["string", "null"]},
                                    "documentCaching": {"type": ["string", "null"]},
                                    "agent": {"type": ["string", "null"]},
                                    "embeddings": {"type": ["string", "null"]},
                                    "qa": {"type": ["string", "null"]}
                                },
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'amplifyGroups'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "amplifyGroups"
                            },
                            "data": {
                                "type": "object",
                                "patternProperties": {
                                    "^.*$": {
                                        "type": "object",
                                        "properties": {
                                            "groupName": {
                                                "type": "string"
                                            },
                                            "createdBy": {
                                                "type": "string"
                                            },
                                            "members": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            },
                                            "includeFromOtherGroups": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            },
                                            "rateLimit" : rate_limit_schema,
                                            "isBillingGroup": {
                                                "type": "boolean"
                                            }
                                        },
                                        "required": ["groupName", "createdBy", "members", "rateLimit", "isBillingGroup"],
                                        "additionalProperties": False

                                    }
                                },
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'assistantAdminGroups'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "assistantAdminGroups"
                            },
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "group_id": {"type": "string"},
                                        "amplifyGroups": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "isPublic": {"type": "boolean"},
                                        "supportConvAnalysis": {"type": "boolean"},
                                    },
                                    "required": ["group_id", "amplifyGroups", "isPublic", "supportConvAnalysis"],
                                "additionalProperties": False
                                },
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'powerPointTemplates'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "powerPointTemplates"
                            },
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "isAvailable": {"type": "boolean"},
                                        "amplifyGroups": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        }
                                    },
                                    "required": ["name", "isAvailable", "amplifyGroups"],
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'promtCostAlert'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "promtCostAlert"
                            },
                            "data": {
                                "type": "object",
                                "properties": {
                                    "isActive": {"type": "boolean"},
                                    "alertMessage": {"type": "string"},
                                    "cost": {"type": "number"},
                                },
                                "required": ["isActive", "alertMessage", "cost"],
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'emailSupport'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "emailSupport"
                            },
                            "data": {
                                "type": "object",
                                "properties": {
                                    "isActive": {"type": "boolean"},
                                    "email": {"type": "string"},
                                },
                                "required": ["isActive", "email"],
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'aiEmailDomain'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "aiEmailDomain"
                            },
                            "data":  {
                                "type": "string",
                            },
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'defaultConversationStorage'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "defaultConversationStorage"
                            },
                            "data": {
                                "type": "string",
                                "enum": ["future-local", "future-cloud"],
                            },
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                                
                    {
                        # Configuration for 'rateLimit'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "rateLimit"
                            },
                            "data": rate_limit_schema
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'integrations'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "integrations"
                            },
                            "data": {
                                "type": "object",
                                "patternProperties": {
                                    "^(google|microsoft|drive|github|slack)$": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "id": {"type": "string"},
                                                "icon": {"type": "string"},
                                                "description": {"type": "string"},
                                                "isAvailable": {"type": "boolean"},
                                            },
                                            "required": ["name", "id", "icon", "description"],
                                            "additionalProperties": False
                                        },
                                    }
                                },
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                ]
            }
        }
    },
    "required": ["configurations"],
    "additionalProperties": False
}
