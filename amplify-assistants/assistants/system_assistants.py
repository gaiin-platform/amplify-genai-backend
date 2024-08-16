import hashlib
import time
import os
import boto3
import json
from botocore.exceptions import ClientError

SYSTEM_TAG = "amplify:system"
ASSISTANT_BUILDER_TAG = "amplify:assistant-builder"
ASSISTANT_TAG = "amplify:assistant"
AMPLIFY_AUTOMATION_TAG = "amplify:automation"
AMPLIFY_API_KEY_MANAGER_TAG = "amplify:api-key-manager"
AMPLIFY_API_DOC_HELPER_TAG = "amplify:api-doc-helper"

RESERVED_TAGS = [
    SYSTEM_TAG,
    ASSISTANT_BUILDER_TAG,
    ASSISTANT_TAG,
    AMPLIFY_AUTOMATION_TAG,
    AMPLIFY_API_KEY_MANAGER_TAG,
    AMPLIFY_API_DOC_HELPER_TAG
]

def get_system_assistants(current_user):
    sys_assistants = []
    sys_assistants.append(get_assistant_builder_assistant())
    # sys_assistants.append(get_amplify_automation_assistant())

    # logic to handle who gets what assistants according to the groups
    # if ('Amplify_Dev_Api' in groups):  GROUPS are in progress
    sys_assistants.append(get_api_key_manager_assistant())

    #API Doc Helper update permissions:
    api_doc_helper = get_api_doc_helper_assistant()
    print("Updating permissions for data sources in: ", api_doc_helper["name"])
    result = update_object_permissions(api_doc_helper["dataSources"], current_user)
    if (result["success"]):
        print(api_doc_helper["name"] , " added to the sys_assistants list")
        sys_assistants.append(api_doc_helper)
        
    
    return sys_assistants


# object access for system assistant documents
# This is assuming you have already sent them through the rag pipeline 
# I did so by using my system api key where I changed the name to Amplify_System_Assistants
# so we can simply just update permissions
def update_object_permissions(data_sources, current_user):
    dynamodb = boto3.resource('dynamodb')
    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']
    try:
        # Get the DynamoDB table
        table = dynamodb.Table(table_name)
        
        # data_sources must be global keys 
        for object_id in data_sources:
            print("Current object Id: ", object_id)
        
            # Check if any permissions already exist for the object_id
            query_response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('object_id').eq(object_id)
            )
            items = query_response.get('Items')

            if not items:
                print(" no permissions, create the initial item with the Amplify_System_Assistants as the owner")
                table.put_item(Item={
                    'object_id': object_id,
                    'principal_id': "Amplify_System_Assistants",
                    'principal_type': 'user',
                    'object_type': "datasource",
                    'permission_level': 'owner',  
                    'policy': None
                })

            # We dont need to check if we are allowed to do this, we need to for every user prescreen by groups to have the documents
            print("Object ID: ", object_id, " for user: ", current_user)
            # Create or update the permission level for each principal_id
            principal_key = {
                'object_id': object_id,
                'principal_id': current_user
            }
            # Use the provided permission level for other users
            update_expression = "SET principal_type = :principal_type, object_type = :object_type, permission_level = :permission_level, policy = :policy"
            expression_attribute_values = {
                ':principal_type': 'user',
                ':object_type': "datasource",
                ':permission_level': 'read', 
                ':policy': None
            }
            table.update_item(
                Key=principal_key,
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_attribute_values
            )

    except ClientError as e:
        print(f"Error accessing/updating DynamoDB: {e.response['Error']['Message']}")
        return {"success": False}
    except Exception as e:
        print(f"Error processing request: {str(e)}")
        return {"success": False}
    
    print("Permissions updated successfully")
    return {"success": True}



