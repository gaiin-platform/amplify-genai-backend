import json
import os
import uuid
from datetime import datetime

import boto3
import concurrent.futures

from boto3.dynamodb.types import TypeSerializer
from llm.chat import chat_simple

from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata import permissions

setup_validated(rules, permissions.get_permission_checker)
from pycommon.api.ops import api_tool, set_route_data, set_permissions_by_state
from service.routes import route_data

set_route_data(route_data)
set_permissions_by_state(permissions)


serializer = TypeSerializer()
s3_client = boto3.client("s3")
dynamodb_client = boto3.client("dynamodb")


def get_description_and_schema(access_token, model, code):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        # Define the tasks to be run in parallel
        future_to_task = {
            executor.submit(
                chat_simple,
                access_token,
                model,
                f"Please describe what the following code does and provide a sample of the data it returns:\n\n{code}",
                "Carefully describe what this code does in 1-2 sentences and include a sample of the data it returns.",
            ): "description",
            executor.submit(
                chat_simple,
                access_token,
                model,
                f"Please describe the schema for the context used by this code:\n\n{code}",
                'Carefully create a json schema for context object based on what the code accesses within it. If the context value is open-ended, then just create an object with "additionalProperties": true and no keys defined. Output the schema in EXACTLY ONE ```json block.',
            ): "schema",
            executor.submit(
                chat_simple,
                access_token,
                model,
                f"Please describe the top-level parameters:\n\n{code}",
                """
            Describe the parameters in this format:
            - <parameter_name>: <description>
            
            Example:
            - x: The first number to add
            - y: The second number to add
            - operation: The operation to perform (addition, subtraction, multiplication, division)
            - person: A json object with the person's name and age (e.g. {"name": "Alice", "age": 30}) with lines escaped with "\\n"

            Always include the "with lines escaped with "\\n"" part for json objects.
            """,
            ): "agent_desc",
            executor.submit(
                chat_simple,
                access_token,
                model,
                f"Please describe the schema for the return value(s) of this script:\n\n{code}",
                'Carefully create a json schema for the return value from the code. If the return value is open-ended, then just create an object with "additionalProperties": true and no keys defined. Output the schema in EXACTLY ONE ```json block.',
            ): "return_schema",
        }

        results = {}
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            try:
                result = future.result()
                results[task] = result
            except Exception as e:
                print(f"{task} generated an exception: {e}")

    description = results["description"]
    schema = results["schema"]
    agent_desc = results["agent_desc"]
    return_schema = results["return_schema"]

    schema_dict = json.loads(schema.split("```json")[1].split("```")[0].strip())
    return_schema_dict = json.loads(
        return_schema.split("```json")[1].split("```")[0].strip()
    )

    return description, schema_dict, agent_desc, return_schema_dict


"""
createPython:

Writes a python script that can be dynamically executed to perform the requested function. Takes a list of available python libraries and versions to use. Takes an optional notes parameter with notes to give to openai when producing the code. The last line of the generated code must be the return value with whatever was requested for the code to return. Generates a json schema describing the parameters that the script expects. Uses this openAI api to generate the code but with different messages: from openai import OpenAI
client = OpenAI()
requestedCodeDescription = ....
response = client.chat.completions.create(
  model="gpt-4o-mini",
  messages=[
    {"role": "system", "content": "You are an expert Python developer. You write python scripts that can be dynamically executed that return the requested data s the last statement in the script."},
    {"role": "user", "content": f"Please generate a python script that can be dynamically executed to: {requestedCodeDescription}"},
  ]
)   code = completion.choices[0].message.content
"""


