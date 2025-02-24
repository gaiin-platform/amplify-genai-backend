
import json
import os
import ast
from typing import List, Optional
from pydantic import BaseModel, field_validator, ValidationError
from boto3.dynamodb.conditions import Key
import os
import uuid
import boto3
from pydantic import ValidationError
import os



class ParamModel(BaseModel):
    description: str
    name: str

class OperationModel(BaseModel):
    description: str
    id: str
    includeAccessToken: bool
    method: str
    name: str
    tags: List[str]
    params: List[ParamModel]
    type: str
    url: str
    parameters: Optional[dict] = None

    @field_validator('method')
    def validate_method(cls, v):
        allowed_methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
        if v.upper() not in allowed_methods:
            raise ValueError(f"Method must be one of {allowed_methods}")
        return v.upper()


def integration_config_trigger(event, context):
    """
    Triggered by a DynamoDB stream event on the admin configs table.
    For the 'integrations' record, on MODIFY events: if the specified provider key
    (PROVIDER) is newly added to the data field, call register_ops.
    """
    print("Admin Config Trigger invoked")
    PROVIDER = "google"
    for record in event.get('Records', []):
        if record.get('eventName') != 'MODIFY':
            continue

        new_image = record.get('dynamodb', {}).get('NewImage', {})
        config_id = new_image.get('config_id', {}).get('S')
        if config_id != "integrations":
            continue

        old_image = record.get('dynamodb', {}).get('OldImage', {})
        new_data = new_image.get('data', {})
        old_data = old_image.get('data', {})

        def extract_keys(data_field):
            # Assuming data_field is stored as a DynamoDB Map attribute.
            if 'M' in data_field:
                return set(data_field['M'].keys())
            # Assume it's already a native dict.
            return set(data_field.keys())

        old_keys = extract_keys(old_data)
        new_keys = extract_keys(new_data)
        print(f"Old keys: {old_keys}")
        print(f"New keys: {new_keys}")

        # If the provider key was absent before and is now present, register ops.
        if PROVIDER not in old_keys and PROVIDER in new_keys:
            print(f"Registering {PROVIDER} ops (provider key added)")
            result = register_ops()
            if not result.get('success'):
                print(f"Failed to register {PROVIDER} ops")
        else:
            print(f"Provider {PROVIDER} ops already exists, skipping op registration")

def register_ops(current_user: str = "system", file_path: Optional[str] = None) -> dict:
    """
    Registers operations by scanning a single file (defaulting to 'core.py' in this folder)
    for operations decorated with @op or @vop and writing them to the DynamoDB table.

    The DynamoDB table name must be set in the environment variable 'OPS_DYNAMODB_TABLE'.

    Args:
        current_user (str): The current user identifier. Defaults to "system".
        file_path (Optional[str]): The file path to inspect for operations.
                                   If None, it defaults to 'core.py' in the same directory as this file.

    Returns:
        dict: A dictionary indicating the success or failure of the registration and a message.
    """
    # If no file path is provided, default to core.py in the same directory as this file.
    if file_path is None:
        current_dir = os.path.dirname(__file__)
        file_path = os.path.join(current_dir, "core.py")
    
    # Ensure the DynamoDB table name is set via environment variable.
    table_name = os.environ.get('OPS_DYNAMODB_TABLE')
    if not table_name:
        return {
            "success": False,
            "message": "DynamoDB table name is not set in environment variables."
        }
    
    # Extract operations from the given file.
    ops = extract_ops_from_file(file_path)
    if not ops:
        print(f"No operations found in file: {file_path}")
        return {
            "success": True,
            "message": f"No operations found in file: {file_path}"
        }
    
    # Register the extracted operations to DynamoDB.
    response = write_ops(current_user=current_user, ops=ops)
    return response




def extract_ops_from_file(file_path: str) -> List[OperationModel]:
    try:
        ops_found = []
        with open(file_path, 'r') as file:
            content = file.read()

        # Parse the abstract syntax tree of the file
        tree = ast.parse(content)

        # Look for function definitions and their decorators
        for node in ast.walk(tree):
            try:
                if isinstance(node, ast.FunctionDef):
                    for decorator in node.decorator_list:
                        if isinstance(decorator, ast.Call) and (getattr(decorator.func, 'id', None) == 'op' or getattr(decorator.func, 'id', None) == 'vop'):
                            op_kwargs = {kw.arg: kw.value for kw in decorator.keywords}
                            if 'path' in op_kwargs and 'name' in op_kwargs and 'description' in op_kwargs and ('params' in op_kwargs or 'parameters' in op_kwargs):

                                params_dict = extract_dict( op_kwargs.get('params', ast.Dict(keys=[], values=[])) )


                                params = [ParamModel(description=desc, name=name) for name, desc in params_dict.items()] if 'params' in op_kwargs else []
                                parameters = extract_dict_from_ast(op_kwargs.get('parameters', ast.Dict(keys=[], values=[])))
                                try:
                                    operation = OperationModel(
                                        description=op_kwargs['description'].s,
                                        id=op_kwargs['name'].s,
                                        includeAccessToken=True,  # Assuming ops will include access token
                                        method=op_kwargs['method'].s if 'method' in op_kwargs else "POST",  # Default method
                                        name=op_kwargs['name'].s,
                                        params=params,
                                        type="custom",  # Assuming custom type
                                        url=op_kwargs['path'].s,
                                        tags=extract_tags(op_kwargs),
                                        parameters=parameters
                                    )
                                    ops_found.append(operation)
                                except ValidationError as ve:
                                    print(f"\nValidation error in {file_path}:")
                                    for error in ve.errors():
                                        print(f"Parsing: "+ op_kwargs['name'].s)
                                        print(f"Field: {' -> '.join(str(x) for x in error['loc'])}")
                                        print(f"Error: {error['msg']}")
                                        print(f"Type: {error['type']}\n")
            except Exception as e:
                print(f"Error processing function {node.name} in {file_path}: {e}")


        return ops_found
    except Exception as e:
        print(f"Error processing {file_path}:")
        print(e)
        print(f"Skipping {file_path} due to unparseable AST")
        return []