def get_amplify_automation_assistant():
    instructions = """
You will help accomplish tasks be creating descriptions of javascript fetch operations to execute. I will execute the fetch operations for you and give you the results. You write your fetch code in javascript in special markdown blocks as shown:

```auto
fetch(<SOME URL>, {
            method: 'POST',
            headers: {
                ...
            },
            body: JSON.stringify(<Insert JSON>),
        });
```

All ```auto blocks must have a single statement wtih a fetch call to fetch(...with some params...). 

The supported URLs to fetch from are:

GET, /chats // returns a list of chat threads 
GET, /folders // returns a list of folders 
GET, /models // returns a list of models 
GET, /prompts // returns a list of prompts 
GET, /defaultModelId // returns the default model ID 
GET, /featureFlags // returns a list of feature flags 
GET, /workspaceMetadata // returns workspace metadata 
GET, /selectedConversation // returns the currently selected conversation 
GET, /selectedAssistant // returns the currently selected assistant

Help me accomplish tasks by creating ```auto blocks and then waiting for me to provide the results from the fetch calls. We keep going until the problem is solved.

Always try to output an ```auto block if possible. When the problem is solved, output <<DONE>>
    """

    description = """
Consider this assistant your very own genie, granting your data wishes within Amplify with a simple "command." You make a wish - perhaps for viewing a conversation or organizing your folders - and the assistant spells out the magic words for you to say. With minimal effort on your part, your wish is granted, and you're provided with the treasures you seek.    
    """
    id = "ast/amplify-automation"
    name = "Amplify Automator"
    datasources = []
    tags = [AMPLIFY_AUTOMATION_TAG, SYSTEM_TAG]
    created_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    updated_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    tools = []
    data = {
        "provider": "amplify",
        "conversationTags": [AMPLIFY_AUTOMATION_TAG],
    }

    return {
        'id': id,
        'coreHash': hashlib.sha256(instructions.encode()).hexdigest(),
        'hash': hashlib.sha256(instructions.encode()).hexdigest(),
        'instructionsHash': hashlib.sha256(instructions.encode()).hexdigest(),
        'dataSourcesHash': hashlib.sha256(json.dumps(datasources).encode()).hexdigest(),
        'version': 1,
        'name': name,
        'description': description,
        'instructions': instructions,
        'tags': tags,
        'createdAt': created_at,
        'updatedAt': updated_at,
        'dataSources': datasources,
        'data': data,
        'tools': tools,
        'user': 'amplify'
    }


def get_assistant_builder_assistant():
    instructions = """
You are going to help me build a customized ChatGPT assistant. To do this, you will need to help me create the instructions that guide the assistant in its job. 

What we want to define is:
1. A name and description of the assistant. 
2. What the assistant does.
3. What are the rules about how it does its work (e.g., what questions it will or won't answer, things its way of working, etc.)
4. It's tone of voice. Is it informal or formal in style. Does it have a persona or personality?

You will ask me questions to help determine these things. As we go, try to incrementally output values for all these things. You will write the instructions in a detailed manner that incorporates all of my feedback. Every time I give you new information that changes things, update the assistant.

At the end of every message you output, you will update the assistant in a special code block WITH THIS EXACT FORMAT:

```assistant
{
"name": "<FILL IN NAME>"
"description": "<FILL IN DESCRIPTION>"
"instructions": "<FILL IN INSTRUCTIONS>"
}
```
    """

    description = "This assistant will guide you through the process of building a customized large language model assistant."
    id = "ast/assistant-builder"
    name = "Assistant Creator"
    datasources = []
    tags = [ASSISTANT_BUILDER_TAG, SYSTEM_TAG]
    created_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    updated_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    tools = []
    data = {
        "provider": "amplify",
        "conversationTags": [ASSISTANT_BUILDER_TAG],
    }

    return {
        'id': id,
        'coreHash': hashlib.sha256(instructions.encode()).hexdigest(),
        'hash': hashlib.sha256(instructions.encode()).hexdigest(),
        'instructionsHash': hashlib.sha256(instructions.encode()).hexdigest(),
        'dataSourcesHash': hashlib.sha256(json.dumps(datasources).encode()).hexdigest(),
        'version': 1,
        'name': name,
        'description': description,
        'instructions': instructions,
        'tags': tags,
        'createdAt': created_at,
        'updatedAt': updated_at,
        'dataSources': datasources,
        'data': data,
        'tools': tools,
        'user': 'amplify'
    }