@api_tool(
    path="/code/create",
    tags=["code"],
    name="createCode",
    description="Dynamically generate a Python script to perform a specified function.",
    parameters={
        "type": "object",
        "properties": {
            "requested_function": {
                "type": "string",
                "description": "Description of the requested function that the Python script should perform.",
            },
            "libraries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "version": {"type": "string"},
                    },
                    "required": ["name", "version"],
                },
                "description": "A list of available Python libraries and their versions to use (e.g. [{'name': 'numpy', 'version': '1.21.2'}]).",
            },
            "notes": {
                "type": "string",
                "description": "Optional notes to provide additional context for the code generation.",
            },
        },
        "required": ["requested_function", "libraries"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the code creation was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "The complete generated Python script",
                    },
                    "description": {
                        "type": "string",
                        "description": "AI-generated description of what the code does",
                    },
                    "schema": {
                        "type": "object",
                        "description": "JSON schema for the context parameters the script expects",
                    },
                    "return_schema": {
                        "type": "object",
                        "description": "JSON schema for the return value of the script",
                    },
                    "agent_desc": {
                        "type": "string",
                        "description": "Agent-friendly description of the parameters",
                    },
                    "uuid": {
                        "type": "string",
                        "description": "Unique identifier for the created script",
                    },
                },
                "required": [
                    "script",
                    "description",
                    "schema",
                    "return_schema",
                    "agent_desc",
                    "uuid",
                ],
            },
            "message": {
                "type": "string",
                "description": "Error message if success is false",
            },
        },
        "required": ["success"],
    },
)
@validated(op="create")
def create_code(event, context, current_user, name, data):
    try:
        # Extract parameters from the request payload
        requested_function = data["data"].get("requested_function", "")
        libraries = data["data"].get("libraries", [])
        notes = data["data"].get("notes", "")
        access_token = data["access_token"]

        # Construct the libraries declaration for the Python script
        libraries_code = "\n".join(
            [f"import {lib['name']} as {lib['name']}" for lib in libraries]
        )

        model = "gpt-4o"
        requested_code_description = f"{requested_function}. Libraries: {', '.join([lib['name'] for lib in libraries])}. Notes: {notes}"

        system_instructions = """
        You are an expert Python developer. You write python scripts that can be dynamically executed to
        perform the requested function.

        The script you write will be invoke by this code:
        # Prepare the execution environment
        exec_globals = {'context': context_dict}
        exec_locals = {}
        exec(the_script_you_write_as_a_string, exec_globals, exec_locals)
        result = exec_locals.get('result', exec_globals.get('result'))
        print(f"Execution result: {result}")
        
        ALL PARAMETERS PASSED TO THE SCRIPT WILL BE AVAILABLE IN THE context VARIABLE, which is a
        dictionary. Read the context variable to get the parameters passed to the script.

        Make sure that you account for this so that the last line of the script is the expected output
        otherwise exec_locals.get('result', exec_globals.get('result')) will fail.
        DO NOT INCLUDE a RETURN. THE RESULT MUST BE THE LAST LINE OF THE SCRIPT.
        
        Example:
        ```python
        # A bunch of code...
        # more code..
        # more code...
        result = 2 * 4 + 3 + z
        result
        """

        prompt_instructions = f"""
        Please generate a Python script that can be dynamically executed to:
        -----------
        {requested_code_description}
        ---------
        DO NOT HAVE A RETURN STATEMENT AT THE END! The last line of the script should be the result of the function
        as a variable named 'result' like this:
        ```python
        # A bunch of code...
        
        result = some_function()
        result
        ```
        
        You must output EXACTLY ONE ```python code block with the script inside it.
        """

        raw_code = chat_simple(
            access_token, model, prompt_instructions, system_instructions
        )

        print(f"Raw code: {raw_code}")

        # Parse out the code between ```python and ``` in the response
        code = raw_code.split("```python")[1].split("```")[0].strip()

        description, schema_dict, agent_desc, return_schema_dict = (
            get_description_and_schema(access_token, model, code)
        )

        # Construct the complete script
        final_script = f"{libraries_code}\n\n{code}\n\n"

        # Create a uuid string
        uuid_str = f"code/{uuid.uuid4()}"

        # Store the code in S3
        today = datetime.now().strftime("%m-%d-%Y")
        s3_bucket = os.getenv("DYNAMIC_CODE_BUCKET")
        s3_path = f"{s3_bucket}/{current_user}/{today}/{uuid_str}.py"
        s3_client.put_object(Bucket=s3_bucket, Key=s3_path, Body=final_script)

        # Store the data in DynamoDB
        dynamo_table = os.getenv("DYNAMO_DYNAMIC_CODE_TABLE")
        item = {
            "creator": {"S": current_user},
            "created_at": {"S": datetime.utcnow().isoformat()},
            "last_updated": {"S": datetime.utcnow().isoformat()},
            "uuid": {"S": uuid_str},
            "s3Key": {"S": s3_path},
            "description": {"S": description},
            "schema": serializer.serialize(schema_dict),
            "return_schema": serializer.serialize(return_schema_dict),
            "agent_desc": serializer.serialize(agent_desc),
        }

        dynamodb_client.put_item(TableName=dynamo_table, Item=item)

        return {
            "success": True,
            "data": {
                "script": final_script,
                "description": description,
                "schema": schema_dict,
                "return_schema": return_schema_dict,
                "agent_desc": agent_desc,
                "uuid": uuid_str,
            },
        }

    except Exception as e:
        print(f"Error in create_code: {str(e)}")
        return {
            "success": False,
            "message": f"Failed to create the Python script: {str(e)}",
        }


