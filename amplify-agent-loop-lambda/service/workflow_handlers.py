from workflow.workflow_template_registry import (
    list_workflow_templates,
    get_workflow_template,
    register_workflow_template,
    delete_workflow_template,
    update_workflow_template,
)
from pycommon.api.ops import api_tool
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation
)
from pycommon.logger import getLogger
logger = getLogger("workflow_handlers")


@required_env_vars({
    "WORKFLOW_TEMPLATES_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@api_tool(
    path="/vu-agent/register-workflow-template",
    tags=["workflows"],
    name="registerWorkflowTemplate",
    description="Register a new workflow template.",
    parameters={
        "type": "object",
        "properties": {
            "template": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "tool": {"type": "string"},
                                "instructions": {"type": "string"},
                                "values": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                                "args": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                                "stepName": {"type": "string"},
                                "actionSegment": {"type": "string"},
                                "editableArgs": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "useAdvancedReasoning": {"type": "boolean"},
                            },
                            "required": ["tool", "instructions"],
                        },
                    }
                },
                "required": ["steps"],
                "description": "The workflow template definition with steps",
            },
            "name": {"type": "string", "description": "Name of the template"},
            "description": {
                "type": "string",
                "description": "Description of the template",
            },
            "inputSchema": {
                "type": "object",
                "description": "Schema defining the template inputs",
            },
            "outputSchema": {
                "type": "object",
                "description": "Schema defining the template outputs",
            },
            "isBaseTemplate": {
                "type": "boolean",
                "description": "Whether this is a base template",
            },
            "isPublic": {
                "type": "boolean",
                "description": "Whether this template is publicly accessible",
            },
        },
        "required": ["template", "name", "description", "inputSchema", "outputSchema"],
    },
    output={
        "type": "object",
        "properties": {
            "templateId": {
                "type": "string",
                "description": "The unique ID of the registered workflow template",
            }
        },
        "required": ["templateId"],
    },
)
def register_workflow_template_handler(
    current_user,
    access_token,
    template,
    name,
    description,
    input_schema,
    output_schema,
    is_base_template=False,
    is_public=False,
):
    try:
        template_id = (
            register_workflow_template(  # Use template_id as per earlier changes
                current_user=current_user,
                template=template,
                name=name,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
                is_base_template=is_base_template,
                is_public=is_public,
                access_token=access_token,
            )
        )
        logger.info("Registered workflow template: %s", template_id)
        return {"templateId": template_id}  # Use camel case in response
    except Exception as e:
        logger.error("Error registering workflow template: %s", e)
        raise RuntimeError(f"Failed to register workflow template: {str(e)}")


@required_env_vars({
    "WORKFLOW_TEMPLATES_TABLE": [DynamoDBOperation.DELETE_ITEM],
})
@api_tool(
    path="/vu-agent/delete-workflow-template",
    tags=["workflows"],
    name="deleteWorkflowTemplate",
    description="Delete a workflow template by ID.",
    parameters={
        "type": "object",
        "properties": {
            "templateId": {
                "type": "string",
                "description": "ID of the template to delete",
            }
        },
        "required": ["templateId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the template was deleted successfully",
            },
            "message": {"type": "string", "description": "Success or error message"},
        },
        "required": ["success", "message"],
    },
)
def delete_workflow_template_handler(current_user, access_token, template_id):
    try:
        return delete_workflow_template(current_user, template_id, access_token)

    except Exception as e:
        logger.error("Error deleting workflow template: %s", e)
        raise RuntimeError(f"Failed to delete workflow template: {str(e)}")


@required_env_vars({
    "WORKFLOW_TEMPLATES_TABLE": [DynamoDBOperation.GET_ITEM],
})
@api_tool(
    path="/vu-agent/get-workflow-template",
    tags=["workflows"],
    name="getWorkflowTemplate",
    description="Get a workflow template by ID.",
    parameters={
        "type": "object",
        "properties": {
            "templateId": {
                "type": "string",
                "description": "ID of the template to retrieve",
            }
        },
        "required": ["templateId"],
    },
    output={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the template"},
            "description": {
                "type": "string",
                "description": "Description of the template",
            },
            "inputSchema": {
                "type": "object",
                "description": "Schema defining the template inputs",
            },
            "outputSchema": {
                "type": "object",
                "description": "Schema defining the template outputs",
            },
            "templateId": {
                "type": "string",
                "description": "Unique identifier of the template",
            },
            "template": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "tool": {"type": "string"},
                                "instructions": {"type": "string"},
                                "values": {"type": "object"},
                                "args": {"type": "object"},
                                "stepName": {"type": "string"},
                                "actionSegment": {"type": "string"},
                                "editableArgs": {"type": "array"},
                                "useAdvancedReasoning": {"type": "boolean"},
                            },
                        },
                    }
                },
                "description": "The workflow template definition",
            },
            "isPublic": {
                "type": "boolean",
                "description": "Whether the template is publicly accessible",
            },
            "isBaseTemplate": {
                "type": "boolean",
                "description": "Whether this is a base template",
            },
        },
        "required": [
            "name",
            "description",
            "inputSchema",
            "outputSchema",
            "templateId",
            "template",
            "isPublic",
            "isBaseTemplate",
        ],
    },
)
def get_workflow_template_handler(current_user, access_token, template_id):
    try:
        template = get_workflow_template(current_user, template_id, access_token)
        if template is None:
            raise ValueError("Template not found")
        return template  # No need for conversion; already uses templateId
    except Exception as e:
        raise RuntimeError(f"Failed to get workflow template: {str(e)}")


