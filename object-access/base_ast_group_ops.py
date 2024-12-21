ops = [
{   "id": "getUserAvailableModels",
    "url": "/available_models",
    "method": "GET",
    "name": "getUserAvailableModels",
    "description": """Retrieve a list of available AI models for the user, including details such as model ID, name, description, and capabilities.

Example response:
{
    "success": true,
    "data": {
        # list of Model dicts. example
        "models": [
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "description": "An optimized version of GPT-4 for general use.",
                "inputContextWindow": 200000,
                "outputTokenLimit": 4096, 
                "supportsImages": true,
                "provider": "OpenAI",
                "supportsSystemPrompts": true,
                "systemPrompt": "Additional Prompt",
            },
        ],
        "default": <Model dict>,
        "advanced": <Model dict>,
        "cheapest": <Model dict>
    }
}
""",
    "type": "apiDocumentation",
    "params": [],
    "tags": ["apiDocumentation"],
},

{
"id": "chatWithAmplify",
"url": "/chat",
"method": "POST",
"name": "chatWithAmplify",
"description": """Interact with Amplify via real-time streaming chat capabilities, utilizing advanced AI models. 
Example request: 
    {
    "data":{
        "model": "gpt-4o",
        "temperature": 0.7,
        "max_tokens": 150,
        "dataSources": [{"id": "s3://user@vanderbilt.edu/2014-qwertyuio","type": "application/pdf"}],
        "messages": [
            {
                "role": "user",
                "content": "What is the capital of France?"
            }
        ],
        "options": {
            "ragOnly": false,
            "skipRag": true,
            "model": {"id": "gpt-4o"},
            "assistantId": "astp/abcdefghijk",
            "prompt": "What is the capital of France?"
        }
    }
}
""",
"type": "apiDocumentation",
"params": [
    {
        "name": "model",
        "description": "String representing the model ID. User can request a list of the models by calling the /available_models endpoint"
    },
    {
        "name": "temperature",
        "description": "Float value controlling the randomness of responses. Example: 0.7 for balanced outputs."
    },
    {
        "name": "max_tokens",
        "description": "Integer representing the maximum number of tokens the model can generate in the response. Typically never over 2048. The user can confirm the max tokens for each model by calling the /available_models endpoint"
    },
    {
        "name": "dataSources",
        "description": "Array of objects representing input files or documents for retrieval-augmented generation (RAG). Each object must contain an 'id' and 'type'. Example: [{'id': 's3://example_file.pdf', 'type': 'application/pdf'}]. The user can make a call to the /files/query endpoint to get the id for their file."
    },
    {
        "name": "messages",
        "description": "Array of objects representing the conversation history. Each object includes 'role' (system/assistant/user) and 'content' (the message text). Example: [{'role': 'user', 'content': 'What is the capital of France?'}]."
    },
    {
        "name": "options",
        "description": "An object that includes advanced configurations such as: ragOnly (Boolean for retrieval-only responses), skipRag (Boolean to skip RAG), assistantId (String for assistant identification), model (Object for model configurations), and prompt (String for system prompts)."
    }
],
"tags": ["apiDocumentation"]
},

{
    "id": "viewSharedState",
    "url": "/state/share",
    "method": "GET",
    "name": "viewSharedState",
    "description": """View a list of shared resources, including assistants, conversations, and organizational folders distributed by other Amplify platform users.
    
    Example response:
    [
      {
        "note": "testing share with a doc",
        "sharedAt": 1720714099836,
        "key": "yourEmail@vanderbilt.edu/sharedByEmail@vanderbilt.edu/9324805-24382.json",
        "sharedBy": "sharedByEmail@vanderbilt.edu"
      }
    ]
    """,
    "type": "apiDocumentation",
    "params": [],
    "tags": ["apiDocumentation"]
},

{
    "id": "loadSharedState",
    "url": "/state/share/load",
    "method": "POST",
    "name": "loadSharedState",
    "description": """Retrieve specific shared data elements using their unique identifier key. 
    Example request:
    {
        "data": {
            "key": "yourEmail@vanderbilt.edu/sharedByEmail@vanderbilt.edu/932934805-24382.json"
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "key",
            "description": "String. Required. Unique identifier for the shared resource to retrieve. Users can find their keys by calling /state/share"
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "uploadFile",
    "url": "/files/upload",
    "method": "POST",
    "name": "uploadFile",
    "description": """Initiate a file upload to the Amplify platform, enabling interaction via prompts and assistants.

    Example request:
    {
        "data": {
            "type": "application/fileExtension",
            "name": "fileName.pdf",
            "knowledgeBase": "default",
            "tags": [],
            "data": {}
        }
    }

    Example response:
    {
        "success": true,
        "uploadUrl": "<uploadUrl>",
        "statusUrl": "<statusUrl>",
        "contentUrl": "<contentUrl>",
        "metadataUrl": "<metadataUrl>",
        "key": "yourEmail@vanderbilt.edu/date/293088.json"
    }

    The user can use the presigned url 'uploadUrl' to upload their file to Amplify.
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "type",
            "description": "String. Required. MIME type of the file to be uploaded. Example: 'application/pdf'."
        },
        {
            "name": "name",
            "description": "String. Required. Name of the file to be uploaded."
        },
        {
            "name": "knowledgeBase",
            "description": "String. Required. Knowledge base the file should be associated with. Default: 'default'."
        },
        {
            "name": "tags",
            "description": "Array of strings. Tags to associate with the file. Example: ['tag1', 'tag2']."
        },
        {
            "name": "data",
            "description": "Object. Additional metadata associated with the file upload."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "queryUploadedFiles",
    "url": "/files/query",
    "method": "POST",
    "name": "queryUploadedFiles",
    "description": """Retrieve a list of uploaded files stored on the Amplify. A user can retrieve details about their files include id, types, size, and more.

    Example request:
    {
        "data": {
            "pageSize": 10,
            "sortIndex": "",
            "forwardScan": false
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "startDate",
            "description": "String (date-time). Optional. Start date for querying files. Default: '2021-01-01T00:00:00Z'."
        },
        {
            "name": "pageSize",
            "description": "Integer. Optional. Number of results to return. Default: 10."
        },
        {
            "name": "pageKey",
            "description": "Object. Optional. Includes 'id', 'createdAt', and 'type' for pagination purposes."
        },
        {
            "name": "namePrefix",
            "description": "String. Optional. Prefix for filtering file names."
        },
        {
            "name": "createdAtPrefix",
            "description": "String. Optional. Prefix for filtering creation date."
        },
        {
            "name": "typePrefix",
            "description": "String. Optional. Prefix for filtering file types."
        },
        {
            "name": "types",
            "description": "Array of strings. Optional. List of file types to filter by. Default: []."
        },
        {
            "name": "tags",
            "description": "Array of strings. Optional. List of tags to filter files by. Default: []."
        },
        {
            "name": "pageIndex",
            "description": "Integer. Optional. Page index for pagination. Default: 0."
        },
        {
            "name": "forwardScan",
            "description": "Boolean. Optional. Set to 'true' for forward scanning. Default: false."
        },
        {
            "name": "sortIndex",
            "description": "String. Optional. Attribute to sort results by. Default: 'createdAt'."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "listFileTags",
    "url": "/files/tags/list",
    "method": "GET",
    "name": "listFileTags",
    "description": """Retrieve a list of all tags associated with files, conversations, and assistants on the Amplify platform.

    Example response:
    {
        "success": true,
        "data": {
            "tags": ["NewTag", "Important", "Archived"]
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [],
    "tags": ["apiDocumentation"]
},

{
    "id": "createFileTags",
    "url": "/files/tags/create",
    "method": "POST",
    "name": "createFileTags",
    "description": """Create new tags to associate with files, conversations, and assistants.

    Example request:
    {
        "data": {
            "tags": ["NewTag"]
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "tags",
            "description": "Array of strings. Required. List of tags to create. Example: ['NewTag', 'Important']."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "deleteFileTag",
    "url": "/files/tags/delete",
    "method": "POST",
    "name": "deleteFileTag",
    "description": """Delete a specific tag from the Amplify platform.

    Example request:
    {
        "data": {
            "tag": "NewTag"
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "tag",
            "description": "String. Required. The tag to be deleted. Example: 'NewTag'."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "associateFileTags",
    "url": "/files/set_tags",
    "method": "POST",
    "name": "associateFileTags",
    "description": """Associate one or more tags with a specific files only.

    Example request:
    {
        "data": {
            "id": "yourEmail@vanderbilt.edu/date/23094023573924890-208.json",
            "tags": ["NewTag", "Important"]
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "id",
            "description": "String. Required. Unique identifier of the file. Example: 'yourEmail@vanderbilt.edu/date/23094023573924890-208.json'."
        },
        {
            "name": "tags",
            "description": "Array of strings. Required. List of tags to associate. Example: ['NewTag', 'Important']."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "retrieveEmbeddings",
    "url": "/embedding-dual-retrieval",
    "method": "POST",
    "name": "retrieveEmbeddings",
    "description": """Retrieve embeddings from Amplify data sources based on user input using the dual retrieval method.

    Example request:
    {
        "data": {
            "userInput": "Can you describe the policies outlined in the document?",
            "dataSources": ["global/09342587234089234890.content.json"],
            "limit": 10
        }
    }

    Example response:
    {
        "result": [
            {
                "content": "xmlns:w=3D'urn:schemas-microsoft-com:office:word' ...",
                "file": "global/24059380341.content.json",
                "line_numbers": [15, 30],
                "score": 0.7489801645278931
            }
        ]
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "userInput",
            "description": "String. Required. Query text for embedding retrieval. Example: 'What are the main points of this document?'."
        },
        {
            "name": "dataSources",
            "description": "Array of strings. Required. List of data source IDs to retrieve embeddings from. These ids must start with global/. Example: ['global/09342587234089234890.content.json']. User can find these keys by calling the /files/query endpoint."
        },
        {
            "name": "limit",
            "description": "Integer. Optional. Maximum number of results to return. Default: 10."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "listAssistants",
    "url": "/assistant/list",
    "method": "GET",
    "name": "listAssistants",
    "description": """Retrieve a list of all Amplify assistants created or accessible by the user.

    Example response:
    {
        "success": true,
        "message": "Assistants retrieved successfully",
        "data": [
            {
                "assistantId": "astp/498370528-38594",
                "version": 3,
                "instructions": "<instructions>",
                "disclaimerHash": "348529340098580234959824580-pueiorupo4",
                "coreHash": "eiouqent84832n8989pdeer",
                "user": "yourEmail@vanderbilt.edu",
                "uri": null,
                "createdAt": "2024-07-15T19:07:57",
                "dataSources": [
                    {
                        "metadata": "<metadata>",
                        "data": "",
                        "name": "api_documentation.yml",
                        "raw": "",
                        "id": "global/7834905723785897982345088927.content.json",
                        "type": "application/x-yaml"
                    }
                ]
            }
        ]
    }
    """,
    "type": "apiDocumentation",
    "params": [],
    "tags": ["apiDocumentation"]
},

{
    "id": "shareAssistant",
    "url": "/assistant/share",
    "method": "POST",
    "name": "shareAssistant",
    "description": """Share an Amplify assistant with other users on the platform.

    Example request:
    {
        "data": {
            "assistantId": "ast/8934572093982034020-9",
            "recipientUsers": ["yourEmail@vanderbilt.edu"],
            "note": "Sharing label"
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "assistantId",
            "description": "String. Required. Unique identifier of the assistant to share. Example: 'ast/8934572093982034020-9'. prefixed with ast."
        },
        {
            "name": "recipientUsers",
            "description": "Array of strings. Required. List of email addresses of the users to share the assistant with. Example: ['user1@example.com', 'user2@example.com']."
        },
        {
            "name": "note",
            "description": "String. Optional. A note to include with the shared assistant. Example: 'Sharing this assistant for project collaboration.'"
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "createOrUpdateAssistant",
    "url": "/assistant/create",
    "method": "POST",
    "name": "createOrUpdateAssistant",
    "description": """Create or update a customizable Amplify assistant.

    Example request:
    {
        "data": {
            "name": "Sample Assistant 3",
            "description": "This is a sample assistant for demonstration purposes.",
            "assistantId": "",
            "tags": ["test"],
            "instructions": "Respond to user queries about general knowledge topics.",
            "disclaimer": "This assistant's responses are for informational purposes only.",
            "dataSources": [{"id": "e48759073324384kjsf", "name": "api_paths_summary.csv", "type": "text/csv", "raw": "", "data": "", "key": "yourEmail@vanderbilt.edu/date/w3ou009we3.json", "metadata": {"name": "api_paths_summary.csv", "totalItems": 20, "locationProperties": ["row_number"], "contentKey": "yourEmail@vanderbilt.edu/date/w3ou009we3.json.content.json", "createdAt": "2024-07-15T18:58:24.912235", "totalTokens": 3750, "tags": [], "props": {}}}],
        }
    }

    Example response:
    {
        "success": true,
        "message": "Assistant created successfully.",
        "data": {
            "assistantId": "astp/3io4u5ipy34jkelkdfweiorwur",
            "id": "ast/03uio3904583049859482",
            "version": 1
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "name",
            "description": "String. Required. Name of the assistant. Example: 'Sample Assistant 3'."
        },
        {
            "name": "description",
            "description": "String. Required. Description of the assistant's purpose."
        },
        {
            "name": "assistantId",
            "description": "String. Optional. If provided, updates an existing assistant. Example: 'astp/3io4u5ipy34jkelkdfweiorwur'. prefixed with astp."
        },
        {
            "name": "tags",
            "description": "Array of strings. Required. Tags to categorize the assistant."
        },
        {
            "name": "instructions",
            "description": "String. Required. Detailed instructions on how the assistant should respond."
        },
        {
            "name": "disclaimer",
            "description": "String. Optional. Disclaimer for the assistant's responses."
        },
        {
            "name": "dataSources",
            "description": "Array of objects. Required. List of data sources the assistant can use. You can obtain full data source objects by calling the /files/query endpoint."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "deleteAssistant",
    "url": "/assistant/delete",
    "method": "POST",
    "name": "deleteAssistant",
    "description": """Delete a specified Amplify assistant.

    Example request:
    {
        "data": {
            "assistantId": "astp/3209457834985793094"
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "assistantId",
            "description": "String. Required. Unique identifier of the assistant to delete. Example: 'astp/3209457834985793094'."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "createCodeInterpreterAssistant",
    "url": "/assistant/create/codeinterpreter",
    "method": "POST",
    "name": "createCodeInterpreterAssistant",
    "description": """Create a new Code Interpreter assistant with specific attributes for analyzing and processing data.
    Example request:
    {
        "data": {
            "name": "Data Analysis Assistant",
            "description": "An AI assistant specialized in data analysis and visualization.",
            "tags": ["data analysis"],
            "instructions": "Analyze data files, perform statistical operations, and create visualizations as requested by the user.",
            "dataSources": ["yourEmail@vanderbilt.edu/2024-05-08/0f20f0447b.json"]
        }
    }

    Example response:
    {
        "success": true,
        "message": "Assistant created successfully.",
        "data": {
            "assistantId": "yourEmail@vanderbilt.edu/ast/373849029843"
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "name",
            "description": "String. Required. Name of the Code Interpreter assistant. Example: 'Data Analysis Assistant'."
        },
        {
            "name": "description",
            "description": "String. Required. Description of the assistant's functionality. Example: 'An AI assistant specialized in data analysis and visualization.'."
        },
        {
            "name": "tags",
            "description": "Array of strings. Required. Tags to categorize the assistant. Example: ['data analysis', 'visualization']."
        },
        {
            "name": "instructions",
            "description": "String. Required. Instructions for how the assistant should handle user queries. Example: 'Analyze data files and generate insights.'."
        },
        {
            "name": "dataSources",
            "description": "Array of strings. Required. List of data source IDs the assistant will use. Starts with your email. These can be retrieved by calling the /files/query endpoint."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "chatWithCodeInterpreter",
    "url": "/assistant/chat/codeinterpreter",
    "method": "POST",
    "name": "chatWithCodeInterpreter",
    "description": """Initiate a conversation with the Code Interpreter. Each request can append new messages to the existing conversation using a unique thread ID.
    Data source keys for files can be found by calling files/query.
    Example request:
    {   
        "data": {
            "assistantId": "yourEmail@vanderbilt.edu/ast/43985037429849290398",
            "threadId": "yourEmail@vanderbilt.edu/thr/442309eb-0772-42d0-b6ef-34e20ee2355e",
            "messages": [
                { 
                    "role": "user",
                    "content": "Can you tell me something about the data analytics and what you are able to do?",
                    "dataSourceIds": ["yourEmail@vanderbilt.edu/2024-05-08/0f20f0447b.json"]
                }
            ]
        }
    }

    Example response:
    {
        "success": true,
        "message": "Chat completed successfully",
        "data": {
            "threadId": "yourEmail@vanderbilt.edu/thr/442309eb-0772-42d0-b6ef-34e20ee2355e",
            "role": "assistant",
            "textContent": "I've saved the generated pie chart as a PNG file. You can download it using the link below:\n\n[Download Ice Cream Preferences Pie Chart](sandbox:/mnt/data/ice_cream_preferences_pie_chart.png)\n",
            "content": [
                {
                    "type": "image/png",
                    "values": {
                        "file_key": "yourEmail@vanderbilt.edu/msg_P0lpFUEY _pie_chart.png ",
                        "presigned_url": "https://vu-amplify-assistants-dev-code-interpreter-files.s3.amazonaws.com/...",
                        "file_size": 149878
                    }
                }
            ]
        }
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "assistantId",
            "description": "String. Required. Unique identifier of the Code Interpreter assistant. Example: 'yourEmail@vanderbilt.edu/ast/43985037429849290398'."
        },
        {
            "name": "threadId",
            "description": "String. Optional. For the assistant to have history and memory of a conversation, a user must include the threadId. If no thread id is provided then a new one will be created and will be provided for future use in the response body."
        },
        {
            "name": "messages",
            "description": "Array of objects. Required. New conversation messages. Each object includes 'role' (user/system/assistant), 'content' as a string, and 'dataSourceIds' a list of strings. These messages should only include the new messages if providing a threadId since the thread already has knowledge of previous messages."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "downloadCodeInterpreterFiles",
    "url": "/assistant/files/download/codeinterpreter",
    "method": "POST",
    "name": "downloadCodeInterpreterFiles",
    "description": """Download files generated by the Code Interpreter assistant via pre-signed URLs.

    Example request:
    {
        "data": {
            "key": "yourEmail@vanderbilt.edu/msg_P0lpFUEY_pie_chart.png"
        }
    }

    Example response:
    {
        "success": true,
        "downloadUrl": "<Download URL>"
    }
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "key",
            "description": "String. Required. Unique key identifying the file to download. Example: 'yourEmail@vanderbilt.edu/msg_P0lpFUEY_pie_chart.png'. These may be generated in the /assistant/chat/codeinterpreter endpoint responses."
        },
        {
            "name": "fileName",
            "description": "String. Optional. If specified, directly downloads the file with this name."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "deleteCodeInterpreterThread",
    "url": "/assistant/openai/thread/delete",
    "method": "DELETE",
    "name": "deleteCodeInterpreterThread",
    "description": """Delete a specific Code Interpreter conversation thread, removing all associated messages.

    Example request (via query parameter):
    DELETE /assistant/openai/thread/delete?threadId=yourEmail@vanderbilt.edu/thr/8923047385920349782093
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "threadId",
            "description": "String. Required. Unique identifier of the thread to delete. Example: 'yourEmail@vanderbilt.edu/thr/8923047385920349782093'."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "deleteCodeInterpreterAssistant",
    "url": "/assistant/openai/delete",
    "method": "DELETE",
    "name": "deleteCodeInterpreterAssistant",
    "description": """Delete a Code Interpreter assistant instance, permanently removing it from the platform.

    Example request (via query parameter):
    DELETE /assistant/openai/delete?assistantId=yourEmail@vanderbilt.edu/ast/38940562397049823
    """,
    "type": "apiDocumentation",
    "params": [
        {
            "name": "assistantId",
            "description": "String. Required. Unique identifier of the assistant to delete. Example: 'yourEmail@vanderbilt.edu/ast/38940562397049823'."
        }
    ],
    "tags": ["apiDocumentation"]
},

{
    "id": "getUserAccounts",
    "url": "/state/accounts/get",
    "method": "GET",
    "name": "getUserAccounts",
    "description": "Get a list of the user's accounts that costs are charged to.",
    "type": "apiKeysAst",
    "params": [],
    "tags": ["apiKeysAst"]
},

{
    "id": "getApiKeysForAst",
    "url": "/apiKeys/get_keys_ast",
    "method": "GET",
    "name": "getApiKeysForAst",
    "description": "Get user's amplify API keys filtered for assistant use.",
    "type": "apiKeysAst",
    "params": [],
    "tags": ["apiKeysAst"]
}


]


