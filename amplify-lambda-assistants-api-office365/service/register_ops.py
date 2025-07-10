import os
import ast
from typing import List, Optional
from pydantic import BaseModel, field_validator, ValidationError
from boto3.dynamodb.conditions import Key
import uuid
import boto3


class OperationModel(BaseModel):
    description: str
    id: str
    includeAccessToken: bool
    method: str
    name: str
    tags: List[str]
    type: str = "integration"  # Default for backward compatibility
    url: str
    parameters: Optional[dict] = None  # Input schema
    output: Optional[dict] = None  # Output schema
    permissions: Optional[dict] = None

    @field_validator("method")
    def validate_method(cls, v):
        allowed_methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
        if v.upper() not in allowed_methods:
            raise ValueError(f"Method must be one of {allowed_methods}")
        return v.upper()


def extract_complex_dict(ast_node):
    """Extract complex dictionary from AST Dict node."""
    result = {}
    for key, value in zip(ast_node.keys, ast_node.values):
        if isinstance(value, ast.Dict):
            result[key.s] = extract_complex_dict(value)
        elif isinstance(value, ast.List):
            result[key.s] = extract_list(value)
        elif isinstance(value, ast.Constant):
            result[key.s] = value.value
        else:
            # Try to get a literal value or default to string representation
            try:
                result[key.s] = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                result[key.s] = str(value)
    return result


def extract_list(ast_node):
    """Extract list from AST List node."""
    result = []
    for item in ast_node.elts:
        if isinstance(item, ast.Dict):
            result.append(extract_complex_dict(item))
        elif isinstance(item, ast.List):
            result.append(extract_list(item))
        elif isinstance(item, ast.Constant):
            result.append(item.value)
        else:
            # Try to get a literal value or default to string representation
            try:
                result.append(ast.literal_eval(item))
            except (ValueError, SyntaxError):
                result.append(str(item))
    return result


def extract_tags(op_kwargs):
    tags = op_kwargs.get("tags", [])

    # Ensure tags is a list
    if isinstance(tags, ast.List):
        # Extract elements from the ast.List
        tags = [
            elt.value if isinstance(elt, ast.Constant) else str(elt)
            for elt in tags.elts
        ]

    return tags if isinstance(tags, list) else []


def integration_config_trigger(event, context):
    """
    Triggered by a DynamoDB stream event on the admin configs table.
    For the 'integrations' record, on MODIFY events: if the specified provider key
    (PROVIDER) is newly added to the data field, call register_ops.
    """
    print("Admin Config Trigger invoked")
    PROVIDER = "microsoft"
    for record in event.get("Records", []):
        if record.get("eventName") != "MODIFY":
            continue

        new_image = record.get("dynamodb", {}).get("NewImage", {})
        config_id = new_image.get("config_id", {}).get("S")
        if config_id != "integrations":
            continue

        old_image = record.get("dynamodb", {}).get("OldImage", {})
        new_data = new_image.get("data", {})
        old_data = old_image.get("data", {})

        def extract_keys(data_field):
            # Assuming data_field is stored as a DynamoDB Map attribute.
            if "M" in data_field:
                return set(data_field["M"].keys())
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
            if not result.get("success"):
                print(f"Failed to register {PROVIDER} ops")
        else:
            print(f"Provider {PROVIDER} ops already exists, skipping op registration")


