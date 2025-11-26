import copy
import re

from integrations.o365.calendar import (
    add_attachment,
    check_event_conflicts,
    create_calendar,
    create_event,
    create_recurring_event,
    delete_attachment,
    delete_calendar,
    delete_event,
    find_meeting_times,
    get_calendar_permissions,
    get_event_details,
    get_events_between_dates,
    list_calendar_events,
    list_calendars,
    remove_calendar_sharing,
    respond_to_event,
    share_calendar,
    update_event,
    update_recurring_event,
)
from integrations.o365.calendar import get_attachments as get_attachments_calendar
from integrations.o365.contacts import (
    create_contact,
    delete_contact,
    get_contact_details,
    list_contacts,
)
from integrations.o365.excel import (
    add_row_to_table,
    create_chart,
    create_worksheet,
    delete_chart,
    delete_table,
    delete_worksheet,
    get_chart,
    get_table_range,
    get_worksheet,
    list_charts,
    list_tables,
    list_worksheets,
    read_range,
    update_range,
)
from integrations.o365.excel import create_table as create_table_excel
from integrations.o365.onedrive import (
    copy_drive_item,
    create_folder,
    create_sharing_link,
    delete_item,
    download_file,
    get_drive_item,
    invite_to_drive_item,
    list_drive_items,
    move_drive_item,
    update_drive_item,
    upload_file,
)
from integrations.o365.onenote import (
    create_page_in_section,
    create_page_with_attachment,
    get_page_content,
    list_notebooks,
    list_pages_in_section,
    list_sections_in_notebook,
)
from integrations.o365.outlook import (
    add_attachment,
    create_draft,
    delete_attachment,
    delete_message,
    download_attachment,
    forward_message,
    get_folder_details,
    get_message_details,
    list_folders,
    list_messages,
    move_message,
    reply_all_message,
    reply_to_message,
    search_messages,
    send_draft,
    send_mail,
    update_message,
)
from integrations.o365.outlook import get_attachments as get_attachments_outlook
from integrations.o365.planner import (
    create_task,
    delete_task,
    list_buckets_in_plan,
    list_plans_in_group,
    list_tasks_in_plan,
    update_task,
)
from integrations.o365.sharepoint import (
    create_list_item,
    delete_list_item,
    get_list_items,
    get_site_by_path,
    list_site_lists,
    list_sites,
    update_list_item,
    delete_list_item,
    list_document_libraries,
    list_library_files,
    get_file_download_url,
    get_drive_item_metadata,
    upload_file_to_library,
    get_all_library_files_recursively,
)
from integrations.o365.teams import (
    create_channel,
    get_chat_messages,
    list_channels,
    list_teams,
    schedule_meeting,
    send_channel_message,
)
from integrations.o365.user_groups import (
    create_group,
    delete_group,
    get_group_details,
    get_user_details,
    list_groups,
    list_users,
)
from integrations.o365.word_doc import (
    add_comment,
    apply_formatting,
    create_document,
    create_list,
    delete_document,
    get_document_content,
    get_document_permissions,
    get_document_sections,
    get_document_statistics,
    get_document_versions,
    insert_image,
    insert_page_break,
    insert_section,
    list_documents,
    remove_permission,
    replace_text,
    restore_version,
    search_document,
    set_header_footer,
    share_document,
    update_document_content,
    update_table_cell,
)
from integrations.o365.word_doc import create_table as create_table_word
from integrations.oauth import MissingCredentialsError
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from pycommon.api.ops import api_tool, set_op_type, set_route_data
from service.routes import route_data

set_route_data(route_data)
set_op_type("integration")
from pycommon.authz import validated


def camel_to_snake(name):
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return snake


def fix_data_types(data, func_schema):
    """
    Attempts to fix data types to match the expected schema.
    Returns a copy of the data with type corrections applied.
    """
    
    fixed_data = copy.deepcopy(data)
    
    if not func_schema or "properties" not in func_schema:
        return fixed_data
        
    if "data" not in fixed_data or not isinstance(fixed_data["data"], dict):
        return fixed_data
    
    properties = func_schema["properties"]
    
    for field_name, field_value in fixed_data["data"].items():
        if field_name not in properties:
            continue
            
        expected_type = properties[field_name].get("type")
        if not expected_type:
            continue
            
        try:
            # Skip if already correct type
            if expected_type == "string" and isinstance(field_value, str):
                continue
            elif expected_type == "integer" and isinstance(field_value, int):
                continue
            elif expected_type == "number" and isinstance(field_value, (int, float)):
                continue
            elif expected_type == "boolean" and isinstance(field_value, bool):
                continue
            elif expected_type in ["array", "object"]:
                continue  # Don't attempt to fix complex types
                
            # Attempt type conversion
            if expected_type == "integer":
                if isinstance(field_value, str):
                    try:
                        # Handle negative numbers and standard integer strings
                        if field_value.lstrip('-').isdigit():
                            fixed_data["data"][field_name] = int(field_value)
                    except ValueError:
                        pass
                elif isinstance(field_value, float) and field_value.is_integer():
                    fixed_data["data"][field_name] = int(field_value)
                    
            elif expected_type == "number":
                if isinstance(field_value, str):
                    try:
                        fixed_data["data"][field_name] = float(field_value)
                    except ValueError:
                        pass
                        
            elif expected_type == "boolean":
                if isinstance(field_value, str):
                    if field_value.lower() in ["true", "1", "yes", "on"]:
                        fixed_data["data"][field_name] = True
                    elif field_value.lower() in ["false", "0", "no", "off"]:
                        fixed_data["data"][field_name] = False
                elif isinstance(field_value, (int, float)):
                    fixed_data["data"][field_name] = bool(field_value)
                    
            elif expected_type == "string":
                if not isinstance(field_value, str):
                    fixed_data["data"][field_name] = str(field_value)
                    
        except (ValueError, TypeError, AttributeError):
            # If conversion fails, leave the original value
            continue
    
    return fixed_data


def common_handler(operation, *required_params, **optional_params):
    def handler(current_user, data):
        print("Input Data: ", data["data"])
        try:
            params = {
                camel_to_snake(param): data["data"][param] for param in required_params
            }
            for param in optional_params:
                snake_param = camel_to_snake(param)
                if param in data["data"]:
                    params[snake_param] = data["data"][param]

            params["access_token"] = data["access_token"]
            response = operation(current_user, **params)
            print("Integration Response: ", response)
            return {"success": True, "data": response}
        except MissingCredentialsError as me:
            print("Missing Credentials Error: ", str(me))
            return {"success": False, "error": str(me)}
        except Exception as e:
            print("Error: ", str(e))
            return {"success": False, "error": str(e)}

    return handler


@validated("route", False)
def route_request(event, context, current_user, name, data):
    try:
        # First try to use path-based routing if available
        target_path_string = event.get("path", event.get("rawPath", ""))
        print(f"Route path: {target_path_string}")

        # Check if we have a direct path match in our route_data
        route_info = route_data.get(target_path_string, None)

        if not route_info:
            return {"success": False, "error": "Invalid path"}

        func_schema = route_info["parameters"] or {}

        wrapper_schema = {
            "type": "object",
            "properties": {"data": func_schema},
            "required": ["data"],
        }

        print("Validating request")
        try:
            validate(data, wrapper_schema)
            print("Request data validated")
        except ValidationError as e:
            print("Validation error: ", str(e))
            print("Attempting to fix data types...")
            
            try:
                fixed_data = fix_data_types(data, func_schema)
                validate(fixed_data, wrapper_schema)
                print("Data types fixed and validation successful")
                data = fixed_data
            except (ValidationError, ValueError, TypeError) as fix_error:
                print(f"Type fixing failed: {str(fix_error)}")
                raise ValueError(f"Invalid request: {str(e)}")

        service = "/microsoft/integrations/"
        # If no op parameter, try to extract from the path
        op = None
        if target_path_string.startswith(service):
            op = target_path_string.split(service)[1]
        else:
            return {"success": False, "message": "Invalid path"}

        print("Operation to execute: ", op)

        # Dynamically look up the handler function based on the operation name
        handler_name = f"{op}_handler"
        handler_func = globals().get(handler_name)

        if not handler_func:
            return {
                "success": False,
                "message": f"Invalid operation: {op}. No handler function found for {handler_name}",
            }

        print("Executing handler function...")
        return handler_func(current_user, data)

    except Exception as e:
        import traceback

        return {
            "success": False,
            "message": f"Error processing request: {str(e)}",
            "traceback": traceback.format_exc(),
        }