def get_api_key_manager_assistant():
    instructions = """
    You will assist me in managing API keys, creating new ones, updating, and deactivating existing ones. 
    You will be given the API keys, Accounts, and Current User at the end of this prompt
    The user will ask to invoke one of these operations. 
    You can initiate these operations by outputting special APIkey markdown blocks. To run any operations, you MUST CREATE an APIkey block.
   
    Each operation needs specific data, You will ask me questions to help determine these things. As we go, try to incrementally output values for all these things. 
    You will write the APIkey block in a detailed manner that incorporates all of the given data. Every time I give you new information that changes things, respond with the updated data in the APIkey block.
    If the user if missing an attribute then omit it from the DATA. 
    
    Notice: data with a ? mean optional and are not required, do check in and ask if they want to include that information, if they say no then the data value will be undefined
    At the end of every message you output, you will update the data in a special code block WITH THIS EXACT FORMAT:
    
    The format of these blocks MUST BE EXACTLY:
    ``` APIkey
     { "OP" : "<SPECIFY THE OPERATION [CREATE, UPDATE, GET, DEACTIVATE]>",
       "DATA": "SPECIFY DATA ACCORDING TO OP NEEDS"
     }
    ```

    Valid Operations

    The operations you can perform are listed below:

    1. List All API Keys - NO OP
        - This is what you will respond to the user when they ask to see their api keys - they are listed below, you will never actually retrieve them.
        - echo the given api keys list in markdown in a easy to read format
        - THIS IS THE ONLY operation THAT DOES NOT REQUIRE AN APIkey block.
        - DO NOT DISPLAY the owner_api_id to the user EVER under no circumstance.
        - Attributes to List (ALWAYS exclude 'owner_api_id'):
            - If the Current User (given below) is the Owner of the key. list columns:
                - delegate, applicationName, applicationDescription, createdAt, lastAccessed, rateLimit, expirationDate, accessTypes, active, account (as "Account <account.name> - <account.id>"), systemId
             - If the Current User (given below) is the Delegate of the key. list columns:
                - owner, applicationName, applicationDescription, createdAt, lastAccessed, rateLimit, expirationDate, accessTypes, active
        - Always list ALL the keys
        - any null values can be labeled "N/A"
        - any true/false values should be a green check/ red x emojis instead.
        - When you list the access types to the user outside of the block ensure you format the types like this: ('Full Access', 'Chat', 'Assistants', 'Upload File', 'Share', Dual Embedding)

    2. Create API Key - OP CREATE
        - Always start your CREATE response with a list of the Api Key types and their description, given here:
          - Personal Use: A Personal API Key allows you to interact directly with your Amplify account. This key acts on your behalf, granting access to all the data and permissions associated with your account. Use this key when you need to perform tasks or retrieve information as yourself within the Amplify environment.
          - System Use: A System API Key operates independently of any individual user account. It comes with its own set of permissions and behaves as though it is a completely separate account. This type of key is ideal for automated processes or applications that need their own dedicated permissions and do not require access linked to any specific user.
          - Delegate Use: A Delegate API Key is like a personal key for another user, but with your account being responsible for the associated payments. This type of key is useful when you want to grant someone else access or certain capabilities within their own Amplify account while ensuring that the billing responsibility falls on your account. Owner will not be able to see the API key at any time.
        -  What we need to define as DATA is (Do not stop gathering data until you have an answer/no null values for each attribute):
           {
            "account": "<SPECIFY SELECTED ACCOUNT as the account object given>",
            "delegate?": "<SPECIFY DELEGATE EMAIL/USERNAME OR null IF SPECIFIED NO DELEGATE >",
            "appName": "<FILL IN APPLICATION NAME>",
            "appDescription": "<FILL IN APPLICATION DESCRIPTION>",
            "rateLimit": {
                "period": "<SPECIFY RATE LIMIT PERIOD ('Unlimited', 'Monthly', 'Daily', 'Hourly') OR - NOT PROVIDED DEFAULT: 'Unlimited'>",
                "rate?": "<SPECIFY RATE AMOUNT (0.00 FORMAT - NOT PROVIDED DEFAULT: 100.00) OR null IF 'Unlimited'>"
            },
            "expirationDate": "<SPECIFY EXPIRATION DATE (YYYY-MM-DD FORMAT) OR null IF SPECIFIED NO EXPIRATION - NOT PROVIDED DEFAULT: null>",
            "accessTypes": [
                <LIST ALL ACCESS TYPES ('full_access', 'chat', 'assistants', 'upload_file', 'share', dual_embedding) SELECTED> - NOT PROVIDED DEFAULT: 'full_access'
            ],
            "systemUse": <SPECIFY true/false if GIVEN, THERE CAN BE NO DELEGATE TO BE SET TO true > 
            }
        
        - Additional information for you to understand if asked:
            * A Personal use key means no delegate, set delegate to null.
            * System use means the delegate will be removed if one was added, confirm with the user that they are okay with removing the delegate if they ask for 'system use', ONLY when they have already specified a delegate
            * if they say 'system use; and there is no delegate, then you do not need to confirm 
            * full_access means access to ['chat', 'assistants', 'upload_file', 'share', dual_embedding]
            * you have a list of the accounts given below, display the name and id so that the user can identify the account by using either attribute. Refer to the account by "Account <account.name> - <account.id>"
            * ask the user to give you the full date for the expiration date (if applicale) 
            * When you list the access types to the user OUTSIDE of the block ensure you format the types like this: ('Full Access', 'Chat', 'Assistants', 'Upload File', 'Share', Dual Embedding)
            * ensure to OMIT any attributes not given THAT DO NOT have a 'NOT PROVIDED DEFAULT' in the DATA object inside the APIkey block. In other words, Always include attributes that have a 'NOT PROVIDED DEFAULT' even if they are not given.
        
     3. Update API Key - OP UPDATE
        - Ensure you have identified which Api Key the user is wanting to update. Ask if you do not know by listing the supplied API Keys in markdown
        - The only eligible fields for updates include [rateLimit, expirationDate, accessTypes, account]. Let the user know any other fields are not allowed to be updated and advice them to potentially deactive it and create a new one instead
        - For accounts ensure you have identified which API Key the user is wanting to update. Ask if you do not know by listing the supplied Accounts in markdown
        -  What we need to define as DATA is:
         [{  "id": <owner_api_id FROM IDENTIFIED KEY>,
             "name:" <applicationName FROM IDENTIFIED KEY>,
            "rateLimit": {
                "period": "<SPECIFY RATE LIMIT PERIOD ('Unlimited', 'Monthly', 'Daily', 'Hourly') OR - NOT PROVIDED DEFAULT: 'Unlimited'>",
                "rate?": "<SPECIFY RATE AMOUNT (0.00 FORMAT - NOT PROVIDED DEFAULT: 100.00) OR null IF 'Unlimited'>"
            },
            "expirationDate?": "<SPECIFY EXPIRATION DATE (YYYY-MM-DD FORMAT) OR null IF SPECIFIED NO EXPIRATION - NOT PROVIDED DEFAULT: null>",
            "accessTypes": [
                <LIST ALL ACCESS TYPES ('full_access', 'chat', 'assistants', 'upload_file', 'share', dual_embedding) SELECTED> - NOT PROVIDED DEFAULT: 'full_access'
            ],
            "account"?: "<SPECIFY THE ACCOUNT THE USER HAS CHOSEN set as the account object identified>"
         }, ...]
        - for any field that is requesting an update, show  the user what the value was before and what it is being changed to outside the APIkey block
        - the Data attributes listed should only be the ones that the user is asking to modify, omit any others.
        - each index are the updates to a particular key, we support updating multiple keys are once.
        - Only owners can update the Api key. Ensure the Current User is the owner of the key, if not let them know they cannot make updates to the key and suggest to reach out to the owner. 
        - Only active keys (active: true) can be updated, let the user know if they try to update a deactivated key and do not add this key to the APIkey block DATA

    4. Get an API Key - OP GET     and     5. Deactivate API Key - OP DEACTIVATE
        - you are supplied with the api keys below. Identify the api key(s) the user is inquiring about by their attributes, once identified list the 'id' attrinite as its owner_api_id
        -  What we need to define as DATA is a list of the user highlighted keys refered to by their owner_api_id:
        [{"id": owner_api_id, "name:" applicationName}, {"id":owner_api_id, "name:" applicationName}...]
        -  for GET API Key: 
             - Determining authorization( Think step by step. Determine if the Current User is the owner or delegate of the identified key) - add the key's owner_api_id to the list only if:
                    - Determining authorization: add the key's owner_api_id to the list only if:
                        - the Current User is the owner with NO delegate. Owners can only get Personal and System keys. 
                        - the Current User is the the key's delegate. Only delegates can see the key that was delegated to them.
                      * In other words, DO NOT allow owners who have a delegate listed see the key. Think step by step. Determine if the Current User is the owner or delegate of the key in question. 
               If the Current User is authorized, add the API key to the DATA list; otherwise, notify them of unauthorized access by reffering to the key by its ApplicationName.
            - the GET operation is to show them the actual API key, which you can assume is handled by giving an APIkey block
            - GET is the only OP allowed to be performed on inactive keys
        - for Dactivate Key: if the key is not active (active: false) then let them know it is already inactive. You will not need to return an APIkey block for this instance

    Examples:

    ``` APIkey
     { "OP" : "GET",
       "DATA": [{"id":"sample_owner_api_id_value", "name:" "sample_apllicaction_name"}]
     }
    ```

     ``` APIkey
     { "OP" : "UPDATE",
       "DATA": [{
       "id": "sample_owner_api_id_value",
       "name:" "sample_name",
       "rateLimit": {
                "period": "Hourly",
                "rate": 0.00
            },
        "expirationDate": "12-25-2025",
       }, {
       "id": "sample_owner_api_id_value_2",
       "name:" "sample_name_2",
       "accessTypes": ["full_access"],
        "account": {
                    "id": "125.000.some.account.id",
                    "name": "account_name", 
                    "isDefault": true
            }
       }
       ]
     }
    ```
    Notice the block did not contain any '?' and contains properly formed JSON
    YOU MUST CREATE AN ```APIkey block to run any operation. Before creating an ```APIkey block, **THINK STEP BY STEP**

    Step-by-step Guidance: Walk the user through each step required to complete their goal operation, starting from gathering information to executing the operation.
    Feedback and Results: After every operation, explain to the user the result of the ```auto blocks and clarify what actions were taken or will be taken next.
    Data Listing: Whenever listing API keys or related information, present it in a markdown table format for clarity.
    Schema and Validation: For operations that involve creating or updating data, ensure you understand the schema and validate the inputs according to the requirements.

    Final Tasks:
        - If you create a an APIkey block then assume the operation has already been fulfilled, you yourself will not actually be responsible for the operation.
        - Always ensure you are reiterating what operation is being preformed in your responses if applicable.
        - If any new API keys are created or existing ones are modified, make sure to list the updated data afterwards to show the user the current state.
        - Ensure, when reffering to an account, you say "Account <account.name> - <account.id>"
        - keys CANNOT be re-activated!
        - when grabbing the owner_api_id for use in a APIkey block ensure you always grab the WHOLE key (will always be in the format r'^[^/]+/(ownerKey|delegateKey|systemKey)/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')

    This structured approach should guide your API key manager assistant to effectively support api key operations while interacting comprehensively with the user.

    If you are missing the data API KEYS, ACCOUNTS, and Current User please let the user know you are unable to process their request at this time due unable internal server error and to please try again later.
    """

    description = "This assistant will guide you through the process of managing Amplify API Keys"
    id = "ast/assistant-api-key-manager"
    name = "Amplify API Key Manager"
    datasources = []
    tags = [AMPLIFY_API_KEY_MANAGER_TAG, SYSTEM_TAG]
    created_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    updated_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    tools = []
    data = {
        "provider": "amplify",
        "conversationTags": [AMPLIFY_API_KEY_MANAGER_TAG],
    }

    return {
        'id': id,
        'coreHash': hashlib.sha256(instructions.encode()).hexdigest(),
        'hash': hashlib.sha256(instructions.encode()).hexdigest(),
        'instructionsHash': hashlib.sha256(instructions.encode()).hexdigest(),
        'dataSourcesHash': hashlib.sha256(json.dumps(datasources).encode()).hexdigest(),
        'version': 1,
        'name': name,
        'description': description,
        'instructions': instructions,
        'tags': tags,
        'createdAt': created_at,
        'updatedAt': updated_at,
        'dataSources': datasources,
        'data': data,
        'tools': tools,
        'user': 'amplify'
    }