def register_ops(current_user: str = "system", file_path: Optional[str] = None) -> dict:
    """
    Registers operations by scanning a single file (defaulting to 'core.py' in this folder)
    for operations decorated with @api_tool and writing them to the DynamoDB table.

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
    table_name = os.environ.get("OPS_DYNAMODB_TABLE")
    if not table_name:
        return {
            "success": False,
            "message": "DynamoDB table name is not set in environment variables.",
        }

    # Extract operations from the given file.
    ops = extract_ops_from_file(file_path)
    if not ops:
        print(f"No operations found in file: {file_path}")
        return {"success": True, "message": f"No operations found in file: {file_path}"}

    # Register the extracted operations to DynamoDB.
    response = write_ops(current_user=current_user, ops=ops)
    return response


def extract_ops_from_file(file_path: str) -> List[OperationModel]:
    try:
        ops_found = []
        with open(file_path, "r") as file:
            content = file.read()

        # Parse the abstract syntax tree of the file
        tree = ast.parse(content)

        # First, scan for set_op_type calls to get the default op_type for this file
        file_op_type = "built_in"  # Default
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = getattr(node.func, "id", None)
                if func_name == "set_op_type" and node.args:
                    # Extract the op_type value from set_op_type("some_type")
                    arg = node.args[0]
                    if hasattr(arg, "s"):  # String literal
                        file_op_type = arg.s
                    else:
                        file_op_type = str(arg)
                    break

        # Look for function definitions and their decorators
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    # Look for decorators with parentheses (ast.Call)
                    decorator_name = None
                    if isinstance(decorator, ast.Call) and hasattr(decorator, "func"):
                        if hasattr(decorator.func, "id"):
                            decorator_name = decorator.func.id
                    elif isinstance(decorator, ast.Name):  # pragma: no cover
                        decorator_name = decorator.id

                    if decorator_name in [
                        "op",
                        "vop",
                        "api_tool",
                    ]:
                        op_kwargs = {}
                        if isinstance(decorator, ast.Call):
                            op_kwargs = {kw.arg: kw.value for kw in decorator.keywords}
                        if (
                            "path" in op_kwargs
                            and "name" in op_kwargs
                            and "description" in op_kwargs
                        ):

                            # Extract parameters (input schema)
                            parameters = None
                            if "parameters" in op_kwargs:
                                parameters = extract_complex_dict(
                                    op_kwargs["parameters"]
                                )

                            # Extract output (output schema)
                            output = None
                            if "output" in op_kwargs:
                                output = extract_complex_dict(op_kwargs["output"])

                            # Extract permissions
                            permissions = None
                            if "permissions" in op_kwargs:
                                permissions = extract_complex_dict(
                                    op_kwargs["permissions"]
                                )

                            # Extract method
                            method = "POST"
                            if "method" in op_kwargs:
                                method_value = op_kwargs["method"]
                                if hasattr(method_value, "s"):
                                    method = method_value.s
                                else:
                                    method = str(method_value)

                            # Extract tags
                            tags = extract_tags(op_kwargs)

                            # Use file_op_type (from set_op_type call) as the default
                            op_type = file_op_type

                            # Extract type if explicitly provided
                            if "type" in op_kwargs:
                                type_value = op_kwargs["type"]
                                if hasattr(type_value, "s"):
                                    op_type = type_value.s
                                elif hasattr(type_value, "value"):
                                    op_type = type_value.value
                                else:
                                    op_type = str(type_value)

                            # Extract name and description safely
                            name = ""
                            description = ""
                            path = ""

                            if hasattr(op_kwargs["name"], "s"):
                                name = op_kwargs["name"].s
                            elif hasattr(op_kwargs["name"], "value"):
                                name = op_kwargs["name"].value
                            else:
                                name = str(op_kwargs["name"])

                            if hasattr(op_kwargs["description"], "s"):
                                description = op_kwargs["description"].s
                            elif hasattr(op_kwargs["description"], "value"):
                                description = op_kwargs["description"].value
                            else:
                                description = str(op_kwargs["description"])

                            if hasattr(op_kwargs["path"], "s"):
                                path = op_kwargs["path"].s
                            elif hasattr(op_kwargs["path"], "value"):
                                path = op_kwargs["path"].value
                            else:
                                path = str(op_kwargs["path"])

                            operation = OperationModel(
                                description=description,
                                id=name,
                                includeAccessToken=True,
                                method=method,
                                name=name,
                                type=op_type,  # Use file-level op_type or explicit type
                                url=path,
                                tags=tags,
                                parameters=parameters,  # Input schema
                                output=output,  # Output schema
                                permissions=permissions,
                            )
                            ops_found.append(operation)
        return ops_found
    except Exception as e:
        print(e)
        print(f"Skipping {file_path} due to unparseable AST")
        return []


def write_ops(current_user: str = "system", ops: List[OperationModel] = None):
    print_pretty_ops(ops)
    dynamodb = boto3.resource("dynamodb")

    # Get the DynamoDB table name from the environment variable
    table_name = os.environ.get("OPS_DYNAMODB_TABLE")
    if not table_name:
        return {
            "success": False,
            "message": "DynamoDB table name is not set in environment variables",
        }

    # Use a resource client to interact with DynamoDB
    table = dynamodb.Table(table_name)

    # Check if `ops` is provided
    if ops is None:
        return {"success": False, "message": "Operations must be provided"}

    # Validate and Serialize operations for DynamoDB
    for op in ops:
        try:
            op_dict = op.model_dump()
        except ValidationError as e:
            return {"success": False, "message": f"Operation validation failed: {e}"}

        # Check and register based on tags attached to the operation
        operation_tags = op_dict.get("tags", ["default"])
        operation_tags.append("all")

        for tag in operation_tags:
            # Check if an entry exists
            response = table.query(
                KeyConditionExpression=Key("user").eq(current_user) & Key("tag").eq(tag)
            )
            existing_items = response["Items"]

            if existing_items:
                # If an entry exists, update it by checking for op id
                for item in existing_items:
                    existing_ops = item["ops"]
                    op_exists = False

                    for index, existing_op in enumerate(existing_ops):
                        if existing_op["id"] == op_dict["id"]:
                            print(
                                f"Updating {op_dict['id']} for user {current_user} and tag {tag}"
                            )
                            existing_ops[index] = op_dict
                            op_exists = True
                            break

                    if not op_exists:
                        existing_ops.append(op_dict)

                    table.update_item(
                        Key={
                            "user": current_user,
                            "tag": tag,
                        },
                        UpdateExpression="SET ops = :ops",
                        ExpressionAttributeValues={
                            ":ops": existing_ops,
                        },
                    )
                    print(
                        f"Updated item in table {table_name} for user {current_user} "
                        f"and tag {tag}"
                    )
            else:
                # If no entry exists, create a new one
                item = {
                    "id": str(uuid.uuid4()),  # Using UUID to ensure unique primary key
                    "user": current_user,
                    "tag": tag,
                    "ops": [op_dict],
                }
                table.put_item(Item=item)
                print(
                    f"Put item into table {table_name} for user {current_user} "
                    f"and tag {tag}: {item}"
                )

    return {
        "success": True,
        "message": "Successfully associated operations with provided tags and user",
    }


def print_pretty_ops(ops: List[OperationModel]):
    for op in ops:
        print("Operation Details:")
        print(f"  Name       : {op.name}")
        print(f"  URL        : {op.url}")
        print(f"  Method     : {op.method}")
        print(f"  Description: {op.description}")
        print(f"  ID         : {op.id}")
        if op.parameters:
            print("  Parameters (Input Schema):")
            properties = op.parameters.get("properties", {})
            for prop_name, prop_def in properties.items():
                if isinstance(prop_def, dict):
                    description = prop_def.get("description", prop_name)
                    print(f"    - {prop_name} : {description}")
        print(f"  Include Access Token: {op.includeAccessToken}")
        print(f"  Type       : {op.type}")
        if op.output:
            print(f"  Output Schema: {op.output}")
        if op.permissions:
            print(f"  Permissions: {op.permissions}")
        print("")