# ### drive ###
@api_tool(
    path="/microsoft/integrations/list_drive_items",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_read"],
    name="microsoftListDriveItems",
    description="Lists items in the specified OneDrive folder.",
    parameters={
        "type": "object",
        "properties": {
            "folder_id": {
                "type": "string",
                "description": "ID of the folder to list (default: root)",
            },
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Number of items per page (default: 25)",
                "default": 25,
            },
        },
    },
)
def list_drive_items_handler(current_user, data):
    return common_handler(list_drive_items, folder_id="root", page_size=25)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/upload_file",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="microsoftUploadFile",
    description="Uploads a file to OneDrive.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path where to store the file (including filename)",
            },
            "file_content": {"type": "string", "description": "Content to upload"},
            "folder_id": {
                "type": "string",
                "description": "Parent folder ID (default: root)",
                "default": "root",
            },
        },
        "required": ["file_path", "file_content"],
    },
)
def upload_file_handler(current_user, data):
    return common_handler(
        upload_file, file_path=None, file_content=None, folder_id="root"
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/download_file",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_read"],
    name="microsoftDownloadFile",
    description="Downloads a file from OneDrive.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the file to download"}
        },
        "required": ["item_id"],
    },
)
def download_file_handler(current_user, data):
    return common_handler(download_file, item_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="microsoftDeleteItem",
    description="Deletes a file or folder from OneDrive.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the item to delete"}
        },
        "required": ["item_id"],
    },
)
def delete_item_handler(current_user, data):
    return common_handler(delete_item, item_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_drive_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_read"],
    name="microsoftGetDriveItem",
    description="Retrieves metadata for a specific drive item.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the drive item"}
        },
        "required": ["item_id"],
    },
)
def get_drive_item_handler(current_user, data):
    return common_handler(get_drive_item, item_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_folder",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="microsoftCreateFolder",
    description="Creates a new folder in OneDrive.",
    parameters={
        "type": "object",
        "properties": {
            "folder_name": {"type": "string", "description": "Name of the new folder"},
            "parent_folder_id": {
                "type": "string",
                "description": "ID of the parent folder",
                "default": "root",
            },
        },
        "required": ["folder_name"],
    },
)
def create_folder_handler(current_user, data):
    return common_handler(create_folder, folder_name=None, parent_folder_id="root")(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/update_drive_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="microsoftUpdateDriveItem",
    description="Updates metadata for a specific drive item.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the drive item"},
            "updates": {"type": "object", "description": "Dictionary of updates"},
        },
        "required": ["item_id", "updates"],
    },
)
def update_drive_item_handler(current_user, data):
    return common_handler(update_drive_item, item_id=None, updates=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/copy_drive_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="microsoftCopyDriveItem",
    description="Copies a drive item to a new location.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "ID of the drive item to copy",
            },
            "new_name": {
                "type": "string",
                "description": "New name for the copied item",
            },
            "parent_folder_id": {
                "type": "string",
                "description": "Destination parent folder ID",
                "default": "root",
            },
        },
        "required": ["item_id", "new_name"],
    },
)
def copy_drive_item_handler(current_user, data):
    return common_handler(
        copy_drive_item, item_id=None, new_name=None, parent_folder_id="root"
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/move_drive_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="microsoftMoveDriveItem",
    description="Moves a drive item to a different folder.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "ID of the drive item to move",
            },
            "new_parent_id": {
                "type": "string",
                "description": "ID of the new parent folder",
            },
        },
        "required": ["item_id", "new_parent_id"],
    },
)
def move_drive_item_handler(current_user, data):
    return common_handler(move_drive_item, item_id=None, new_parent_id=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/create_sharing_link",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_read"],
    name="microsoftCreateSharingLink",
    description="Creates a sharing link for a drive item.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the drive item"},
            "link_type": {
                "type": "string",
                "description": "Type of link",
                "default": "view",
            },
            "scope": {
                "type": "string",
                "description": "Link scope",
                "default": "anonymous",
            },
        },
        "required": ["item_id"],
    },
)
def create_sharing_link_handler(current_user, data):
    return common_handler(
        create_sharing_link, item_id=None, link_type="view", scope="anonymous"
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/invite_to_drive_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="microsoftInviteToDriveItem",
    description="Invites users to access a drive item.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the drive item"},
            "recipients": {
                "type": "array",
                "items": {"type": "object"},
                "description": "List of recipient objects",
            },
            "message": {
                "type": "string",
                "description": "Invitation message",
                "default": "",
            },
            "require_sign_in": {
                "type": "boolean",
                "description": "Require sign in",
                "default": True,
            },
            "send_invitation": {
                "type": "boolean",
                "description": "Send invitation",
                "default": True,
            },
            "roles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of roles",
                "default": ["read"],
            },
        },
        "required": ["item_id", "recipients"],
    },
)
def invite_to_drive_item_handler(current_user, data):
    return common_handler(
        invite_to_drive_item,
        item_id=None,
        recipients=None,
        message="",
        require_sign_in=True,
        send_invitation=True,
        roles=None,
    )(current_user, data)


### excel ###


@api_tool(
    path="/microsoft/integrations/list_worksheets",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="microsoftListWorksheets",
    description="Lists worksheets in a workbook stored in OneDrive.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            }
        },
        "required": ["item_id"],
    },
)
def list_worksheets_handler(current_user, data):
    return common_handler(list_worksheets, item_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/list_tables",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="microsoftListTables",
    description="Lists tables in a workbook or specific worksheet.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "worksheet_name": {
                "type": "string",
                "description": "Optional worksheet name to filter tables",
            },
        },
        "required": ["item_id"],
    },
)
def list_tables_handler(current_user, data):
    return common_handler(list_tables, item_id=None, worksheet_name=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/add_row_to_table",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="microsoftAddRowToTable",
    description="Adds a row to a table in the workbook.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "table_name": {"type": "string", "description": "Name of the table"},
            "row_values": {
                "type": "array",
                "description": "List of cell values for the new row",
            },
        },
        "required": ["item_id", "table_name", "row_values"],
    },
)
def add_row_to_table_handler(current_user, data):
    return common_handler(
        add_row_to_table, item_id=None, table_name=None, row_values=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/read_range",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="microsoftReadRange",
    description="Reads a range in the given worksheet.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "worksheet_name": {
                "type": "string",
                "description": "Name of the worksheet",
            },
            "address": {
                "type": "string",
                "description": "Range address (e.g., 'A1:C10')",
            },
        },
        "required": ["item_id", "worksheet_name", "address"],
    },
)
def read_range_handler(current_user, data):
    return common_handler(read_range, item_id=None, worksheet_name=None, address=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/update_range",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="microsoftUpdateRange",
    description="Updates a range in the given worksheet.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "worksheet_name": {
                "type": "string",
                "description": "Name of the worksheet",
            },
            "address": {
                "type": "string",
                "description": "Range address (e.g., 'A1:C10')",
            },
            "values": {"type": "array", "description": "2D list of values to update"},
        },
        "required": ["item_id", "worksheet_name", "address", "values"],
    },
)
def update_range_handler(current_user, data):
    return common_handler(
        update_range, item_id=None, worksheet_name=None, address=None, values=None
    )(current_user, data)


### outlook ###


