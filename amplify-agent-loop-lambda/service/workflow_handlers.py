from workflow.workflow_template_registry import list_workflow_templates, get_workflow_template, \
    register_workflow_template, delete_workflow_template, update_workflow_template
from common.ops import vop

@vop(
    path="/vu-agent/register-workflow-template",
    tags=["workflows"],
    name="registerWorkflowTemplate",
    description="Register a new workflow template.",
    params={
        "template": "The workflow template definition with steps",
        "name": "Name of the template",
        "description": "Description of the template",
        "inputSchema": "Schema defining the template inputs",
        "outputSchema": "Schema defining the template outputs"
    },
    schema={
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
                                "values": {"type": "object", "additionalProperties": {"type": "string"}},
                                "args": {"type": "object", "additionalProperties": {"type": "string"}},
                                "stepName": {"type": "string"},
                                "actionSegment": {"type": "string"},
                                "editableArgs": {"type": "array", "items": {"type": "string"}},
                                "useAdvancedReasoning": {"type": "boolean"}
                            },
                            "required": ["tool", "instructions"]
                        }
                    }
                },
                "required": ["steps"]
            },
            "name": {"type": "string"},
            "description": {"type": "string"},
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "object"},
            "isBaseTemplate": {"type": "boolean"},
            "isPublic": {"type": "boolean"}
        },
        "required": ["template", "name", "description", "inputSchema", "outputSchema"]
    }
)
def register_workflow_template_handler(current_user, access_token, template, name, description, input_schema, output_schema, is_base_template=False, is_public=False):
    try:
        template_id = register_workflow_template(  # Use template_id as per earlier changes
            current_user=current_user,
            template=template,
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            is_base_template=is_base_template,
            is_public=is_public
        )
        print(f"Registered workflow template: {template_id}")
        return {"templateId": template_id}  # Use camel case in response
    except Exception as e:
        print(f"Error registering workflow template: {e}")
        raise RuntimeError(f"Failed to register workflow template: {str(e)}")

@vop(
    path="/vu-agent/delete-workflow-template",
    tags=["workflows"],
    name="deleteWorkflowTemplate",
    description="Delete a workflow template by ID.",
    params={
        "templateId": "ID of the template to delete"
    },
    schema={
        "type": "object",
        "properties": {
            "templateId": {"type": "string"}
        },
        "required": ["templateId"]
    }
)
def delete_workflow_template_handler(current_user, access_token, template_id):
    try:
        result = delete_workflow_template(current_user, template_id)
        print(f"Delete workflow template result: {result}")
        return result
    except Exception as e:
        print(f"Error deleting workflow template: {e}")
        raise RuntimeError(f"Failed to delete workflow template: {str(e)}")


@vop(
    path="/vu-agent/get-workflow-template",
    tags=["workflows"],
    name="getWorkflowTemplate",
    description="Get a workflow template by ID.",
    params={
        "templateId": "ID of the template to retrieve"
    },
    schema={
        "type": "object",
        "properties": {
            "templateId": {"type": "string"}
        },
        "required": ["templateId"]
    }
)
def get_workflow_template_handler(current_user, access_token, template_id):
    try:
        template = get_workflow_template(current_user, template_id)
        if template is None:
            raise ValueError("Template not found")
        return template  # No need for conversion; already uses templateId
    except Exception as e:
        raise RuntimeError(f"Failed to get workflow template: {str(e)}")

@vop(
    path="/vu-agent/list-workflow-templates",
    tags=["workflows"],
    name="listWorkflowTemplates",
    description="List all workflow templates for the current user.",
    schema={
       "type": "object",
        "properties": {
            "filterBaseTemplates": {"type": "boolean"},
            "includePublicTemplates": {"type": "boolean"}
        },
        "required": []
    },
    params={"filterBaseTemplates": "Optional boolean to filter for base templates only"}
)
def list_workflow_templates_handler(current_user, access_token, filter_base_templates=False, include_public_templates=False):
    try:
        templates = list_workflow_templates(current_user, include_public_templates)
        if filter_base_templates:
            templates = [t for t in templates if t['isBaseTemplate']]
        return {"templates": templates}  # No need for conversion; already uses templateId
    except Exception as e:
        raise RuntimeError(f"Failed to list workflow templates: {str(e)}")

@vop(
    path="/vu-agent/update-workflow-template",
    tags=["workflows"],
    name="updateWorkflowTemplate",
    description="Update an existing workflow template.",
    params={
        "templateId": "ID of the template to update",
        "template": "The updated workflow template definition with steps",
        "name": "Updated name of the template",
        "description": "Updated description of the template",
        "inputSchema": "Updated schema defining the template inputs",
        "outputSchema": "Updated schema defining the template outputs",
        "isBaseTemplate": "Whether this is a base template",
        "isPublic": "Whether this template is publicly accessible"
    },
    schema={
        "type": "object",
        "properties": {
            "templateId": {"type": "string"},
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
                                "values": {"type": "object", "additionalProperties": {"type": "string"}},
                                "args": {"type": "object", "additionalProperties": {"type": "string"}},
                                "stepName": {"type": "string"},
                                "actionSegment": {"type": "string"},
                                "editableArgs": {"type": "array", "items": {"type": "string"}},
                                "useAdvancedReasoning": {"type": "boolean"}
                            },
                            "required": ["tool", "instructions"]
                        }
                    }
                },
                "required": ["steps"]
            },
            "name": {"type": "string"},
            "description": {"type": "string"},
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "object"},
            "isBaseTemplate": {"type": "boolean"},
            "isPublic": {"type": "boolean"}
        },
        "required": ["templateId", "template", "name", "description", "inputSchema", "outputSchema"]
    }
)
def update_workflow_template_handler(current_user, access_token, template_id, template, name, description, 
                                      input_schema, output_schema, is_base_template=False, is_public=False):
    try:
        result = update_workflow_template(
            current_user=current_user,
            template_id=template_id,
            template=template,
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            is_base_template=is_base_template,
            is_public=is_public
        )
        print(f"Updated workflow template: {template_id}")
        return result
    except Exception as e:
        print(f"Error updating workflow template: {e}")
        raise RuntimeError(f"Failed to update workflow template: {str(e)}")