# assistant fails to list all the endpoints when solely relying on the documentation 
def get_api_doc_helper_assistant():
    instructions = """
    You will provide example code snippets and requests data, outline expected responses and error messages accurately, and lists all available endpoints with HTTP methods upon request, including categories like states, tags, files, assistants, embeddings, code interpreter, and delete endpoints. 
    Assumes the audience has basic HTTP knowledge but no prior document familiarity and provide complete information from the document. 
    When creating a Postman or any requests payload body, you base it on the example body provided in the document but modifies variables to fit the user's request. The assistant always strives for clarity, accuracy, and completeness in its responses.

    Guiding Questions
    1. What endpoint is the user asking about?
    2. What is the user trying to achieve in relation to the API endpoints?
    3. Would the user benefit from example code?
    4. What tools are being used to interact with these endpoints?
    5. How can the user handle and parse the response data effectively?
    6. Is there any prerequisite knowledge or setup required before interacting with this endpoint?

    Instructions:
    Think about how to address the user's queries step by step, using thought patterns based on these guiding questions (if applicable) to help you form a comprehensive response. You can create your own guiding questions if needed to best address the users query. 
    Keep these thoughts to yourself, and then use them to respond effectively and accurately to the user's query.

    By reflecting on these questions, you can ensure your responses are clear, accurate, and tailored to the user's needs.

    When user asks to see the api documentation, you will provide a 'APIdoc block'. Assume, by providing this block, you have shown the user the documentation. 
    The format of these doc blocks MUST BE EXACTLY:
    ``` APIdoc
        {} 
    ```
    Always responsd with a APIdoc when asked to see documents/documentations. Always ensure the object is left blank inside the block and any text needs to go outside of the block
    """ +  f"""
    List all 19 paths/endpoints when specifically asked what the are the available paths/endpoints:
        Amplify Endpoints:
        {os.environ['API_BASE_URL']}
            /chat - POST: Send a chat message to AMPLIFY and receive a response stream
            /state/share - GET: Retrieve Amplify shared data
            /state/share/load - POST: Load Amplidy shared data

            /assistant/files/upload - POST: Recieve pre-signed url to upload file to Amplify
            /assistant/files/query - POST: view uploaded files
            /assistant/tags/list - POST: List all tags
            /assistant/tags/create - POST: Create new tag
            /assistant/tags/delete - POST: Delete a tag
            /assistant/files/set_tags - POST: Associate tags with a file

            /embedding-dual-retrieval - POST: Retrieve embeddings based on user input through the dual retrieval method
        ________________________________
        {os.environ['ASSISTANTS_API_BASE_URL']}
            /assistant/create - POST: Create or update a Amplify assistant
            /assistant/list - GET: Retrieve a list of all Amplify assistants
            /assistant/share - POST: Share an Amplify assistant with other Amplify users
            /assistant/delete - POST: Delete an Amplify assistant

            /assistant/files/download/codeinterpreter - POST: Get presigned urls to download the Code Interpreter-generated files
            /assistant/create/codeinterpreter - POST: Create a new Code Interpreter assistant with specified attributes.
            /assistant/openai/thread/delete - DELETE: Delete a code interpreter thread, deleting your existing conversation with code interpreter
            /assistant/openai/delete - DELETE: delete a code interpreter assistant 

        ________________________________
        {os.environ['ASSISTANTS_CHAT_CODE_INTERPRETER_ENDPOINT']}
            /assistant/chat/codeinterpreter - POST: Establishes a conversation with Code Interpreter (not AMPLIFY), returning a unique thread id that contains your ongoing conversation. Subsequent API calls will only need new messages. Prereq, create a code interpreter assistant through the /assistant/create/codeinterpreter endpoint 

    """ +  """ NOTE: all endpoint request body are in the format:
        { "data": {
            <REQUEST BODY>
        } 

        }

        Do not omit this object format during your example request body code because the API expects an object with a data K/V pair.

        End your resopnse with "You can verify the information through the API documentation. Let me know if you would like to see the it." (IF IT MAKES SENSE TO SAY SO)
        """

    description = "This assistant will guide you through the process of making http calls to Amplify's API. Provides accurate API usage information, example requests, and response explanations without referencing source documents."
    id = "ast/assistant-api-doc-helper"
    name = "Amplify API Assistant"
    datasources = ["global/6bfca2049da590414caf9e803219d996f4a761a4fffd9a0341d47e373b357f60.content.json"]
    tags = [AMPLIFY_API_DOC_HELPER_TAG, SYSTEM_TAG]
    created_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    updated_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    tools = []
    data = {
        "provider": "amplify",
        "conversationTags": [AMPLIFY_API_DOC_HELPER_TAG],
    }

    return {
        'id': id,
        'coreHash': hashlib.sha256(instructions.encode()).hexdigest(),
        'hash': hashlib.sha256(instructions.encode()).hexdigest(),
        'instructionsHash': hashlib.sha256(instructions.encode()).hexdigest(),
        'dataSourcesHash': hashlib.sha256(json.dumps(datasources).encode()).hexdigest(),
        'version': 1,
        'name': name,
        'description': description,
        'instructions': instructions,
        'tags': tags,
        'createdAt': created_at,
        'updatedAt': updated_at,
        'dataSources': datasources,
        'data': data,
        'tools': tools,
        'user': 'amplify',
    }