@api_tool(
    path="/microsoft/integrations/list_messages",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="microsoftListMessages",
    description="Lists messages in a specified mail folder with pagination and filtering support.",
    parameters={
        "type": "object",
        "properties": {
            "folder_id": {
                "type": "string",
                "description": "Folder ID or well-known name",
                "default": "Inbox",
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of messages to retrieve",
                "default": 10,
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of messages to skip",
                "default": 0,
            },
            "filter_query": {"type": "string", "description": "OData filter query"},
        },
    },
)
def list_messages_handler(current_user, data):
    return common_handler(
        list_messages, folder_id="Inbox", top=10, skip=0, filter_query=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_message_details",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="microsoftGetMessageDetails",
    description="Gets detailed information about a specific message.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "include_body": {
                "type": "boolean",
                "description": "Whether to include message body",
                "default": True,
            },
        },
        "required": ["message_id"],
    },
)
def get_message_details_handler(current_user, data):
    return common_handler(get_message_details, message_id=None, include_body=True)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/send_mail",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftSendMail",
    description="Sends an email with support for CC, BCC, and importance levels.",
    parameters={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body content"},
            "to_recipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of primary recipient email addresses",
            },
            "cc_recipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of CC recipient email addresses",
            },
            "bcc_recipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of BCC recipient email addresses",
            },
            "importance": {
                "type": "string",
                "enum": ["low", "normal", "high"],
                "description": "Message importance",
                "default": "normal",
            },
        },
        "required": ["subject", "body", "to_recipients"],
    },
)
def send_mail_handler(current_user, data):
    return common_handler(
        send_mail,
        subject=None,
        body=None,
        to_recipients=None,
        cc_recipients=None,
        bcc_recipients=None,
        importance="normal",
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftDeleteMessage",
    description="Deletes a message.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID to delete"}
        },
        "required": ["message_id"],
    },
)
def delete_message_handler(current_user, data):
    return common_handler(delete_message, message_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_attachments",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="microsoftGetAttachments",
    description="Gets attachments for a specific message.",
    parameters={
        "type": "object",
        "properties": {"message_id": {"type": "string", "description": "Message ID"}},
        "required": ["message_id"],
    },
)
def get_attachments_handler(current_user, data):
    return common_handler(get_attachments_outlook, message_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/download_attachment",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="microsoftDownloadAttachment",
    description="Downloads a specific attachment from a message. Files under 7MB return base64 content directly. Larger files return download URLs to avoid API Gateway limits.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "attachment_id": {"type": "string", "description": "Attachment ID"}
        },
        "required": ["message_id", "attachment_id"],
    },
)
def download_attachment_handler(current_user, data):
    return common_handler(download_attachment, message_id=None, attachment_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/list_plans_in_group",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_read"],
    name="microsoftListPlansInGroup",
    description="Retrieves all Planner plans in a specific Microsoft 365 group.",
    parameters={
        "type": "object",
        "properties": {
            "group_id": {"type": "string", "description": "Microsoft 365 Group ID"}
        },
        "required": ["group_id"],
    },
)
def list_plans_in_group_handler(current_user, data):
    return common_handler(list_plans_in_group, group_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/list_buckets_in_plan",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_read"],
    name="microsoftListBucketsInPlan",
    description="Lists all buckets in a plan.",
    parameters={
        "type": "object",
        "properties": {"plan_id": {"type": "string", "description": "Plan ID"}},
        "required": ["plan_id"],
    },
)
def list_buckets_in_plan_handler(current_user, data):
    return common_handler(list_buckets_in_plan, plan_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/list_tasks_in_plan",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_read"],
    name="microsoftListTasksInPlan",
    description="Lists all tasks in a plan with optional detailed information.",
    parameters={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "Plan ID"},
            "include_details": {
                "type": "boolean",
                "description": "Whether to include task details",
                "default": False,
            },
        },
        "required": ["plan_id"],
    },
)
def list_tasks_in_plan_handler(current_user, data):
    return common_handler(list_tasks_in_plan, plan_id=None, include_details=False)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/create_task",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_write"],
    name="microsoftCreateTask",
    description="Creates a new task in Planner.",
    parameters={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "Plan ID"},
            "bucket_id": {"type": "string", "description": "Bucket ID"},
            "title": {"type": "string", "description": "Task title"},
            "assignments": {
                "type": "object",
                "description": "Dict of userId -> assignment details",
            },
            "due_date": {
                "type": "string",
                "description": "Optional due date in ISO format",
            },
            "priority": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Optional priority (1-10, where 10 is highest)",
            },
        },
        "required": ["plan_id", "bucket_id", "title"],
    },
)
def create_task_handler(current_user, data):
    return common_handler(
        create_task,
        plan_id=None,
        bucket_id=None,
        title=None,
        assignments=None,
        due_date=None,
        priority=None,
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/update_task",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_write"],
    name="microsoftUpdateTask",
    description="Updates a task with ETag concurrency control.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID"},
            "e_tag": {"type": "string", "description": "Current ETag of the task"},
            "update_fields": {"type": "object", "description": "Fields to update"},
        },
        "required": ["task_id", "e_tag", "update_fields"],
    },
)
def update_task_handler(current_user, data):
    return common_handler(update_task, task_id=None, e_tag=None, update_fields=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/delete_task",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_write"],
    name="microsoftDeleteTask",
    description="Deletes a task with ETag concurrency control.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID"},
            "e_tag": {"type": "string", "description": "Current ETag of the task"},
        },
        "required": ["task_id", "e_tag"],
    },
)
def delete_task_handler(current_user, data):
    return common_handler(delete_task, task_id=None, e_tag=None)(current_user, data)


### sharepoint ###


@api_tool(
    path="/microsoft/integrations/list_sites",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_read",
    ],
    name="microsoftListSites",
    description="Lists SharePoint sites with search and pagination support.",
    parameters={
        "type": "object",
        "properties": {
            "search_query": {"type": "string", "description": "Optional search term"},
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of sites to retrieve",
                "default": 10,
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of sites to skip",
                "default": 0,
            },
        },
    },
)
def list_sites_handler(current_user, data):
    return common_handler(list_sites, search_query=None, top=10, skip=0)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/get_site_by_path",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_read",
    ],
    name="microsoftGetSiteByPath",
    description="Gets a site by its hostname and optional path.",
    parameters={
        "type": "object",
        "properties": {
            "hostname": {"type": "string", "description": "SharePoint hostname"},
            "site_path": {"type": "string", "description": "Optional site path"},
        },
        "required": ["hostname"],
    },
)
def get_site_by_path_handler(current_user, data):
    return common_handler(get_site_by_path, hostname=None, site_path=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/list_site_lists",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_read",
    ],
    name="microsoftListSiteLists",
    description="Lists SharePoint lists in a site with pagination.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID"},
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of lists to retrieve",
                "default": 10,
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of lists to skip",
                "default": 0,
            },
        },
        "required": ["site_id"],
    },
)
def list_site_lists_handler(current_user, data):
    return common_handler(list_site_lists, site_id=None, top=10, skip=0)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/get_list_items",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_read",
    ],
    name="microsoftGetListItems",
    description="Gets items from a SharePoint list with pagination and filtering.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID"},
            "list_id": {"type": "string", "description": "List ID"},
            "expand_fields": {
                "type": "boolean",
                "description": "Whether to expand field values",
                "default": True,
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of items to retrieve",
                "default": 10,
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of items to skip",
                "default": 0,
            },
            "filter_query": {
                "type": "string",
                "description": "Optional OData filter query",
            },
        },
        "required": ["site_id", "list_id"],
    },
)
def get_list_items_handler(current_user, data):
    return common_handler(
        get_list_items,
        site_id=None,
        list_id=None,
        expand_fields=True,
        top=10,
        skip=0,
        filter_query=None,
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_list_item",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_write",
    ],
    name="microsoftCreateListItem",
    description="Creates a new item in a SharePoint list.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID (required)"},
            "list_id": {"type": "string", "description": "List ID (required)"},
            "fields_dict": {
                "type": "object",
                "description": "Dictionary of field names and values (required)",
            },
        },
        "required": ["site_id", "list_id", "fields_dict"],
    },
)
def create_list_item_handler(current_user, data):
    return common_handler(
        create_list_item, site_id=None, list_id=None, fields_dict=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/update_list_item",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_write",
    ],
    name="microsoftUpdateListItem",
    description="Updates an existing SharePoint list item.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID (required)"},
            "list_id": {"type": "string", "description": "List ID (required)"},
            "item_id": {"type": "string", "description": "Item ID (required)"},
            "fields_dict": {
                "type": "object",
                "description": "Dictionary of field names and values to update (required)",
            },
        },
        "required": ["site_id", "list_id", "item_id", "fields_dict"],
    },
)
def update_list_item_handler(current_user, data):
    return common_handler(
        update_list_item, site_id=None, list_id=None, item_id=None, fields_dict=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_list_item",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_write",
    ],
    name="microsoftDeleteListItem",
    description="Deletes an item from a SharePoint list.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID (required)"},
            "list_id": {"type": "string", "description": "List ID (required)"},
            "item_id": {"type": "string", "description": "Item ID (required)"},
        },
        "required": ["site_id", "list_id", "item_id"],
    },
)
def delete_list_item_handler(current_user, data):
    return common_handler(delete_list_item, site_id=None, list_id=None, item_id=None)(
        current_user, data
    )


### sharepoint document libraries ###