@required_env_vars({
    "WORKFLOW_TEMPLATES_TABLE": [DynamoDBOperation.SCAN],
})
@api_tool(
    path="/vu-agent/list-workflow-templates",
    tags=["workflows"],
    name="listWorkflowTemplates",
    description="List all workflow templates for the current user.",
    parameters={
        "type": "object",
        "properties": {
            "filterBaseTemplates": {
                "type": "boolean",
                "description": "Optional boolean to filter for base templates only",
            },
            "includePublicTemplates": {
                "type": "boolean",
                "description": "Optional boolean to include public templates",
            },
        },
        "required": [],
    },
    output={
        "type": "object",
        "properties": {
            "templates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "templateId": {
                            "type": "string",
                            "description": "Unique identifier of the template",
                        },
                        "name": {
                            "type": "string",
                            "description": "Name of the template",
                        },
                        "description": {
                            "type": "string",
                            "description": "Description of the template",
                        },
                        "inputSchema": {
                            "type": "object",
                            "description": "Schema defining the template inputs",
                        },
                        "outputSchema": {
                            "type": "object",
                            "description": "Schema defining the template outputs",
                        },
                        "isBaseTemplate": {
                            "type": "boolean",
                            "description": "Whether this is a base template",
                        },
                        "isPublic": {
                            "type": "boolean",
                            "description": "Whether the template is publicly accessible",
                        },
                    },
                    "required": [
                        "templateId",
                        "name",
                        "description",
                        "inputSchema",
                        "outputSchema",
                        "isBaseTemplate",
                        "isPublic",
                    ],
                },
                "description": "List of workflow templates",
            }
        },
        "required": ["templates"],
    },
)
def list_workflow_templates_handler(current_user, access_token, filter_base_templates=False, include_public_templates=False):
    try:
        templates = list_workflow_templates(current_user, include_public_templates)
        if filter_base_templates:
            templates = [t for t in templates if t["isBaseTemplate"]]
        return {
            "templates": templates
        }  # No need for conversion; already uses templateId
    except Exception as e:
        raise RuntimeError(f"Failed to list workflow templates: {str(e)}")


@required_env_vars({
    "WORKFLOW_TEMPLATES_TABLE": [DynamoDBOperation.UPDATE_ITEM],
})
@api_tool(
    path="/vu-agent/update-workflow-template",
    tags=["workflows"],
    name="updateWorkflowTemplate",
    description="Update an existing workflow template.",
    parameters={
        "type": "object",
        "properties": {
            "templateId": {
                "type": "string",
                "description": "ID of the template to update",
            },
            "template": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "tool": {"type": "string"},
                                "instructions": {"type": "string"},
                                "values": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                                "args": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                                "stepName": {"type": "string"},
                                "actionSegment": {"type": "string"},
                                "editableArgs": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "useAdvancedReasoning": {"type": "boolean"},
                            },
                            "required": ["tool", "instructions"],
                        },
                    }
                },
                "required": ["steps"],
                "description": "The updated workflow template definition with steps",
            },
            "name": {"type": "string", "description": "Updated name of the template"},
            "description": {
                "type": "string",
                "description": "Updated description of the template",
            },
            "inputSchema": {
                "type": "object",
                "description": "Updated schema defining the template inputs",
            },
            "outputSchema": {
                "type": "object",
                "description": "Updated schema defining the template outputs",
            },
            "isBaseTemplate": {
                "type": "boolean",
                "description": "Whether this is a base template",
            },
            "isPublic": {
                "type": "boolean",
                "description": "Whether this template is publicly accessible",
            },
        },
        "required": [
            "templateId",
            "template",
            "name",
            "description",
            "inputSchema",
            "outputSchema",
        ],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the template was updated successfully",
            },
            "message": {"type": "string", "description": "Success or error message"},
            "templateId": {
                "type": "string",
                "description": "The ID of the updated template",
            },
        },
        "required": ["success", "message", "templateId"],
    },
)
def update_workflow_template_handler(
    current_user,
    access_token,
    template_id,
    template,
    name,
    description,
    input_schema,
    output_schema,
    is_base_template=False,
    is_public=False,
):
    try:
        return update_workflow_template(
            current_user=current_user,
            template_id=template_id,
            template=template,
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            is_base_template=is_base_template,
            is_public=is_public,
            access_token=access_token,
        )

    except Exception as e:
        logger.error("Error updating workflow template: %s", e)
        raise RuntimeError(f"Failed to update workflow template: {str(e)}")