def write_ops(current_user: str = 'system', ops: List[OperationModel] = None):
    print_pretty_ops(ops)
    dynamodb = boto3.resource('dynamodb')

    # Get the DynamoDB table name from the environment variable
    table_name = os.environ.get('OPS_DYNAMODB_TABLE')
    if not table_name:
        return {
            "success": False,
            "message": "DynamoDB table name is not set in environment variables"
        }

    # Use a resource client to interact with DynamoDB
    table = dynamodb.Table(table_name)


    # Validate and Serialize operations for DynamoDB
    for op in ops:
        try:
            op_dict = op.model_dump()
        except ValidationError as e:
            return {
                "success": False,
                "message": f"Operation validation failed: {e}"
            }

        # Check and register based on tags attached to the operation
        operation_tags = op_dict.get('tags', ['default'])
        operation_tags.append("all")

        for tag in operation_tags:
            # Check if an entry exists
            response = table.query(
                KeyConditionExpression=Key('user').eq(current_user) & Key('tag').eq(tag)
            )
            existing_items = response['Items']

            if existing_items:
                # If an entry exists, update it by checking for op id
                for item in existing_items:
                    existing_ops = item['ops']
                    op_exists = False

                    for index, existing_op in enumerate(existing_ops):
                        if existing_op['id'] == op_dict['id']:
                            print(f"Updating {op_dict['id']} for user {current_user} and tag {tag}")
                            # print(f"Operation: {json.dumps(op_dict, indent=2)}")
                            existing_ops[index] = op_dict
                            op_exists = True
                            break

                    if not op_exists:
                        existing_ops.append(op_dict)

                    table.update_item(
                        Key={
                            'user': current_user,
                            'tag': tag,
                        },
                        UpdateExpression="SET ops = :ops",
                        ExpressionAttributeValues={
                            ':ops': existing_ops,
                        }
                    )
                    print(f"Published operation with id {op.id} to table {table_name} for user {current_user} and tag {tag}: {op_dict}")
            else:
                # If no entry exists, create a new one
                item = {
                    'id': str(uuid.uuid4()),  # Using UUID to ensure unique primary key
                    'user': current_user,
                    'tag': tag,
                    'ops': [op_dict]
                }
                table.put_item(Item=item)
                print(f"Published operation with id {op.id} to table {table_name} for user {current_user} and tag {tag}: {op_dict}")

    return {
        "success": True,
        "message": "Successfully associated operations with provided tags and user",
    }



def print_pretty_ops(ops: List[OperationModel]):
    for op in ops:
        print("RegisteringOperation...")
        print(f"  Name       : {op.name}\n  URL        : {op.url}")



def extract_dict(ast_node):
    """Extract dictionary from AST Dict node."""
    return {key.s: value.s for key, value in zip(ast_node.keys, ast_node.values)}


def extract_dict_from_ast(node):
    """Helper function to extract dictionary from AST nodes"""
    if isinstance(node, ast.Dict):
        keys = []
        values = []
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant):
                key = k.value
            elif isinstance(k, ast.Str):  # for older Python versions
                key = k.s
            else:
                continue

            if isinstance(v, ast.Dict):
                value = extract_dict_from_ast(v)
            elif isinstance(v, ast.List):
                value = [x.value if isinstance(x, ast.Constant) else x.s for x in v.elts]
            elif isinstance(v, ast.Constant):
                value = v.value
            elif isinstance(v, ast.Str):  # for older Python versions
                value = v.s
            else:
                continue

            keys.append(key)
            values.append(value)
        return dict(zip(keys, values))
    return {}

def extract_tags(op_kwargs):
    tags = op_kwargs.get('tags', [])

    # Ensure tags is a list
    if isinstance(tags, ast.List):
        # Extract elements from the ast.List
        tags = [elt.s if isinstance(elt, ast.Str) else str(elt) for elt in tags.elts]

    return tags if isinstance(tags, list) else []