@api_tool(
    path="/microsoft/integrations/list_document_libraries",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_read",
        "microsoft_drive",
    ],
    name="microsoftListDocumentLibraries",
    description="Lists document libraries in a SharePoint site.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID (required)"},
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of libraries to retrieve",
                "default": 25,
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of libraries to skip",
                "default": 0,
            },
        },
        "required": ["site_id"],
    },
)
def list_document_libraries_handler(current_user, data):
    return common_handler(list_document_libraries, site_id=None, top=25, skip=0)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/list_library_files",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_read",
        "microsoft_drive",
    ],
    name="microsoftListLibraryFiles",
    description="Lists files in a SharePoint document library folder.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID (required)"},
            "drive_id": {"type": "string", "description": "Document library (drive) ID (required)"},
            "folder_path": {
                "type": "string",
                "description": "Folder path or 'root' for root folder",
                "default": "root",
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of files to retrieve",
                "default": 100,
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of files to skip",
                "default": 0,
            },
        },
        "required": ["site_id", "drive_id"],
    },
)
def list_library_files_handler(current_user, data):
    return common_handler(
        list_library_files, site_id=None, drive_id=None, folder_path="root", top=100, skip=0
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_sharepoint_file_download_url",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_read",
        "microsoft_drive",
    ],
    name="microsoftGetSharepointFileDownloadUrl",
    description="Gets download URL for a SharePoint file.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID (required)"},
            "drive_id": {"type": "string", "description": "Document library (drive) ID (required)"},
            "item_id": {"type": "string", "description": "File item ID (required)"},
        },
        "required": ["site_id", "drive_id", "item_id"],
    },
)
def get_sharepoint_file_download_url_handler(current_user, data):
    return common_handler(
        get_file_download_url, site_id=None, drive_id=None, item_id=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_sharepoint_drive_item_metadata",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_read",
        "microsoft_drive",
    ],
    name="microsoftGetSharepointDriveItemMetadata",
    description="Gets metadata for a SharePoint drive item.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID (required)"},
            "drive_id": {"type": "string", "description": "Document library (drive) ID (required)"},
            "item_id": {"type": "string", "description": "Item ID (required)"},
        },
        "required": ["site_id", "drive_id", "item_id"],
    },
)
def get_sharepoint_drive_item_metadata_handler(current_user, data):
    return common_handler(
        get_drive_item_metadata, site_id=None, drive_id=None, item_id=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/upload_file_to_sharepoint_library",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_write",
        "microsoft_drive",
    ],
    name="microsoftUploadFileToSharepointLibrary",
    description="Uploads a file to a SharePoint document library.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID (required)"},
            "drive_id": {"type": "string", "description": "Document library (drive) ID (required)"},
            "file_name": {"type": "string", "description": "Name for the uploaded file (required)"},
            "file_content": {
                "type": "string",
                "description": "File content as base64 encoded string (required)",
            },
            "folder_path": {
                "type": "string",
                "description": "Target folder path or 'root' for root folder",
                "default": "root",
            },
        },
        "required": ["site_id", "drive_id", "file_name", "file_content"],
    },
)
def upload_file_to_sharepoint_library_handler(current_user, data):
    return common_handler(
        upload_file_to_library,
        site_id=None,
        drive_id=None,
        file_name=None,
        file_content=None,
        folder_path="root",
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_all_sharepoint_library_files_recursively",
    tags=[
        "default",
        "integration",
        "microsoft_sharepoint",
        "microsoft_sharepoint_read",
        "microsoft_drive",
    ],
    name="microsoftGetAllSharepointLibraryFilesRecursively",
    description="Recursively gets all files from a SharePoint document library folder and subfolders.",
    parameters={
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "description": "Site ID (required)"},
            "drive_id": {"type": "string", "description": "Document library (drive) ID (required)"},
            "folder_path": {
                "type": "string",
                "description": "Starting folder path or 'root'",
                "default": "root",
            },
        },
        "required": ["site_id", "drive_id"],
    },
)
def get_all_sharepoint_library_files_recursively_handler(current_user, data):
    return common_handler(
        get_all_library_files_recursively,
        site_id=None,
        drive_id=None,
        folder_path="root",
    )(current_user, data)


### teams ###


@api_tool(
    path="/microsoft/integrations/list_teams",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_read"],
    name="microsoftListTeams",
    description="Lists teams that the user is a member of.",
    parameters={},
)
def list_teams_handler(current_user, data):
    return common_handler(list_teams)(current_user, data)


@api_tool(
    path="/microsoft/integrations/list_channels",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_read"],
    name="microsoftListChannels",
    description="Lists channels in a team.",
    parameters={
        "type": "object",
        "properties": {
            "team_id": {"type": "string", "description": "Team ID (required)"}
        },
        "required": ["team_id"],
    },
)
def list_channels_handler(current_user, data):
    return common_handler(list_channels, team_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_channel",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_write"],
    name="microsoftCreateChannel",
    description="Creates a new channel in a team.",
    parameters={
        "type": "object",
        "properties": {
            "team_id": {"type": "string", "description": "Team ID (required)"},
            "name": {"type": "string", "description": "Channel name (required)"},
            "description": {
                "type": "string",
                "description": "Channel description (optional)",
            },
        },
        "required": ["team_id", "name"],
    },
)
def create_channel_handler(current_user, data):
    return common_handler(create_channel, team_id=None, name=None, description="")(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/send_channel_message",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_write"],
    name="microsoftSendChannelMessage",
    description="Sends a message to a channel.",
    parameters={
        "type": "object",
        "properties": {
            "team_id": {"type": "string", "description": "Team ID (required)"},
            "channel_id": {"type": "string", "description": "Channel ID (required)"},
            "message": {
                "type": "string",
                "description": "Message content (can include basic HTML) (required)",
            },
            "importance": {
                "type": "string",
                "enum": ["normal", "high", "urgent"],
                "description": "Message importance (optional)",
                "default": "normal",
            },
        },
        "required": ["team_id", "channel_id", "message"],
    },
)
def send_channel_message_handler(current_user, data):
    return common_handler(
        send_channel_message,
        team_id=None,
        channel_id=None,
        message=None,
        importance="normal",
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_chat_messages",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_read"],
    name="microsoftGetChatMessages",
    description="Gets messages from a chat.",
    parameters={
        "type": "object",
        "properties": {
            "chat_id": {"type": "string", "description": "Chat ID (required)"},
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum number of messages to retrieve (optional)",
                "default": 50,
            },
        },
        "required": ["chat_id"],
    },
)
def get_chat_messages_handler(current_user, data):
    return common_handler(get_chat_messages, chat_id=None, top=50)(current_user, data)


@api_tool(
    path="/microsoft/integrations/schedule_meeting",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_write"],
    name="microsoftScheduleMeeting",
    description="Schedules a Teams meeting.",
    parameters={
        "type": "object",
        "properties": {
            "team_id": {"type": "string", "description": "Team ID (required)"},
            "subject": {"type": "string", "description": "Meeting subject (required)"},
            "start_time": {
                "type": "string",
                "description": "Start time in ISO format (required)",
            },
            "end_time": {
                "type": "string",
                "description": "End time in ISO format (required)",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of attendee email addresses (optional)",
            },
        },
        "required": ["team_id", "subject", "start_time", "end_time"],
    },
)
def schedule_meeting_handler(current_user, data):
    return common_handler(
        schedule_meeting,
        team_id=None,
        subject=None,
        start_time=None,
        end_time=None,
        attendees=None,
    )(current_user, data)


### user_groups ###


@api_tool(
    path="/microsoft/integrations/list_users",
    tags=[
        "default",
        "integration",
        "microsoft_user_groups",
        "microsoft_user_groups_read",
    ],
    name="microsoftListUsers",
    description="Lists users with search, pagination and sorting support.",
    parameters={
        "type": "object",
        "properties": {
            "search_query": {"type": "string", "description": "Optional search term"},
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of users to retrieve",
                "default": 10,
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of users to skip",
                "default": 0,
            },
            "order_by": {"type": "string", "description": "Property to sort by"},
        },
    },
)
def list_users_handler(current_user, data):
    return common_handler(list_users, search_query=None, top=10, skip=0, order_by=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/get_user_details",
    tags=[
        "default",
        "integration",
        "microsoft_user_groups",
        "microsoft_user_groups_read",
    ],
    name="microsoftGetUserDetails",
    description="Gets detailed information about a specific user.",
    parameters={
        "type": "object",
        "properties": {"user_id": {"type": "string", "description": "User ID"}},
        "required": ["user_id"],
    },
)
def get_user_details_handler(current_user, data):
    return common_handler(get_user_details, user_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/list_groups",
    tags=[
        "default",
        "integration",
        "microsoft_user_groups",
        "microsoft_user_groups_read",
    ],
    name="microsoftListGroups",
    description="Lists groups with filtering and pagination support.",
    parameters={
        "type": "object",
        "properties": {
            "search_query": {"type": "string", "description": "Optional search term"},
            "group_type": {
                "type": "string",
                "description": "Optional group type filter",
                "enum": ["Unified", "Security"],
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of groups to retrieve",
                "default": 10,
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of groups to skip",
                "default": 0,
            },
        },
    },
)
def list_groups_handler(current_user, data):
    return common_handler(
        list_groups, search_query=None, group_type=None, top=10, skip=0
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_group_details",
    tags=[
        "default",
        "integration",
        "microsoft_user_groups",
        "microsoft_user_groups_read",
    ],
    name="microsoftGetGroupDetails",
    description="Gets detailed information about a specific group.",
    parameters={
        "type": "object",
        "properties": {"group_id": {"type": "string", "description": "Group ID"}},
        "required": ["group_id"],
    },
)
def get_group_details_handler(current_user, data):
    return common_handler(get_group_details, group_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_group",
    tags=[
        "default",
        "integration",
        "microsoft_user_groups",
        "microsoft_user_groups_write",
    ],
    name="microsoftCreateGroup",
    description="Creates a new group.",
    parameters={
        "type": "object",
        "properties": {
            "display_name": {"type": "string", "description": "Group display name"},
            "mail_nickname": {"type": "string", "description": "Mail nickname"},
            "group_type": {
                "type": "string",
                "enum": ["Unified", "Security"],
                "default": "Unified",
                "description": "Group type",
            },
            "description": {
                "type": "string",
                "description": "Optional group description",
            },
            "owners": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of owner user IDs",
            },
            "members": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of member user IDs",
            },
        },
        "required": ["display_name"],
    },
)
def create_group_handler(current_user, data):
    return common_handler(
        create_group,
        display_name=None,
        mail_nickname=None,
        group_type="Unified",
        description=None,
        owners=None,
        members=None,
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_group",
    tags=[
        "default",
        "integration",
        "microsoft_user_groups",
        "microsoft_user_groups_write",
    ],
    name="microsoftDeleteGroup",
    description="Deletes a group.",
    parameters={
        "type": "object",
        "properties": {
            "group_id": {"type": "string", "description": "Group ID to delete"}
        },
        "required": ["group_id"],
    },
)
def delete_group_handler(current_user, data):
    return common_handler(delete_group, group_id=None)(current_user, data)