@api_tool(
    path="/code/invoke",
    tags=["code"],
    name="invokeCode",
    description="Invoke a dynamically generated Python script with the specified context.",
    parameters={
        "type": "object",
        "properties": {
            "uuid": {
                "type": "string",
                "description": "The unique identifier of the Python script to invoke.",
            },
            "context": {
                "type": "object",
                "description": "The context dictionary to provide to the Python script for execution.",
                "additionalProperties": True,
            },
        },
        "required": ["uuid", "context"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the code execution was successful",
            },
            "result": {
                "description": "The result returned by the executed Python script (can be any type)"
            },
            "error": {
                "type": "string",
                "description": "Error message if execution failed",
            },
            "message": {
                "type": "string",
                "description": "Error message if success is false",
            },
        },
        "required": ["success"],
    },
)
@validated(op="invoke")
def invoke_code(event, context, current_user, name, data):
    try:
        # Extract the uuid and context from the request
        uuid_str = data["data"].get("uuid")
        context_dict = data["data"].get("context")

        # Log the received parameters
        print(f"Received uuid: {uuid_str}")
        print(f"Received context: {context_dict}")

        # Validate the inputs
        if not uuid_str or not context_dict:
            print("Invalid input: Missing uuid or context.")
            return {"success": False, "message": "uuid and context are required."}

        # Lookup the dynamo entry
        dynamo_table = os.getenv("DYNAMO_DYNAMIC_CODE_TABLE")
        response = dynamodb_client.get_item(
            TableName=dynamo_table, Key={"uuid": {"S": uuid_str}}
        )

        item = response.get("Item")
        if not item:
            print(f"Error: No entry found for uuid: {uuid_str}")
            return {"success": False, "message": f"No entry found for uuid: {uuid_str}"}

        # Confirm the current_user is the creator
        creator = item.get("creator", {}).get("S")
        if creator != current_user:
            print(
                f"Error: current_user {current_user} is not the creator of uuid: {uuid_str}"
            )
            return {
                "success": False,
                "message": "You do not have permission to execute this code.",
            }

        # Get the s3_key from the dynamo entry
        s3_key = item.get("s3Key", {}).get("S")
        if not s3_key:
            print(f"Error: No S3 key found for uuid: {uuid_str}")
            return {
                "success": False,
                "message": f"No S3 key found for uuid: {uuid_str}",
            }

        # Download the Python code from S3
        bucket_name = os.getenv("DYNAMIC_CODE_BUCKET")
        print(f"Downloading code from s3://{bucket_name}/{s3_key}...")
        s3_response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
        code = s3_response["Body"].read().decode("utf-8")
        print("Code downloaded successfully.")
        print("Running:")
        print(f"{code}")

        # Prepare the execution environment
        exec_globals = {"context": context_dict}
        exec_locals = {}

        # Execute the code
        print("Executing the dynamic code...")
        exec(code, exec_globals, exec_locals)
        print("Code executed successfully.")

        # Assume the last expression in the code is the result
        result = exec_locals.get("result", exec_globals.get("result"))
        print(f"Execution result: {result}")

        return {"success": True, "result": result}

    except Exception as e:
        print(f"Error in invoke_code: {str(e)}")
        return {
            "success": False,
            "error": f"Failed to invoke the dynamic function: {str(e)}",
        }


@api_tool(
    path="/code/list",
    tags=["code"],
    name="listUserFunctions",
    description="List all the Python functions created by the current user.",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the listing was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "functions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "uuid": {
                                    "type": "string",
                                    "description": "Unique identifier of the function",
                                },
                                "s3Key": {
                                    "type": "string",
                                    "description": "S3 key where the script is stored",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Description of what the function does",
                                },
                                "created_at": {
                                    "type": "string",
                                    "description": "ISO timestamp when the function was created",
                                },
                                "last_updated": {
                                    "type": "string",
                                    "description": "ISO timestamp when the function was last updated",
                                },
                            },
                            "required": [
                                "uuid",
                                "s3Key",
                                "description",
                                "created_at",
                                "last_updated",
                            ],
                        },
                        "description": "List of user's created functions",
                    }
                },
                "required": ["functions"],
            },
            "message": {
                "type": "string",
                "description": "Error message if success is false",
            },
        },
        "required": ["success"],
    },
)
@validated(op="list")
def list_user_code(event, context, current_user, name, data):
    try:
        # Define the table name from the environment variable
        dynamo_table = os.getenv("DYNAMO_DYNAMIC_CODE_TABLE")

        # Scan the table to find entries where the 'creator' matches the current_user
        response = dynamodb_client.scan(
            TableName=dynamo_table,
            FilterExpression="creator = :creator",
            ExpressionAttributeValues={":creator": {"S": current_user}},
        )

        # Extract the items from the response
        items = response.get("Items", [])
        functions = []

        # Process the items to extract the relevant information
        for item in items:
            functions.append(
                {
                    "uuid": item["uuid"]["S"],
                    "s3Key": item["s3Key"]["S"],
                    "description": item.get("description", {}).get(
                        "S", "No description available"
                    ),
                    "created_at": item.get("created_at", {}).get("S", "Unknown"),
                    "last_updated": item.get("last_updated", {}).get("S", "Unknown"),
                }
            )

        return {"success": True, "data": {"functions": functions}}

    except Exception as e:
        print(f"Error in list_user_functions: {str(e)}")
        return {"success": False, "message": f"Failed to list user functions: {str(e)}"}


# bucket = "dynamic-code-test"
# key = "sampledyn.py"
#
# invoke_dynamic_function(None, None, None, None, {'data': {'s3_key':key ,'context':{"a": 10, "b": 20, "operation": "hypotenuse"}}})