### onenote ###


@api_tool(
    path="/microsoft/integrations/list_notebooks",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_read"],
    name="microsoftListNotebooks",
    description="Lists user's OneNote notebooks with pagination support.",
    parameters={
        "type": "object",
        "properties": {
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of notebooks to retrieve",
                "default": 10,
            }
        },
    },
)
def list_notebooks_handler(current_user, data):
    return common_handler(list_notebooks, top=10)(current_user, data)


@api_tool(
    path="/microsoft/integrations/list_sections_in_notebook",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_read"],
    name="microsoftListSectionsInNotebook",
    description="Lists sections in a notebook.",
    parameters={
        "type": "object",
        "properties": {"notebook_id": {"type": "string", "description": "Notebook ID"}},
        "required": ["notebook_id"],
    },
)
def list_sections_in_notebook_handler(current_user, data):
    return common_handler(list_sections_in_notebook, notebook_id=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/list_pages_in_section",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_read"],
    name="microsoftListPagesInSection",
    description="Lists pages in a section.",
    parameters={
        "type": "object",
        "properties": {"section_id": {"type": "string", "description": "Section ID"}},
        "required": ["section_id"],
    },
)
def list_pages_in_section_handler(current_user, data):
    return common_handler(list_pages_in_section, section_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_page_in_section",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_write"],
    name="microsoftCreatePageInSection",
    description="Creates a new page in a section.",
    parameters={
        "type": "object",
        "properties": {
            "section_id": {"type": "string", "description": "Section ID"},
            "title": {"type": "string", "description": "Page title"},
            "html_content": {
                "type": "string",
                "description": "HTML content for the page",
            },
        },
        "required": ["section_id", "title", "html_content"],
    },
)
def create_page_in_section_handler(current_user, data):
    return common_handler(
        create_page_in_section, section_id=None, title=None, html_content=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_page_content",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_read"],
    name="microsoftGetPageContent",
    description="Retrieves the HTML content of a page.",
    parameters={
        "type": "object",
        "properties": {"page_id": {"type": "string", "description": "Page ID"}},
        "required": ["page_id"],
    },
)
def get_page_content_handler(current_user, data):
    return common_handler(get_page_content, page_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_page_with_attachment",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_write"],
    name="microsoftCreatePageWithAttachment",
    description="Creates a page with a file attachment.",
    parameters={
        "type": "object",
        "properties": {
            "section_id": {"type": "string", "description": "Section ID"},
            "title": {"type": "string", "description": "Page title"},
            "html_body": {"type": "string", "description": "HTML content for the page"},
            "file_name": {"type": "string", "description": "Name of the attachment"},
            "file_content": {
                "type": "string",
                "description": "File content as base64 encoded string",
            },
            "file_content_type": {
                "type": "string",
                "description": "File MIME type (e.g. 'application/pdf')",
            },
        },
        "required": [
            "section_id",
            "title",
            "html_body",
            "file_name",
            "file_content",
            "file_content_type",
        ],
    },
)
def create_page_with_attachment_handler(current_user, data):
    return common_handler(
        create_page_with_attachment,
        section_id=None,
        title=None,
        html_body=None,
        file_name=None,
        file_content=None,
        file_content_type=None,
    )(current_user, data)

### contacts ###


@api_tool(
    path="/microsoft/integrations/list_contacts",
    tags=["default", "integration", "microsoft_contacts", "microsoft_contacts_read"],
    name="microsoftListContacts",
    description="Retrieve a list of contacts with pagination support.",
    parameters={
        "type": "object",
        "properties": {
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Number of contacts to retrieve per page",
                "default": 10,
            }
        },
    },
)
def list_contacts_handler(current_user, data):
    return common_handler(list_contacts, page_size=10)(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_contact_details",
    tags=["default", "integration", "microsoft_contacts", "microsoft_contacts_read"],
    name="microsoftGetContactDetails",
    description="Get details for a specific contact.",
    parameters={
        "type": "object",
        "properties": {
            "contact_id": {"type": "string", "description": "Contact ID to retrieve"}
        },
        "required": ["contact_id"],
    },
)
def get_contact_details_handler(current_user, data):
    return common_handler(get_contact_details, contact_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_contact",
    tags=["default", "integration", "microsoft_contacts", "microsoft_contacts_write"],
    name="microsoftCreateContact",
    description="Create a new contact.",
    parameters={
        "type": "object",
        "properties": {
            "given_name": {"type": "string", "description": "Contact's first name"},
            "surname": {"type": "string", "description": "Contact's last name"},
            "email_addresses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of email addresses for the contact",
            },
        },
        "required": ["email_addresses"],
    },
)
def create_contact_handler(current_user, data):
    return common_handler(
        create_contact,
        given_name="microsoft",
        surname="microsoft",
        email_addresses=None,
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_contact",
    tags=["default", "integration", "microsoft_contacts", "microsoft_contacts_write"],
    name="microsoftDeleteContact",
    description="Delete a contact.",
    parameters={
        "type": "object",
        "properties": {
            "contact_id": {"type": "string", "description": "Contact ID to delete"}
        },
        "required": ["contact_id"],
    },
)
def delete_contact_handler(current_user, data):
    return common_handler(delete_contact, contact_id=None)(current_user, data)


### calendar ###
@api_tool(
    path="/microsoft/integrations/create_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftCreateEvent",
    description="Creates a new calendar event.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Event title (required)"},
            "start_time": {"type": "string", "description": "Start time (required)"},
            "end_time": {"type": "string", "description": "End time (required)"},
            "description": {
                "type": "string",
                "description": "Event description (optional)",
                "default": "",
            },
            "location": {
                "type": "string",
                "description": "Physical location (optional)",
            },
            "attendees": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["required", "optional"],
                            "default": "required",
                        },
                    },
                },
                "description": "List of attendees (optional)",
            },
            "calendar_id": {"type": "string", "description": "Calendar ID (optional)"},
            "is_online_meeting": {
                "type": "boolean",
                "description": "Create Teams meeting (optional)",
                "default": False,
            },
            "reminder_minutes_before_start": {
                "type": "integer",
                "description": "Reminder time in minutes (optional)",
            },
            "send_invitations": {
                "type": "string",
                "description": "Invitation sending behavior (optional)",
                "enum": ["auto", "send", "none"],
                "default": "auto",
            },
            "time_zone": {
                "type": "string",
                "description": "Time zone in Windows format (optional)",
                "default": "Central Standard Time",
            },
        },
        "required": ["title", "start_time", "end_time"],
    },
)
def create_event_handler(current_user, data):
    return common_handler(
        create_event,
        title=None,
        start_time=None,
        end_time=None,
        description="",
        location=None,
        attendees=None,
        calendar_id=None,
        is_online_meeting=False,
        reminder_minutes_before_start=None,
        send_invitations="auto",
        time_zone="Central Standard Time",
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/update_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftUpdateEvent",
    description="Updates an existing calendar event.",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID to update"},
            "updated_fields": {
                "type": "object",
                "description": "Dictionary of fields to update",
            },
        },
        "required": ["event_id", "updated_fields"],
    },
)
def update_event_handler(current_user, data):
    return common_handler(update_event, event_id=None, updated_fields=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/delete_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftDeleteEvent",
    description="Deletes a calendar event.",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID to delete"}
        },
        "required": ["event_id"],
    },
)
def delete_event_handler(current_user, data):
    return common_handler(delete_event, event_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_event_details",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="microsoftGetEventDetails",
    description="Gets details for a specific calendar event with timezone support.",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID to retrieve"},
            "user_timezone": {
                "type": "string",
                "description": "User's preferred timezone in Windows format (optional)",
                "default": "UTC",
            },
        },
        "required": ["event_id"],
    },
)
def get_event_details_handler(current_user, data):
    return common_handler(get_event_details, event_id=None, user_timezone="UTC")(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/get_events_between_dates",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="microsoftGetEventsBetweenDates",
    description="Retrieves events between two dates with timezone support.",
    parameters={
        "type": "object",
        "properties": {
            "start_dt": {
                "type": "string",
                "description": "Start datetime in ISO format (required)",
            },
            "end_dt": {
                "type": "string",
                "description": "End datetime in ISO format (required)",
            },
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum number of events to retrieve per page (optional)",
                "default": 50,
            },
            "user_timezone": {
                "type": "string",
                "description": "User's preferred timezone in Windows format (e.g., 'Pacific Standard Time', 'Eastern Standard Time') (optional)",
                "default": "UTC",
            },
        },
        "required": ["start_dt", "end_dt"],
    },
)
def get_events_between_dates_handler(current_user, data):
    return common_handler(
        get_events_between_dates,
        start_dt=None,
        end_dt=None,
        page_size=50,
        user_timezone="UTC",
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/update_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftUpdateMessage",
    description="Updates a specific message with provided changes.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "ID of the message"},
            "changes": {"type": "object", "description": "Dictionary of updates"},
        },
        "required": ["message_id", "changes"],
    },
)
def update_message_handler(current_user, data):
    return common_handler(update_message, message_id=None, changes=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/create_draft",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftCreateDraft",
    description="Creates a draft email message.",
    parameters={
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Draft email subject (required)",
            },
            "body": {
                "type": "string",
                "description": "Draft email body content (required)",
            },
            "to_recipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of recipient email addresses (optional)",
            },
            "cc_recipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of CC email addresses (optional)",
            },
            "bcc_recipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of BCC email addresses (optional)",
            },
            "importance": {
                "type": "string",
                "enum": ["low", "normal", "high"],
                "default": "normal",
                "description": "Email importance (optional)",
            },
        },
        "required": ["subject", "body"],
    },
)
def create_draft_handler(current_user, data):
    return common_handler(
        create_draft,
        subject=None,
        body=None,
        to_recipients=None,
        cc_recipients=None,
        bcc_recipients=None,
        importance="normal",
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/send_draft",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftSendDraft",
    description="Sends a draft email message.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Draft message ID"}
        },
        "required": ["message_id"],
    },
)
def send_draft_handler(current_user, data):
    return common_handler(send_draft, message_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/reply_to_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftReplyToMessage",
    description="Replies to a specific message.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "comment": {"type": "string", "description": "Reply content"},
        },
        "required": ["message_id", "comment"],
    },
)
def reply_to_message_handler(current_user, data):
    return common_handler(reply_to_message, message_id=None, comment=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/reply_all_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftReplyAllMessage",
    description="Replies to all recipients of a specific message.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "comment": {"type": "string", "description": "Reply-all content"},
        },
        "required": ["message_id", "comment"],
    },
)
def reply_all_message_handler(current_user, data):
    return common_handler(reply_all_message, message_id=None, comment=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/forward_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftForwardMessage",
    description="Forwards a specific message.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "comment": {
                "type": "string",
                "description": "Forward comment",
                "default": "",
            },
            "to_recipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Recipient email addresses",
            },
        },
        "required": ["message_id", "to_recipients"],
    },
)
def forward_message_handler(current_user, data):
    return common_handler(
        forward_message, message_id=None, comment="", to_recipients=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/move_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftMoveMessage",
    description="Moves a specific message to a different folder.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "destination_folder_id": {
                "type": "string",
                "description": "Destination folder ID",
            },
        },
        "required": ["message_id", "destination_folder_id"],
    },
)
def move_message_handler(current_user, data):
    return common_handler(move_message, message_id=None, destination_folder_id=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/list_folders",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="microsoftListFolders",
    description="Lists all mail folders.",
    parameters={},
)
def list_folders_handler(current_user, data):
    return common_handler(list_folders)(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_folder_details",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="microsoftGetFolderDetails",
    description="Retrieves details of a specific mail folder.",
    parameters={
        "type": "object",
        "properties": {"folder_id": {"type": "string", "description": "Folder ID"}},
        "required": ["folder_id"],
    },
)
def get_folder_details_handler(current_user, data):
    return common_handler(get_folder_details, folder_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/add_attachment",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftAddAttachment",
    description="Adds an attachment to a specific message.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "name": {"type": "string", "description": "Attachment name"},
            "content_type": {"type": "string", "description": "Attachment MIME type"},
            "content_bytes": {
                "type": "string",
                "description": "Base64 encoded content",
            },
            "is_inline": {
                "type": "boolean",
                "description": "Is the attachment inline",
                "default": False,
            },
        },
        "required": ["message_id", "name", "content_type", "content_bytes"],
    },
)
def add_attachment_handler(current_user, data):
    return common_handler(
        add_attachment,
        message_id=None,
        name=None,
        content_type=None,
        content_bytes=None,
        is_inline=False,
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_attachment",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="microsoftDeleteAttachment",
    description="Deletes an attachment from a message.",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "attachment_id": {"type": "string", "description": "Attachment ID"},
        },
        "required": ["message_id", "attachment_id"],
    },
)
def delete_attachment_handler(current_user, data):
    return common_handler(delete_attachment, message_id=None, attachment_id=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/search_messages",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="microsoftSearchMessages",
    description="Searches messages for a given query string. Note: Pagination with skip is not supported in search queries.",
    parameters={
        "type": "object",
        "properties": {
            "search_query": {"type": "string", "description": "Search query"},
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 10,
                "description": "Maximum messages to return",
            },
        },
        "required": ["search_query"],
    },
)
def search_messages_handler(current_user, data):
    return common_handler(search_messages, search_query=None, top=10)(
        current_user, data
    )


# --- Calendar Routes ---


@api_tool(
    path="/microsoft/integrations/list_calendar_events",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="microsoftListCalendarEvents",
    description="Lists events for a given calendar with timezone support.",
    parameters={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "description": "Calendar ID"},
            "user_timezone": {
                "type": "string",
                "description": "User's preferred timezone in Windows format (optional)",
                "default": "UTC",
            },
        },
        "required": ["calendar_id"],
    },
)
def list_calendar_events_handler(current_user, data):
    return common_handler(list_calendar_events, calendar_id=None, user_timezone="UTC")(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/list_calendars",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="microsoftListCalendars",
    description="Lists all calendars in the user's mailbox.",
    parameters={
        "type": "object",
        "properties": {
            "include_shared": {
                "type": "boolean",
                "description": "Whether to include shared calendars as boolean (default: false)",
            }
        },
        "required": [],
    },
)
def list_calendars_handler(current_user, data):
    return common_handler(list_calendars, include_shared=False)(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_calendar",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftCreateCalendar",
    description="Creates a new calendar.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Calendar name"},
            "color": {"type": "string", "description": "Calendar color"},
        },
        "required": ["name"],
    },
)
def create_calendar_handler(current_user, data):
    return common_handler(create_calendar, name=None, color=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_calendar",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftDeleteCalendar",
    description="Deletes a calendar.",
    parameters={
        "type": "object",
        "properties": {"calendar_id": {"type": "string", "description": "Calendar ID"}},
        "required": ["calendar_id"],
    },
)
def delete_calendar_handler(current_user, data):
    return common_handler(delete_calendar, calendar_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/respond_to_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftRespondToEvent",
    description="Responds to an event invitation.",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID"},
            "response_type": {
                "type": "string",
                "description": "Response type",
                "enum": ["accept", "decline", "tentativelyAccept"],
            },
            "comment": {"type": "string", "description": "Optional comment"},
            "send_response": {
                "type": "boolean",
                "description": "Send response email",
                "default": True,
            },
        },
        "required": ["event_id", "response_type"],
    },
)
def respond_to_event_handler(current_user, data):
    return common_handler(
        respond_to_event,
        event_id=None,
        response_type=None,
        comment=None,
        send_response=True,
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/find_meeting_times",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="microsoftFindMeetingTimes",
    description="Finds available meeting times for a set of attendees based on their calendar availability.",
    parameters={
        "type": "object",
        "properties": {
            "attendees": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"email": {"type": "string"}},
                    "required": ["email"],
                },
                "description": "List of attendees",
            },
            "duration_minutes": {
                "type": "integer",
                "description": "Meeting duration",
                "default": 30,
            },
            "start_time": {"type": "string", "description": "Start time boundary"},
            "end_time": {"type": "string", "description": "End time boundary"},
            "time_zone": {
                "type": "string",
                "description": "Time zone in Windows format",
                "default": "Central Standard Time",
            },
            "required_attendees": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"email": {"type": "string"}},
                    "required": ["email"],
                },
                "description": "Required attendees",
            },
            "optional_attendees": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"email": {"type": "string"}},
                    "required": ["email"],
                },
                "description": "Optional attendees",
            },
            "working_hours_start": {
                "type": "string",
                "description": "Start of working hours (HH:MM)",
                "default": "09:00",
            },
            "working_hours_end": {
                "type": "string",
                "description": "End of working hours (HH:MM)",
                "default": "17:00",
            },
            "include_weekends": {
                "type": "boolean",
                "description": "Include weekends in suggestions (optional)",
                "default": False,
            },
            "availability_view_interval": {
                "type": "integer",
                "description": "Interval in minutes for availability",
                "default": 30,
            },
        },
        "required": [],
    },
)
def find_meeting_times_handler(current_user, data):
    return common_handler(
        find_meeting_times,
        attendees=None,
        duration_minutes=30,
        start_time=None,
        end_time=None,
        time_zone="Central Standard Time",
        required_attendees=None,
        optional_attendees=None,
        working_hours_start="09:00",
        working_hours_end="17:00",
        include_weekends=False,
        availability_view_interval=30,
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_recurring_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftCreateRecurringEvent",
    description="Creates a recurring calendar event.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Event title"},
            "start_time": {"type": "string", "description": "Start time"},
            "end_time": {"type": "string", "description": "End time"},
            "description": {"type": "string", "description": "Event description"},
            "recurrence_pattern": {
                "type": "object",
                "description": "Recurrence pattern",
            },
            "time_zone": {
                "type": "string",
                "description": "Time zone in Windows format",
                "default": "Central Standard Time",
            },
        },
        "required": [
            "title",
            "start_time",
            "end_time",
            "description",
            "recurrence_pattern",
        ],
    },
)
def create_recurring_event_handler(current_user, data):
    return common_handler(
        create_recurring_event,
        title=None,
        start_time=None,
        end_time=None,
        description=None,
        recurrence_pattern=None,
        time_zone="Central Standard Time",
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/update_recurring_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftUpdateRecurringEvent",
    description="Updates a recurring calendar event.",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID"},
            "updated_fields": {"type": "object", "description": "Fields to update"},
            "update_type": {
                "type": "string",
                "description": "Update type",
                "default": "series",
            },
        },
        "required": ["event_id", "updated_fields"],
    },
)
def update_recurring_event_handler(current_user, data):
    return common_handler(
        update_recurring_event, event_id=None, updated_fields=None, update_type="series"
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/calendar_add_attachment",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftCalendarAddAttachment",
    description="Adds an attachment to a calendar event.",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID"},
            "file_name": {"type": "string", "description": "File name"},
            "content_bytes": {
                "type": "string",
                "description": "Base64 encoded file content",
            },
            "content_type": {"type": "string", "description": "MIME type"},
        },
        "required": ["event_id", "file_name", "content_bytes", "content_type"],
    },
)
def calendar_add_attachment_handler(current_user, data):
    return common_handler(
        add_attachment,
        event_id=None,
        file_name=None,
        content_bytes=None,
        content_type=None,
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_event_attachments",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="microsoftGetEventAttachments",
    description="Retrieves attachments for a calendar event.",
    parameters={
        "type": "object",
        "properties": {"event_id": {"type": "string", "description": "Event ID"}},
        "required": ["event_id"],
    },
)
def get_event_attachments_handler(current_user, data):
    return common_handler(get_attachments_calendar, event_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_event_attachment",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftDeleteEventAttachment",
    description="Deletes an attachment from a calendar event.",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID"},
            "attachment_id": {"type": "string", "description": "Attachment ID"},
        },
        "required": ["event_id", "attachment_id"],
    },
)
def delete_event_attachment_handler(current_user, data):
    return common_handler(delete_attachment, event_id=None, attachment_id=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/get_calendar_permissions",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="microsoftGetCalendarPermissions",
    description="Gets sharing permissions for a calendar.",
    parameters={
        "type": "object",
        "properties": {"calendar_id": {"type": "string", "description": "Calendar ID"}},
        "required": ["calendar_id"],
    },
)
def get_calendar_permissions_handler(current_user, data):
    return common_handler(get_calendar_permissions, calendar_id=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/share_calendar",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftShareCalendar",
    description="Shares a calendar with another user. Uses Microsoft Graph API calendar permission roles.",
    parameters={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "description": "Calendar ID"},
            "user_email": {"type": "string", "description": "User email"},
            "role": {
                "type": "string",
                "description": "Permission level: freeBusyRead (free/busy only), limitedRead (free/busy + subject/location), read (all event details)",
                "default": "read",
                "enum": ["freeBusyRead", "limitedRead", "read"],
            },
        },
        "required": ["calendar_id", "user_email"],
    },
)
def share_calendar_handler(current_user, data):
    return common_handler(
        share_calendar, calendar_id=None, user_email=None, role="read"
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/remove_calendar_sharing",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="microsoftRemoveCalendarSharing",
    description="Removes sharing permissions from a calendar.",
    parameters={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "description": "Calendar ID"},
            "permission_id": {"type": "string", "description": "Permission ID"},
        },
        "required": ["calendar_id", "permission_id"],
    },
)
def remove_calendar_sharing_handler(current_user, data):
    return common_handler(
        remove_calendar_sharing, calendar_id=None, permission_id=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/check_event_conflicts",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="microsoftCheckEventConflicts",
    description="Checks for scheduling conflicts within a time window across one or more calendars.",
    parameters={
        "type": "object",
        "properties": {
            "proposed_start_time": {
                "type": "string",
                "description": "Start time in ISO format (required)",
            },
            "proposed_end_time": {
                "type": "string",
                "description": "End time in ISO format (required)",
            },
            "return_conflicting_events": {
                "type": "boolean",
                "description": "Include conflict details (optional)",
                "default": False,
            },
            "calendar_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Calendar IDs to check (optional)",
            },
            "check_all_calendars": {
                "type": "boolean",
                "description": "Check all available calendars (optional)",
                "default": False,
            },
            "time_zone": {
                "type": "string",
                "description": "Time zone in Windows format (optional)",
                "default": "Central Standard Time",
            },
        },
        "required": ["proposed_start_time", "proposed_end_time"],
    },
)
def check_event_conflicts_handler(current_user, data):
    return common_handler(
        check_event_conflicts,
        proposed_start_time=None,
        proposed_end_time=None,
        return_conflicting_events=False,
        calendar_ids=None,
        check_all_calendars=False,
        time_zone="Central Standard Time",
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_worksheet",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="microsoftGetWorksheet",
    description="Retrieves details of a specific worksheet.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "worksheet_name": {"type": "string", "description": "Worksheet name"},
        },
        "required": ["item_id", "worksheet_name"],
    },
)
def get_worksheet_handler(current_user, data):
    return common_handler(get_worksheet, item_id=None, worksheet_name=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/create_worksheet",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="microsoftCreateWorksheet",
    description="Creates a new worksheet in the workbook.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "name": {"type": "string", "description": "Name of the new worksheet"},
        },
        "required": ["item_id", "name"],
    },
)
def create_worksheet_handler(current_user, data):
    return common_handler(create_worksheet, item_id=None, name=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_worksheet",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="microsoftDeleteWorksheet",
    description="Deletes an existing worksheet in the workbook.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "worksheet_name": {"type": "string", "description": "Worksheet name"},
        },
        "required": ["item_id", "worksheet_name"],
    },
)
def delete_worksheet_handler(current_user, data):
    return common_handler(delete_worksheet, item_id=None, worksheet_name=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/create_table_excel",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="microsoftCreateTableExcel",
    description="Creates a new table in the specified worksheet.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "worksheet_name": {"type": "string", "description": "Worksheet name"},
            "address": {
                "type": "string",
                "description": "Range address for the table (e.g., 'A1:D4')",
            },
            "has_headers": {
                "type": "boolean",
                "default": True,
                "description": "Specifies if the table has headers",
            },
        },
        "required": ["item_id", "worksheet_name", "address"],
    },
)
def create_table_excel_handler(current_user, data):
    return common_handler(
        create_table_excel,
        item_id=None,
        worksheet_name=None,
        address=None,
        has_headers=True,
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_table",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="microsoftDeleteTable",
    description="Deletes an existing table from the workbook.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "table_id": {"type": "string", "description": "Identifier of the table"},
        },
        "required": ["item_id", "table_id"],
    },
)
def delete_table_handler(current_user, data):
    return common_handler(delete_table, item_id=None, table_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_table_range",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="microsoftGetTableRange",
    description="Retrieves the data range of a specified table.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "table_id": {"type": "string", "description": "Identifier of the table"},
        },
        "required": ["item_id", "table_id"],
    },
)
def get_table_range_handler(current_user, data):
    return common_handler(get_table_range, item_id=None, table_id=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/list_charts",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="microsoftListCharts",
    description="Lists all charts in a specified worksheet.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "worksheet_name": {"type": "string", "description": "Worksheet name"},
        },
        "required": ["item_id", "worksheet_name"],
    },
)
def list_charts_handler(current_user, data):
    return common_handler(list_charts, item_id=None, worksheet_name=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/get_chart",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="microsoftGetChart",
    description="Retrieves details for a specific chart.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "worksheet_name": {"type": "string", "description": "Worksheet name"},
            "chart_name": {"type": "string", "description": "Chart name"},
        },
        "required": ["item_id", "worksheet_name", "chart_name"],
    },
)
def get_chart_handler(current_user, data):
    return common_handler(
        get_chart, item_id=None, worksheet_name=None, chart_name=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_chart",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="microsoftCreateChart",
    description="Creates a new chart in a worksheet.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "worksheet_name": {"type": "string", "description": "Worksheet name"},
            "chart_type": {"type": "string", "description": "Chart type"},
            "source_range": {
                "type": "string",
                "description": "Range address for the data",
            },
            "series_by": {"type": "string", "description": "How series are grouped"},
            "title": {
                "type": "string",
                "description": "Optional chart title",
                "default": "",
            },
        },
        "required": [
            "item_id",
            "worksheet_name",
            "chart_type",
            "source_range",
            "series_by",
        ],
    },
)
def create_chart_handler(current_user, data):
    return common_handler(
        create_chart,
        item_id=None,
        worksheet_name=None,
        chart_type=None,
        source_range=None,
        series_by=None,
        title="",
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/delete_chart",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="microsoftDeleteChart",
    description="Deletes a chart from a worksheet.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook",
            },
            "worksheet_name": {"type": "string", "description": "Worksheet name"},
            "chart_name": {"type": "string", "description": "Chart name"},
        },
        "required": ["item_id", "worksheet_name", "chart_name"],
    },
)
def delete_chart_handler(current_user, data):
    return common_handler(
        delete_chart, item_id=None, worksheet_name=None, chart_name=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/add_comment",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftAddComment",
    description="Adds a comment to a specific range in a Word document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "text": {"type": "string", "description": "Comment text"},
            "content_range": {"type": "object", "description": "Range to comment on"},
        },
        "required": ["document_id", "text", "content_range"],
    },
)
def add_comment_handler(current_user, data):
    return common_handler(add_comment, document_id=None, text=None, content_range=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/get_document_statistics",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="microsoftGetDocumentStatistics",
    description="Gets document statistics including word count, page count, etc.",
    parameters={
        "type": "object",
        "properties": {"document_id": {"type": "string", "description": "Document ID"}},
        "required": ["document_id"],
    },
)
def get_document_statistics_handler(current_user, data):
    return common_handler(get_document_statistics, document_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/search_document",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="microsoftSearchDocument",
    description="Searches for text within a document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "search_text": {"type": "string", "description": "Text to search for"},
        },
        "required": ["document_id", "search_text"],
    },
)
def search_document_handler(current_user, data):
    return common_handler(search_document, document_id=None, search_text=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/apply_formatting",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftApplyFormatting",
    description="Applies formatting to a specific range in the document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "format_range": {"type": "object", "description": "Range to format"},
            "formatting": {"type": "object", "description": "Formatting options"},
        },
        "required": ["document_id", "format_range", "formatting"],
    },
)
def apply_formatting_handler(current_user, data):
    return common_handler(
        apply_formatting, document_id=None, format_range=None, formatting=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_document_sections",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="microsoftGetDocumentSections",
    description="Gets all sections/paragraphs in a document.",
    parameters={
        "type": "object",
        "properties": {"document_id": {"type": "string", "description": "Document ID"}},
        "required": ["document_id"],
    },
)
def get_document_sections_handler(current_user, data):
    return common_handler(get_document_sections, document_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/insert_section",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftInsertSection",
    description="Inserts a new section/paragraph at a specific position in the document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "content": {"type": "string", "description": "Content to insert"},
            "position": {"type": "integer", "description": "Position to insert at"},
        },
        "required": ["document_id", "content"],
    },
)
def insert_section_handler(current_user, data):
    return common_handler(
        insert_section, document_id=None, content=None, position=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/replace_text",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftReplaceText",
    description="Replaces all occurrences of text in a document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "search_text": {"type": "string", "description": "Text to find"},
            "replace_text": {"type": "string", "description": "Text to replace with"},
        },
        "required": ["document_id", "search_text", "replace_text"],
    },
)
def replace_text_handler(current_user, data):
    return common_handler(
        replace_text, document_id=None, search_text=None, replace_text=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_table_word",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftCreateTableWord",
    description="Inserts a new table into the document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "rows": {"type": "integer", "description": "Number of rows"},
            "columns": {"type": "integer", "description": "Number of columns"},
            "position": {"type": "object", "description": "Position to insert table"},
        },
        "required": ["document_id", "rows", "columns"],
    },
)
def create_table_word_handler(current_user, data):
    return common_handler(
        create_table_word, document_id=None, rows=None, columns=None, position=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/update_table_cell",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftUpdateTableCell",
    description="Updates content and formatting of a table cell.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "table_id": {"type": "string", "description": "ID of the table"},
            "row": {"type": "integer", "description": "Row index"},
            "column": {"type": "integer", "description": "Column index"},
            "content": {"type": "string", "description": "Cell content"},
            "formatting": {"type": "object", "description": "Cell formatting"},
        },
        "required": ["document_id", "table_id", "row", "column", "content"],
    },
)
def update_table_cell_handler(current_user, data):
    return common_handler(
        update_table_cell,
        document_id=None,
        table_id=None,
        row=None,
        column=None,
        content=None,
        formatting=None,
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/create_list",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftCreateList",
    description="Creates a bulleted or numbered list in the document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of items to add",
            },
            "list_type": {
                "type": "string",
                "description": "Type of list",
                "default": "bullet",
            },
            "position": {"type": "object", "description": "Position to insert list"},
        },
        "required": ["document_id", "items"],
    },
)
def create_list_handler(current_user, data):
    return common_handler(
        create_list, document_id=None, items=None, list_type="bullet", position=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/insert_page_break",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftInsertPageBreak",
    description="Inserts a page break at the specified position.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "position": {
                "type": "object",
                "description": "Position to insert page break",
            },
        },
        "required": ["document_id"],
    },
)
def insert_page_break_handler(current_user, data):
    return common_handler(insert_page_break, document_id=None, position=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/set_header_footer",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftSetHeaderFooter",
    description="Sets the header or footer content for the document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "content": {"type": "string", "description": "Content to set"},
            "is_header": {
                "type": "boolean",
                "description": "True for header, False for footer",
                "default": True,
            },
        },
        "required": ["document_id", "content"],
    },
)
def set_header_footer_handler(current_user, data):
    return common_handler(
        set_header_footer, document_id=None, content=None, is_header=True
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/insert_image",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftInsertImage",
    description="Inserts an image into the document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "image_data": {"type": "string", "description": "Image bytes"},
            "position": {"type": "object", "description": "Position to insert image"},
            "name": {"type": "string", "description": "Image name"},
        },
        "required": ["document_id", "image_data"],
    },
)
def insert_image_handler(current_user, data):
    return common_handler(
        insert_image, document_id=None, image_data=None, position=None, name=None
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_document_versions",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="microsoftGetDocumentVersions",
    description="Gets version history of a document.",
    parameters={
        "type": "object",
        "properties": {"document_id": {"type": "string", "description": "Document ID"}},
        "required": ["document_id"],
    },
)
def get_document_versions_handler(current_user, data):
    return common_handler(get_document_versions, document_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/restore_version",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftRestoreVersion",
    description="Restores a previous version of a document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "version_id": {"type": "string", "description": "Version ID to restore"},
        },
        "required": ["document_id", "version_id"],
    },
)
def restore_version_handler(current_user, data):
    return common_handler(restore_version, document_id=None, version_id=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/delete_document",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftDeleteDocument",
    description="Deletes a Word document.",
    parameters={
        "type": "object",
        "properties": {"document_id": {"type": "string", "description": "Document ID"}},
        "required": ["document_id"],
    },
)
def delete_document_handler(current_user, data):
    return common_handler(delete_document, document_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/list_documents",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="microsoftListDocuments",
    description="Lists Word documents in a folder or root.",
    parameters={
        "type": "object",
        "properties": {"folder_path": {"type": "string", "description": "Folder path"}},
    },
)
def list_documents_handler(current_user, data):
    return common_handler(list_documents, folder_path=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/share_document",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftShareDocument",
    description="Shares a Word document with another user.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "user_email": {
                "type": "string",
                "description": "Email of the user to share with",
            },
            "permission_level": {
                "type": "string",
                "description": "Permission level",
                "default": "read",
            },
        },
        "required": ["document_id", "user_email"],
    },
)
def share_document_handler(current_user, data):
    return common_handler(
        share_document, document_id=None, user_email=None, permission_level="read"
    )(current_user, data)


@api_tool(
    path="/microsoft/integrations/get_document_permissions",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="microsoftGetDocumentPermissions",
    description="Gets sharing permissions for a document.",
    parameters={
        "type": "object",
        "properties": {"document_id": {"type": "string", "description": "Document ID"}},
        "required": ["document_id"],
    },
)
def get_document_permissions_handler(current_user, data):
    return common_handler(get_document_permissions, document_id=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/remove_permission",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftRemovePermission",
    description="Removes a sharing permission from a document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "permission_id": {
                "type": "string",
                "description": "Permission ID to remove",
            },
        },
        "required": ["document_id", "permission_id"],
    },
)
def remove_permission_handler(current_user, data):
    return common_handler(remove_permission, document_id=None, permission_id=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/get_document_content",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="microsoftGetDocumentContent",
    description="Gets the content of a Word document.",
    parameters={
        "type": "object",
        "properties": {"document_id": {"type": "string", "description": "Document ID"}},
        "required": ["document_id"],
    },
)
def get_document_content_handler(current_user, data):
    return common_handler(get_document_content, document_id=None)(current_user, data)


@api_tool(
    path="/microsoft/integrations/update_document_content",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftUpdateDocumentContent",
    description="Updates the content of a Word document.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "content": {"type": "string", "description": "New content"},
        },
        "required": ["document_id", "content"],
    },
)
def update_document_content_handler(current_user, data):
    return common_handler(update_document_content, document_id=None, content=None)(
        current_user, data
    )


@api_tool(
    path="/microsoft/integrations/create_document",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="microsoftCreateDocument",
    description="Creates a new Word document.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the document"},
            "content": {"type": "string", "description": "Initial content"},
            "folder_path": {"type": "string", "description": "Folder path"},
        },
        "required": ["name"],
    },
)
def create_document_handler(current_user, data):
    return common_handler(create_document, name=None, content=None, folder_path=None)(
        current_user, data
    )
