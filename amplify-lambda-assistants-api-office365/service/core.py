from common.ops import vop, op
from common.validate import validated
from integrations.oauth import MissingCredentialsError

from integrations.o365.onedrive import list_drive_items, upload_file, download_file, delete_item, get_drive_item, create_folder, update_drive_item, copy_drive_item, move_drive_item, create_sharing_link, invite_to_drive_item
from integrations.o365.excel import list_worksheets, list_tables, add_row_to_table, read_range, update_range, get_worksheet, create_worksheet, delete_worksheet, create_table, delete_table, get_table_range, list_charts, get_chart, create_chart, delete_chart
from integrations.o365.outlook import list_messages, get_message_details, send_mail, delete_message, get_attachments, update_message, create_draft, send_draft, reply_to_message, reply_all_message, forward_message, move_message, list_folders, get_folder_details, add_attachment, delete_attachment, search_messages

from integrations.o365.planner import list_plans_in_group, list_buckets_in_plan, list_tasks_in_plan, create_task, update_task, delete_task

from integrations.o365.sharepoint import list_sites, get_site_by_path, list_site_lists, get_list_items, create_list_item, update_list_item, delete_list_item

from integrations.o365.teams import list_teams, list_channels, create_channel, send_channel_message, get_chat_messages, schedule_meeting

from integrations.o365.user_groups import list_users, get_user_details, list_groups, get_group_details, create_group, delete_group

from integrations.o365.onenote import list_notebooks, list_sections_in_notebook, list_pages_in_section, create_page_in_section, get_page_content, create_page_with_image_and_attachment

from integrations.o365.contacts import list_contacts, get_contact_details, create_contact, delete_contact

from integrations.o365.calendar import create_event, update_event, delete_event, get_event_details, get_events_between_dates, list_calendar_events, list_calendars, create_calendar, delete_calendar, respond_to_event, find_meeting_times, create_recurring_event, update_recurring_event, add_attachment, get_attachments, delete_attachment, get_calendar_permissions, share_calendar, remove_calendar_sharing

from integrations.o365.word_doc import (
    add_comment, get_document_statistics, search_document, apply_formatting, get_document_sections,
    insert_section, replace_text, create_table, update_table_cell, create_list, insert_page_break,
    set_header_footer, insert_image, get_document_versions, restore_version, delete_document,
    list_documents, share_document, get_document_permissions, remove_permission, get_document_content,
    update_document_content, create_document
)

import re




def camel_to_snake(name):
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
    return snake

def common_handler(operation, *required_params, **optional_params):
    def handler(current_user, data):
        try:
            params = {camel_to_snake(param): data['data'][param] for param in required_params}
            params.update({camel_to_snake(param): data['data'].get(param) for param in optional_params})
            params['access_token'] = data['access_token']
            response = operation(current_user, **params)
            return {"success": True, "data": response}
        except MissingCredentialsError as me:
            return {"success": False, "error": str(me)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return handler


@validated("route")
def route_request(event, context, current_user, name, data):
    query_params = event.get('queryStringParameters', {})
    print("Query params: ", query_params)
    op = query_params.get('op', '')
    if not op:
        return {
            'success': False,
            'message': 'Invalid or missing op query parameter'
        }
    
    print("op: ", op)
    print("data: ", data['data'])

    match op:
        case "list_drive_items":
            return list_drive_items_handler(current_user, data)
        case "upload_file":
            return upload_file_handler(current_user, data)
        case "download_file":
            return download_file_handler(current_user, data)
        case "delete_item":
            return delete_item_handler(current_user, data)
        case "list_worksheets":
            return list_worksheets_handler(current_user, data)
        case "list_tables":
            return list_tables_handler(current_user, data)
        case "add_row_to_table":
            return add_row_to_table_handler(current_user, data)
        case "read_range":
            return read_range_handler(current_user, data)
        case "update_range":
            return update_range_handler(current_user, data)
        case "list_messages":
            return list_messages_handler(current_user, data)
        case "get_message_details":
            return get_message_details_handler(current_user, data)
        case "send_mail":
            return send_mail_handler(current_user, data)
        case "delete_message":
            return delete_message_handler(current_user, data)
        case "get_attachments":
            return get_attachments_handler(current_user, data)
        case "update_message":
            return update_message_handler(current_user, data)
        case "create_draft":
            return create_draft_handler(current_user, data)
        case "send_draft":
            return send_draft_handler(current_user, data)
        case "reply_to_message":
            return reply_to_message_handler(current_user, data)
        case "reply_all_message":
            return reply_all_message_handler(current_user, data)
        case "forward_message":
            return forward_message_handler(current_user, data)
        case "move_message":
            return move_message_handler(current_user, data)
        case "list_folders":
            return list_folders_handler(current_user, data)
        case "get_folder_details":
            return get_folder_details_handler(current_user, data)
        case "add_attachment":
            return add_attachment_handler(current_user, data)
        case "delete_attachment":
            return delete_attachment_handler(current_user, data)
        case "search_messages":
            return search_messages_handler(current_user, data)
        case "list_plans_in_group":
            return list_plans_in_group_handler(current_user, data)
        case "list_buckets_in_plan":
            return list_buckets_in_plan_handler(current_user, data)
        case "list_tasks_in_plan":
            return list_tasks_in_plan_handler(current_user, data)
        case "create_task":
            return create_task_handler(current_user, data)
        case "update_task":
            return update_task_handler(current_user, data)
        case "delete_task":
            return delete_task_handler(current_user, data)
        case "list_sites":
            return list_sites_handler(current_user, data)
        case "get_site_by_path":
            return get_site_by_path_handler(current_user, data)
        case "list_site_lists":
            return list_site_lists_handler(current_user, data)
        case "get_list_items":
            return get_list_items_handler(current_user, data)
        case "create_list_item":
            return create_list_item_handler(current_user, data)
        case "update_list_item":
            return update_list_item_handler(current_user, data)
        case "delete_list_item":
            return delete_list_item_handler(current_user, data)
        case "list_teams":
            return list_teams_handler(current_user, data)
        case "list_channels":
            return list_channels_handler(current_user, data)
        case "create_channel":
            return create_channel_handler(current_user, data)
        case "send_channel_message":
            return send_channel_message_handler(current_user, data)
        case "get_chat_messages":
            return get_chat_messages_handler(current_user, data)
        case "schedule_meeting":
            return schedule_meeting_handler(current_user, data)
        case "list_users":
            return list_users_handler(current_user, data)
        case "get_user_details":
            return get_user_details_handler(current_user, data)
        case "list_groups":
            return list_groups_handler(current_user, data)
        case "get_group_details":
            return get_group_details_handler(current_user, data)
        case "create_group":
            return create_group_handler(current_user, data)
        case "delete_group":
            return delete_group_handler(current_user, data)
        case "list_notebooks":
            return list_notebooks_handler(current_user, data)
        case "list_sections_in_notebook":
            return list_sections_in_notebook_handler(current_user, data)
        case "list_pages_in_section":
            return list_pages_in_section_handler(current_user, data)
        case "create_page_in_section":
            return create_page_in_section_handler(current_user, data)
        case "get_page_content":
            return get_page_content_handler(current_user, data)
        case "create_page_with_image_and_attachment":
            return create_page_with_attachments_handler(current_user, data)
        case "list_contacts":
            return list_contacts_handler(current_user, data)
        case "get_contact_details":
            return get_contact_details_handler(current_user, data)
        case "create_contact":
            return create_contact_handler(current_user, data)
        case "delete_contact":
            return delete_contact_handler(current_user, data)
        case "create_event":
            return create_event_handler(current_user, data)
        case "update_event":
            return update_event_handler(current_user, data)
        case "delete_event":
            return delete_event_handler(current_user, data)
        case "get_event_details":
            return get_event_details_handler(current_user, data)
        case "get_events_between_dates":
            return get_events_between_dates_handler(current_user, data)
        case "list_calendar_events":
            return list_calendar_events_handler(current_user, data)
        case "list_calendars":
            return list_calendars_handler(current_user, data)
        case "create_calendar":
            return create_calendar_handler(current_user, data)
        case "delete_calendar":
            return delete_calendar_handler(current_user, data)
        case "respond_to_event":
            return respond_to_event_handler(current_user, data)
        case "find_meeting_times":
            return find_meeting_times_handler(current_user, data)
        case "create_recurring_event":
            return create_recurring_event_handler(current_user, data)
        case "update_recurring_event":
            return update_recurring_event_handler(current_user, data)
        case "calendar_add_attachment":
            return calendar_add_attachment_handler(current_user, data)
        case "get_event_attachments":
            return get_event_attachments_handler(current_user, data)
        case "delete_event_attachment":
            return delete_event_attachment_handler(current_user, data)
        case "get_calendar_permissions":
            return get_calendar_permissions_handler(current_user, data)
        case "share_calendar":
            return share_calendar_handler(current_user, data)
        case "remove_calendar_sharing":
            return remove_calendar_sharing_handler(current_user, data)
        case "get_worksheet":
            return get_worksheet_handler(current_user, data)
        case "create_worksheet":
            return create_worksheet_handler(current_user, data)
        case "delete_worksheet":
            return delete_worksheet_handler(current_user, data)
        case "create_table":
            return create_table_handler(current_user, data)
        case "delete_table":
            return delete_table_handler(current_user, data)
        case "get_table_range":
            return get_table_range_handler(current_user, data)
        case "list_charts":
            return list_charts_handler(current_user, data)
        case "get_chart":
            return get_chart_handler(current_user, data)
        case "create_chart":
            return create_chart_handler(current_user, data)
        case "delete_chart":
            return delete_chart_handler(current_user, data)
        case "get_drive_item":
            return get_drive_item_handler(current_user, data)
        case "create_folder":
            return create_folder_handler(current_user, data)
        case "update_drive_item":
            return update_drive_item_handler(current_user, data)
        case "copy_drive_item":
            return copy_drive_item_handler(current_user, data)
        case "move_drive_item":
            return move_drive_item_handler(current_user, data)
        case "create_sharing_link":
            return create_sharing_link_handler(current_user, data)
        case "invite_to_drive_item":
            return invite_to_drive_item_handler(current_user, data)
        case "add_comment":
            return add_comment_handler(current_user, data)
        case "get_document_statistics":
            return get_document_statistics_handler(current_user, data)
        case "search_document":
            return search_document_handler(current_user, data)
        case "apply_formatting":
            return apply_formatting_handler(current_user, data)
        case "get_document_sections":
            return get_document_sections_handler(current_user, data)
        case "insert_section":
            return insert_section_handler(current_user, data)
        case "replace_text":
            return replace_text_handler(current_user, data)
        case "create_table":
            return create_table_handler(current_user, data)
        case "update_table_cell":
            return update_table_cell_handler(current_user, data)
        case "create_list":
            return create_list_handler(current_user, data)
        case "insert_page_break":
            return insert_page_break_handler(current_user, data)
        case "set_header_footer":
            return set_header_footer_handler(current_user, data)
        case "insert_image":
            return insert_image_handler(current_user, data)
        case "get_document_versions":
            return get_document_versions_handler(current_user, data)
        case "restore_version":
            return restore_version_handler(current_user, data)
        case "delete_document":
            return delete_document_handler(current_user, data)
        case "list_documents":
            return list_documents_handler(current_user, data)
        case "share_document":
            return share_document_handler(current_user, data)
        case "get_document_permissions":
            return get_document_permissions_handler(current_user, data)
        case "remove_permission":
            return remove_permission_handler(current_user, data)
        case "get_document_content":
            return get_document_content_handler(current_user, data)
        case "update_document_content":
            return update_document_content_handler(current_user, data)
        case "create_document":
            return create_document_handler(current_user, data)

        case _:
            return {"success": False, "message": f"Operation {op} is not supported."}


### drive ###
@vop(
    path="/microsoft/integrations/route?op=list_drive_items",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_read"],
    name="listDriveItems",
    description="Lists items in the specified OneDrive folder.",
    params={
        "folder_id": "ID of the folder to list (default: root)",
        "page_size": "Number of items per page (default: 25)"
    },
    parameters={
        "type": "object",
        "properties": {
            "folder_id": {
                "type": "string",
                "description": "ID of the folder to list (default: root)"
            },
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Number of items per page (default: 25)",
                "default": 25
            }
        }
    }
)
# @validated("list_drive_items")
def list_drive_items_handler(current_user, data):
    return common_handler(list_drive_items, folder_id="root", page_size=25)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=upload_file",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="uploadFile",
    description="Uploads a file to OneDrive.",
    params={
        "file_path": "Path where to store the file (including filename)",
        "file_content": "Content to upload",
        "folder_id": "Parent folder ID (default: root)"
    },
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path where to store the file (including filename)"
            },
            "file_content": {
                "type": "string",
                "description": "Content to upload"
            },
            "folder_id": {
                "type": "string",
                "description": "Parent folder ID (default: root)",
                "default": "root"
            }
        },
        "required": ["file_path", "file_content"]
    }
)
# @validated("upload_file")
def upload_file_handler(current_user, data):
    return common_handler(upload_file, file_path=None, file_content=None, folder_id="root")(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=download_file",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_read"],
    name="downloadFile",
    description="Downloads a file from OneDrive.",
    params={
        "item_id": "ID of the file to download"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "ID of the file to download"
            }
        },
        "required": ["item_id"]
    }
)
# @validated("download_file")
def download_file_handler(current_user, data):
    return common_handler(download_file, item_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="deleteItem",
    description="Deletes a file or folder from OneDrive.",
    params={
        "item_id": "ID of the item to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "ID of the item to delete"
            }
        },
        "required": ["item_id"]
    }
)
# @validated("delete_item")
def delete_item_handler(current_user, data):
    return common_handler(delete_item, item_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_drive_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_read"],
    name="getDriveItem",
    description="Retrieves metadata for a specific drive item.",
    params={
        "item_id": "ID of the drive item"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the drive item"}
        },
        "required": ["item_id"]
    }
)
def get_drive_item_handler(current_user, data):
    return common_handler(get_drive_item, item_id=None)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=create_folder",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="createFolder",
    description="Creates a new folder in OneDrive.",
    params={
        "folder_name": "Name of the new folder",
        "parent_folder_id": "ID of the parent folder (default: root)"
    },
    parameters={
        "type": "object",
        "properties": {
            "folder_name": {"type": "string", "description": "Name of the new folder"},
            "parent_folder_id": {"type": "string", "description": "ID of the parent folder", "default": "root"}
        },
        "required": ["folder_name"]
    }
)
def create_folder_handler(current_user, data):
    return common_handler(create_folder, folder_name=None, parent_folder_id="root")(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=update_drive_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="updateDriveItem",
    description="Updates metadata for a specific drive item.",
    params={
        "item_id": "ID of the drive item",
        "updates": "Dictionary of updates to apply"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the drive item"},
            "updates": {"type": "object", "description": "Dictionary of updates"}
        },
        "required": ["item_id", "updates"]
    }
)
def update_drive_item_handler(current_user, data):
    return common_handler(update_drive_item, item_id=None, updates=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=copy_drive_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="copyDriveItem",
    description="Copies a drive item to a new location.",
    params={
        "item_id": "ID of the drive item to copy",
        "new_name": "New name for the copied item",
        "parent_folder_id": "Destination parent folder ID (default: root)"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the drive item to copy"},
            "new_name": {"type": "string", "description": "New name for the copied item"},
            "parent_folder_id": {"type": "string", "description": "Destination parent folder ID", "default": "root"}
        },
        "required": ["item_id", "new_name"]
    }
)
def copy_drive_item_handler(current_user, data):
    return common_handler(copy_drive_item, item_id=None, new_name=None, parent_folder_id="root")(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=move_drive_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="moveDriveItem",
    description="Moves a drive item to a different folder.",
    params={
        "item_id": "ID of the drive item to move",
        "new_parent_id": "ID of the new parent folder"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the drive item to move"},
            "new_parent_id": {"type": "string", "description": "ID of the new parent folder"}
        },
        "required": ["item_id", "new_parent_id"]
    }
)
def move_drive_item_handler(current_user, data):
    return common_handler(move_drive_item, item_id=None, new_parent_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_sharing_link",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_read"],
    name="createSharingLink",
    description="Creates a sharing link for a drive item.",
    params={
        "item_id": "ID of the drive item",
        "link_type": "Type of link (view/edit), default view",
        "scope": "Link scope (anonymous/organization), default anonymous"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the drive item"},
            "link_type": {"type": "string", "description": "Type of link", "default": "view"},
            "scope": {"type": "string", "description": "Link scope", "default": "anonymous"}
        },
        "required": ["item_id"]
    }
)
def create_sharing_link_handler(current_user, data):
    return common_handler(create_sharing_link, item_id=None, link_type="view", scope="anonymous")(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=invite_to_drive_item",
    tags=["default", "integration", "microsoft_drive", "microsoft_drive_write"],
    name="inviteToDriveItem",
    description="Invites users to access a drive item.",
    params={
        "item_id": "ID of the drive item",
        "recipients": "List of recipient objects (with email addresses)",
        "message": "Invitation message (optional)",
        "require_sign_in": "Whether sign-in is required (default true)",
        "send_invitation": "Whether to send invitation email (default true)",
        "roles": "List of roles (default ['read'])"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "ID of the drive item"},
            "recipients": {"type": "array", "items": {"type": "object"}, "description": "List of recipient objects"},
            "message": {"type": "string", "description": "Invitation message", "default": ""},
            "require_sign_in": {"type": "boolean", "description": "Require sign in", "default": True},
            "send_invitation": {"type": "boolean", "description": "Send invitation", "default": True},
            "roles": {"type": "array", "items": {"type": "string"}, "description": "List of roles", "default": ["read"]}
        },
        "required": ["item_id", "recipients"]
    }
)
def invite_to_drive_item_handler(current_user, data):
    return common_handler(invite_to_drive_item, item_id=None, recipients=None, message="", require_sign_in=True, send_invitation=True, roles=None)(current_user, data)



### excel ###

@vop(
    path="/microsoft/integrations/route?op=list_worksheets",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="listWorksheets",
    description="Lists worksheets in a workbook stored in OneDrive.",
    params={
        "item_id": "OneDrive item ID of the workbook"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook"
            }
        },
        "required": ["item_id"]
    }
)
# @validated("list_worksheets")
def list_worksheets_handler(current_user, data):
    return common_handler(list_worksheets, item_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=list_tables",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="listTables",
    description="Lists tables in a workbook or specific worksheet.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "worksheet_name": "Optional worksheet name to filter tables"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook"
            },
            "worksheet_name": {
                "type": "string",
                "description": "Optional worksheet name to filter tables"
            }
        },
        "required": ["item_id"]
    }
)
# @validated("list_tables")
def list_tables_handler(current_user, data):
    return common_handler(list_tables, item_id=None, worksheet_name=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=add_row_to_table",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="addRowToTable",
    description="Adds a row to a table in the workbook.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "table_name": "Name of the table",
        "row_values": "List of cell values for the new row"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook"
            },
            "table_name": {
                "type": "string",
                "description": "Name of the table"
            },
            "row_values": {
                "type": "array",
                "description": "List of cell values for the new row"
            }
        },
        "required": ["item_id", "table_name", "row_values"]
    }
)
# @validated("add_row_to_table")
def add_row_to_table_handler(current_user, data):
    return common_handler(add_row_to_table, item_id=None, table_name=None, row_values=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=read_range",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="readRange",
    description="Reads a range in the given worksheet.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "worksheet_name": "Name of the worksheet",
        "address": "Range address (e.g., 'A1:C10')"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook"
            },
            "worksheet_name": {
                "type": "string",
                "description": "Name of the worksheet"
            },
            "address": {
                "type": "string",
                "description": "Range address (e.g., 'A1:C10')"
            }
        },
        "required": ["item_id", "worksheet_name", "address"]
    }
)
# @validated("read_range")
def read_range_handler(current_user, data):
    return common_handler(read_range, item_id=None, worksheet_name=None, address=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=update_range",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="updateRange",
    description="Updates a range in the given worksheet.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "worksheet_name": "Name of the worksheet",
        "address": "Range address (e.g., 'A1:C10')",
        "values": "2D list of values to update"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "OneDrive item ID of the workbook"
            },
            "worksheet_name": {
                "type": "string",
                "description": "Name of the worksheet"
            },
            "address": {
                "type": "string",
                "description": "Range address (e.g., 'A1:C10')"
            },
            "values": {
                "type": "array",
                "description": "2D list of values to update"
            }
        },
        "required": ["item_id", "worksheet_name", "address", "values"]
    }
)
# @validated("update_range")
def update_range_handler(current_user, data):
    return common_handler(update_range, item_id=None, worksheet_name=None, address=None, values=None)(current_user, data)

### outlook ###

@vop(
    path="/microsoft/integrations/route?op=list_messages",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="listMessages",
    description="Lists messages in a specified mail folder with pagination and filtering support.",
    params={
        "folder_id": "Folder ID or well-known name (default: Inbox)",
        "top": "Maximum number of messages to retrieve",
        "skip": "Number of messages to skip",
        "filter_query": "OData filter query"
    },
    parameters={
        "type": "object",
        "properties": {
            "folder_id": {
                "type": "string",
                "description": "Folder ID or well-known name",
                "default": "Inbox"
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of messages to retrieve",
                "default": 10
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of messages to skip",
                "default": 0
            },
            "filter_query": {
                "type": "string",
                "description": "OData filter query"
            }
        }
    }
)
# @validated("list_messages")
def list_messages_handler(current_user, data):
    return common_handler(list_messages, folder_id="Inbox", top=10, skip=0, filter_query=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_message_details",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="getMessageDetails",
    description="Gets detailed information about a specific message.",
    params={
        "message_id": "Message ID",
        "include_body": "Whether to include message body"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "Message ID"
            },
            "include_body": {
                "type": "boolean",
                "description": "Whether to include message body",
                "default": True
            }
        },
        "required": ["message_id"]
    }
)
# @validated("get_message_details")
def get_message_details_handler(current_user, data):
    return common_handler(get_message_details, message_id=None, include_body=True)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=send_mail",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="sendMail",
    description="Sends an email with support for CC, BCC, and importance levels.",
    params={
        "subject": "Email subject",
        "body": "Email body content",
        "to_recipients": "List of primary recipient email addresses",
        "cc_recipients": "Optional list of CC recipient email addresses",
        "bcc_recipients": "Optional list of BCC recipient email addresses",
        "importance": "Message importance (low, normal, high)"
    },
    parameters={
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Email subject"
            },
            "body": {
                "type": "string",
                "description": "Email body content"
            },
            "to_recipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of primary recipient email addresses"
            },
            "cc_recipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of CC recipient email addresses"
            },
            "bcc_recipients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of BCC recipient email addresses"
            },
            "importance": {
                "type": "string",
                "enum": ["low", "normal", "high"],
                "description": "Message importance",
                "default": "normal"
            }
        },
        "required": ["subject", "body", "to_recipients"]
    }
)
# @validated("send_mail")
def send_mail_handler(current_user, data):
    return common_handler(send_mail, subject=None, body=None, to_recipients=None, 
                        cc_recipients=None, bcc_recipients=None, importance="normal")(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="deleteMessage",
    description="Deletes a message.",
    params={
        "message_id": "Message ID to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "Message ID to delete"
            }
        },
        "required": ["message_id"]
    }
)
# @validated("delete_message")
def delete_message_handler(current_user, data):
    return common_handler(delete_message, message_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_attachments",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="getAttachments",
    description="Gets attachments for a specific message.",
    params={
        "message_id": "Message ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "Message ID"
            }
        },
        "required": ["message_id"]
    }
)
# @validated("get_attachments")
def get_attachments_handler(current_user, data):
    return common_handler(get_attachments, message_id=None)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=list_plans_in_group",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_read"],
    name="listPlansInGroup",
    description="Retrieves all Planner plans in a specific Microsoft 365 group.",
    params={
        "group_id": "Microsoft 365 Group ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "group_id": {
                "type": "string",
                "description": "Microsoft 365 Group ID"
            }
        },
        "required": ["group_id"]
    }
)
# @validated("list_plans_in_group")
def list_plans_in_group_handler(current_user, data):
    return common_handler(list_plans_in_group, group_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=list_buckets_in_plan",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_read"],
    name="listBucketsInPlan",
    description="Lists all buckets in a plan.",
    params={
        "plan_id": "Plan ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "plan_id": {
                "type": "string",
                "description": "Plan ID"
            }
        },
        "required": ["plan_id"]
    }
)
# @validated("list_buckets_in_plan")
def list_buckets_in_plan_handler(current_user, data):
    return common_handler(list_buckets_in_plan, plan_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=list_tasks_in_plan",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_read"],
    name="listTasksInPlan",
    description="Lists all tasks in a plan with optional detailed information.",
    params={
        "plan_id": "Plan ID",
        "include_details": "Whether to include task details"
    },
    parameters={
        "type": "object",
        "properties": {
            "plan_id": {
                "type": "string",
                "description": "Plan ID"
            },
            "include_details": {
                "type": "boolean",
                "description": "Whether to include task details",
                "default": False
            }
        },
        "required": ["plan_id"]
    }
)
# @validated("list_tasks_in_plan")
def list_tasks_in_plan_handler(current_user, data):
    return common_handler(list_tasks_in_plan, plan_id=None, include_details=False)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_task",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_write"],
    name="createTask",
    description="Creates a new task in Planner.",
    params={
        "plan_id": "Plan ID",
        "bucket_id": "Bucket ID",
        "title": "Task title",
        "assignments": "Dict of userId -> assignment details",
        "due_date": "Optional due date in ISO format",
        "priority": "Optional priority (1-10, where 10 is highest)"
    },
    parameters={
        "type": "object",
        "properties": {
            "plan_id": {
                "type": "string",
                "description": "Plan ID"
            },
            "bucket_id": {
                "type": "string",
                "description": "Bucket ID"
            },
            "title": {
                "type": "string",
                "description": "Task title"
            },
            "assignments": {
                "type": "object",
                "description": "Dict of userId -> assignment details"
            },
            "due_date": {
                "type": "string",
                "description": "Optional due date in ISO format"
            },
            "priority": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Optional priority (1-10, where 10 is highest)"
            }
        },
        "required": ["plan_id", "bucket_id", "title"]
    }
)
# @validated("create_task")
def create_task_handler(current_user, data):
    return common_handler(create_task, plan_id=None, bucket_id=None, title=None, 
                        assignments=None, due_date=None, priority=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=update_task",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_write"],
    name="updateTask",
    description="Updates a task with ETag concurrency control.",
    params={
        "task_id": "Task ID",
        "e_tag": "Current ETag of the task",
        "update_fields": "Fields to update"
    },
    parameters={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Task ID"
            },
            "e_tag": {
                "type": "string",
                "description": "Current ETag of the task"
            },
            "update_fields": {
                "type": "object",
                "description": "Fields to update"
            }
        },
        "required": ["task_id", "e_tag", "update_fields"]
    }
)
# @validated("update_task")
def update_task_handler(current_user, data):
    return common_handler(update_task, task_id=None, e_tag=None, update_fields=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_task",
    tags=["default", "integration", "microsoft_planner", "microsoft_planner_write"],
    name="deleteTask",
    description="Deletes a task with ETag concurrency control.",
    params={
        "task_id": "Task ID",
        "e_tag": "Current ETag of the task"
    },
    parameters={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Task ID"
            },
            "e_tag": {
                "type": "string",
                "description": "Current ETag of the task"
            }
        },
        "required": ["task_id", "e_tag"]
    }
)
# @validated("delete_task")
def delete_task_handler(current_user, data):
    return common_handler(delete_task, task_id=None, e_tag=None)(current_user, data)

### sharepoint ###

@vop(
    path="/microsoft/integrations/route?op=list_sites",
    tags=["default", "integration", "microsoft_sharepoint", "microsoft_sharepoint_read"],
    name="listSites",
    description="Lists SharePoint sites with search and pagination support.",
    params={
        "search_query": "Optional search term",
        "top": "Maximum number of sites to retrieve",
        "skip": "Number of sites to skip"
    },
    parameters={
        "type": "object",
        "properties": {
            "search_query": {
                "type": "string",
                "description": "Optional search term"
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of sites to retrieve",
                "default": 10
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of sites to skip",
                "default": 0
            }
        }
    }
)
# @validated("list_sites")
def list_sites_handler(current_user, data):
    return common_handler(list_sites, search_query=None, top=10, skip=0)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_site_by_path",
    tags=["default", "integration", "microsoft_sharepoint", "microsoft_sharepoint_read"],
    name="getSiteByPath",
    description="Gets a site by its hostname and optional path.",
    params={
        "hostname": "SharePoint hostname",
        "site_path": "Optional site path"
    },
    parameters={
        "type": "object",
        "properties": {
            "hostname": {
                "type": "string",
                "description": "SharePoint hostname"
            },
            "site_path": {
                "type": "string",
                "description": "Optional site path"
            }
        },
        "required": ["hostname"]
    }
)
# @validated("get_site_by_path")
def get_site_by_path_handler(current_user, data):
    return common_handler(get_site_by_path, hostname=None, site_path=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=list_site_lists",
    tags=["default", "integration", "microsoft_sharepoint", "microsoft_sharepoint_read"],
    name="listSiteLists",
    description="Lists SharePoint lists in a site with pagination.",
    params={
        "site_id": "Site ID",
        "top": "Maximum number of lists to retrieve",
        "skip": "Number of lists to skip"
    },
    parameters={
        "type": "object",
        "properties": {
            "site_id": {
                "type": "string",
                "description": "Site ID"
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of lists to retrieve",
                "default": 10
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of lists to skip",
                "default": 0
            }
        },
        "required": ["site_id"]
    }
)
# @validated("list_site_lists")
def list_site_lists_handler(current_user, data):
    return common_handler(list_site_lists, site_id=None, top=10, skip=0)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_list_items",
    tags=["default", "integration", "microsoft_sharepoint", "microsoft_sharepoint_read"],
    name="getListItems",
    description="Gets items from a SharePoint list with pagination and filtering.",
    params={
        "site_id": "Site ID",
        "list_id": "List ID",
        "expand_fields": "Whether to expand field values",
        "top": "Maximum number of items to retrieve",
        "skip": "Number of items to skip",
        "filter_query": "Optional OData filter query"
    },
    parameters={
        "type": "object",
        "properties": {
            "site_id": {
                "type": "string",
                "description": "Site ID"
            },
            "list_id": {
                "type": "string",
                "description": "List ID"
            },
            "expand_fields": {
                "type": "boolean",
                "description": "Whether to expand field values",
                "default": True
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of items to retrieve",
                "default": 10
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of items to skip",
                "default": 0
            },
            "filter_query": {
                "type": "string",
                "description": "Optional OData filter query"
            }
        },
        "required": ["site_id", "list_id"]
    }
)
# @validated("get_list_items")
def get_list_items_handler(current_user, data):
    return common_handler(get_list_items, site_id=None, list_id=None, expand_fields=True, 
                        top=10, skip=0, filter_query=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_list_item",
    tags=["default", "integration", "microsoft_sharepoint", "microsoft_sharepoint_write"],
    name="createListItem",
    description="Creates a new item in a SharePoint list.",
    params={
        "site_id": "Site ID",
        "list_id": "List ID",
        "fields_dict": "Dictionary of field names and values"
    },
    parameters={
        "type": "object",
        "properties": {
            "site_id": {
                "type": "string",
                "description": "Site ID"
            },
            "list_id": {
                "type": "string",
                "description": "List ID"
            },
            "fields_dict": {
                "type": "object",
                "description": "Dictionary of field names and values"
            }
        },
        "required": ["site_id", "list_id", "fields_dict"]
    }
)
# @validated("create_list_item")
def create_list_item_handler(current_user, data):
    return common_handler(create_list_item, site_id=None, list_id=None, fields_dict=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=update_list_item",
    tags=["default", "integration", "microsoft_sharepoint", "microsoft_sharepoint_write"],
    name="updateListItem",
    description="Updates an existing SharePoint list item.",
    params={
        "site_id": "Site ID",
        "list_id": "List ID",
        "item_id": "Item ID",
        "fields_dict": "Dictionary of field names and values to update"
    },
    parameters={
        "type": "object",
        "properties": {
            "site_id": {
                "type": "string",
                "description": "Site ID"
            },
            "list_id": {
                "type": "string",
                "description": "List ID"
            },
            "item_id": {
                "type": "string",
                "description": "Item ID"
            },
            "fields_dict": {
                "type": "object",
                "description": "Dictionary of field names and values to update"
            }
        },
        "required": ["site_id", "list_id", "item_id", "fields_dict"]
    }
)
# @validated("update_list_item")
def update_list_item_handler(current_user, data):
    return common_handler(update_list_item, site_id=None, list_id=None, item_id=None, 
                        fields_dict=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_list_item",
    tags=["default", "integration", "microsoft_sharepoint", "microsoft_sharepoint_write"],
    name="deleteListItem",
    description="Deletes an item from a SharePoint list.",
    params={
        "site_id": "Site ID",
        "list_id": "List ID",
        "item_id": "Item ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "site_id": {
                "type": "string",
                "description": "Site ID"
            },
            "list_id": {
                "type": "string",
                "description": "List ID"
            },
            "item_id": {
                "type": "string",
                "description": "Item ID"
            }
        },
        "required": ["site_id", "list_id", "item_id"]
    }
)
# @validated("delete_list_item")
def delete_list_item_handler(current_user, data):
    return common_handler(delete_list_item, site_id=None, list_id=None, item_id=None)(current_user, data)

### teams ###

@op(
    path="/microsoft/integrations/route?op=list_teams",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_read"],
    name="listTeams",
    description="Lists teams that the user is a member of.",
    method="GET",
    parameters={}
)
# @validated("list_teams")
def list_teams_handler(current_user, data):
    return common_handler(list_teams)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=list_channels",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_read"],
    name="listChannels",
    description="Lists channels in a team.",
    params={
        "team_id": "Team ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "Team ID"
            }
        },
        "required": ["team_id"]
    }
)
# @validated("list_channels")
def list_channels_handler(current_user, data):
    return common_handler(list_channels, team_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_channel",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_write"],
    name="createChannel",
    description="Creates a new channel in a team.",
    params={
        "team_id": "Team ID",
        "name": "Channel name",
        "description": "Channel description (optional)"
    },
    parameters={
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "Team ID"
            },
            "name": {
                "type": "string",
                "description": "Channel name"
            },
            "description": {
                "type": "string",
                "description": "Channel description"
            }
        },
        "required": ["team_id", "name"]
    }
)
# @validated("create_channel")
def create_channel_handler(current_user, data):
    return common_handler(create_channel, team_id=None, name=None, description="")(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=send_channel_message",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_write"],
    name="sendChannelMessage",
    description="Sends a message to a channel.",
    params={
        "team_id": "Team ID",
        "channel_id": "Channel ID",
        "message": "Message content (can include basic HTML)",
        "importance": "Message importance (normal, high, urgent)"
    },
    parameters={
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "Team ID"
            },
            "channel_id": {
                "type": "string",
                "description": "Channel ID"
            },
            "message": {
                "type": "string",
                "description": "Message content (can include basic HTML)"
            },
            "importance": {
                "type": "string",
                "enum": ["normal", "high", "urgent"],
                "description": "Message importance",
                "default": "normal"
            }
        },
        "required": ["team_id", "channel_id", "message"]
    }
)
# @validated("send_channel_message")
def send_channel_message_handler(current_user, data):
    return common_handler(send_channel_message, team_id=None, channel_id=None, 
                        message=None, importance="normal")(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_chat_messages",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_read"],
    name="getChatMessages",
    description="Gets messages from a chat.",
    params={
        "chat_id": "Chat ID",
        "top": "Maximum number of messages to retrieve"
    },
    parameters={
        "type": "object",
        "properties": {
            "chat_id": {
                "type": "string",
                "description": "Chat ID"
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum number of messages to retrieve",
                "default": 50
            }
        },
        "required": ["chat_id"]
    }
)
# @validated("get_chat_messages")
def get_chat_messages_handler(current_user, data):
    return common_handler(get_chat_messages, chat_id=None, top=50)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=schedule_meeting",
    tags=["default", "integration", "microsoft_teams", "microsoft_teams_write"],
    name="scheduleMeeting",
    description="Schedules a Teams meeting.",
    params={
        "team_id": "Team ID",
        "subject": "Meeting subject",
        "start_time": "Start time in ISO format",
        "end_time": "End time in ISO format",
        "attendees": "List of attendee email addresses"
    },
    parameters={
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "Team ID"
            },
            "subject": {
                "type": "string",
                "description": "Meeting subject"
            },
            "start_time": {
                "type": "string",
                "description": "Start time in ISO format"
            },
            "end_time": {
                "type": "string",
                "description": "End time in ISO format"
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of attendee email addresses"
            }
        },
        "required": ["team_id", "subject", "start_time", "end_time"]
    }
)
# @validated("schedule_meeting")
def schedule_meeting_handler(current_user, data):
    return common_handler(schedule_meeting, team_id=None, subject=None, 
                        start_time=None, end_time=None, attendees=None)(current_user, data)


### user_groups ###

@vop(
    path="/microsoft/integrations/route?op=list_users",
    tags=["default", "integration", "microsoft_user_groups", "microsoft_user_groups_read"],
    name="listUsers",
    description="Lists users with search, pagination and sorting support.",
    params={
        "search_query": "Optional search term",
        "top": "Maximum number of users to retrieve",
        "skip": "Number of users to skip",
        "order_by": "Property to sort by (e.g., 'displayName', 'userPrincipalName')"
    },
    parameters={
        "type": "object",
        "properties": {
            "search_query": {
                "type": "string",
                "description": "Optional search term"
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of users to retrieve",
                "default": 10
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of users to skip",
                "default": 0
            },
            "order_by": {
                "type": "string",
                "description": "Property to sort by"
            }
        }
    }
)
# @validated("list_users")
def list_users_handler(current_user, data):
    return common_handler(list_users, search_query=None, top=10, skip=0, order_by=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_user_details",
    tags=["default", "integration", "microsoft_user_groups", "microsoft_user_groups_read"],
    name="getUserDetails",
    description="Gets detailed information about a specific user.",
    params={
        "user_id": "User ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "User ID"
            }
        },
        "required": ["user_id"]
    }
)
# @validated("get_user_details")
def get_user_details_handler(current_user, data):
    return common_handler(get_user_details, user_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=list_groups",
    tags=["default", "integration", "microsoft_user_groups", "microsoft_user_groups_read"],
    name="listGroups",
    description="Lists groups with filtering and pagination support.",
    params={
        "search_query": "Optional search term",
        "group_type": "Optional group type filter ('Unified', 'Security')",
        "top": "Maximum number of groups to retrieve",
        "skip": "Number of groups to skip"
    },
    parameters={
        "type": "object",
        "properties": {
            "search_query": {
                "type": "string",
                "description": "Optional search term"
            },
            "group_type": {
                "type": "string",
                "description": "Optional group type filter",
                "enum": ["Unified", "Security"]
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of groups to retrieve",
                "default": 10
            },
            "skip": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of groups to skip",
                "default": 0
            }
        }
    }
)
# @validated("list_groups")
def list_groups_handler(current_user, data):
    return common_handler(list_groups, search_query=None, group_type=None, top=10, skip=0)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_group_details",
    tags=["default", "integration", "microsoft_user_groups", "microsoft_user_groups_read"],
    name="getGroupDetails",
    description="Gets detailed information about a specific group.",
    params={
        "group_id": "Group ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "group_id": {
                "type": "string",
                "description": "Group ID"
            }
        },
        "required": ["group_id"]
    }
)
# @validated("get_group_details")
def get_group_details_handler(current_user, data):
    return common_handler(get_group_details, group_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_group",
    tags=["default", "integration", "microsoft_user_groups", "microsoft_user_groups_write"],
    name="createGroup",
    description="Creates a new group.",
    params={
        "display_name": "Group display name",
        "mail_nickname": "Mail nickname",
        "group_type": "Group type ('Unified' or 'Security')",
        "description": "Optional group description",
        "owners": "Optional list of owner user IDs",
        "members": "Optional list of member user IDs"
    },
    parameters={
        "type": "object",
        "properties": {
            "display_name": {
                "type": "string",
                "description": "Group display name"
            },
            "mail_nickname": {
                "type": "string",
                "description": "Mail nickname"
            },
            "group_type": {
                "type": "string",
                "enum": ["Unified", "Security"],
                "default": "Unified",
                "description": "Group type"
            },
            "description": {
                "type": "string",
                "description": "Optional group description"
            },
            "owners": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of owner user IDs"
            },
            "members": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of member user IDs"
            }
        },
        "required": ["display_name"]
    }
)
# @validated("create_group")
def create_group_handler(current_user, data):
    return common_handler(create_group, display_name=None, mail_nickname=None, 
                        group_type="Unified", description=None, owners=None, 
                        members=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_group",
    tags=["default", "integration", "microsoft_user_groups", "microsoft_user_groups_write"],
    name="deleteGroup",
    description="Deletes a group.",
    params={
        "group_id": "Group ID to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "group_id": {
                "type": "string",
                "description": "Group ID to delete"
            }
        },
        "required": ["group_id"]
    }
)
# @validated("delete_group")
def delete_group_handler(current_user, data):
    return common_handler(delete_group, group_id=None)(current_user, data)


### onenote ###

@vop(
    path="/microsoft/integrations/route?op=list_notebooks",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_read"],
    name="listNotebooks",
    description="Lists user's OneNote notebooks with pagination support.",
    params={
        "top": "Maximum number of notebooks to retrieve"
    },
    parameters={
        "type": "object",
        "properties": {
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of notebooks to retrieve",
                "default": 10
            }
        }
    }
)
# @validated("list_notebooks")
def list_notebooks_handler(current_user, data):
    return common_handler(list_notebooks, top=10)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=list_sections_in_notebook",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_read"],
    name="listSectionsInNotebook",
    description="Lists sections in a notebook.",
    params={
        "notebook_id": "Notebook ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "notebook_id": {
                "type": "string",
                "description": "Notebook ID"
            }
        },
        "required": ["notebook_id"]
    }
)
# @validated("list_sections_in_notebook")
def list_sections_in_notebook_handler(current_user, data):
    return common_handler(list_sections_in_notebook, notebook_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=list_pages_in_section",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_read"],
    name="listPagesInSection",
    description="Lists pages in a section.",
    params={
        "section_id": "Section ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "section_id": {
                "type": "string",
                "description": "Section ID"
            }
        },
        "required": ["section_id"]
    }
)
# @validated("list_pages_in_section")
def list_pages_in_section_handler(current_user, data):
    return common_handler(list_pages_in_section, section_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_page_in_section",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_write"],
    name="createPageInSection",
    description="Creates a new page in a section.",
    params={
        "section_id": "Section ID",
        "title": "Page title",
        "html_content": "HTML content for the page"
    },
    parameters={
        "type": "object",
        "properties": {
            "section_id": {
                "type": "string",
                "description": "Section ID"
            },
            "title": {
                "type": "string",
                "description": "Page title"
            },
            "html_content": {
                "type": "string",
                "description": "HTML content for the page"
            }
        },
        "required": ["section_id", "title", "html_content"]
    }
)
# @validated("create_page_in_section")
def create_page_in_section_handler(current_user, data):
    return common_handler(create_page_in_section, section_id=None, title=None, 
                        html_content=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_page_content",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_read"],
    name="getPageContent",
    description="Retrieves the HTML content of a page.",
    params={
        "page_id": "Page ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "page_id": {
                "type": "string",
                "description": "Page ID"
            }
        },
        "required": ["page_id"]
    }
)
# @validated("get_page_content")
def get_page_content_handler(current_user, data):
    return common_handler(get_page_content, page_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_page_with_image_and_attachment",
    tags=["default", "integration", "microsoft_onenote", "microsoft_onenote_write"],
    name="createPageWithImageAndAttachment",
    description="Creates a page with embedded image and file attachment.",
    params={
        "section_id": "Section ID",
        "title": "Page title",
        "html_body": "HTML content",
        "image_name": "Name of the image file",
        "image_content": "Base64 encoded image content",
        "image_content_type": "Image MIME type",
        "file_name": "Name of the attachment",
        "file_content": "Base64 encoded file content",
        "file_content_type": "File MIME type"
    },
    parameters={
        "type": "object",
        "properties": {
            "section_id": {
                "type": "string",
                "description": "Section ID"
            },
            "title": {
                "type": "string",
                "description": "Page title"
            },
            "html_body": {
                "type": "string",
                "description": "HTML content"
            },
            "image_name": {
                "type": "string",
                "description": "Name of the image file"
            },
            "image_content": {
                "type": "string",
                "description": "Base64 encoded image content"
            },
            "image_content_type": {
                "type": "string",
                "description": "Image MIME type"
            },
            "file_name": {
                "type": "string",
                "description": "Name of the attachment"
            },
            "file_content": {
                "type": "string",
                "description": "Base64 encoded file content"
            },
            "file_content_type": {
                "type": "string",
                "description": "File MIME type"
            }
        },
        "required": ["section_id", "title", "html_body", "image_name", "image_content",
                    "image_content_type", "file_name", "file_content", "file_content_type"]
    }
)
# @validated("create_page_with_image_and_attachment")
def create_page_with_attachments_handler(current_user, data):
    return common_handler(create_page_with_image_and_attachment, section_id=None, title=None,
                        html_body=None, image_name=None, image_content=None, image_content_type=None,
                        file_name=None, file_content=None, file_content_type=None)(current_user, data)


### contacts ###

@vop(
    path="/microsoft/integrations/route?op=list_contacts",
    tags=["default", "integration", "microsoft_contacts", "microsoft_contacts_read"],
    name="listContacts",
    description="Retrieve a list of contacts with pagination support.",
    params={
        "page_size": "Number of contacts to retrieve per page"
    },
    parameters={
        "type": "object",
        "properties": {
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Number of contacts to retrieve per page",
                "default": 10
            }
        }
    }
)
# @validated("list_contacts")
def list_contacts_handler(current_user, data):
    return common_handler(list_contacts, page_size=10)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_contact_details",
    tags=["default", "integration", "microsoft_contacts", "microsoft_contacts_read"],
    name="getContactDetails",
    description="Get details for a specific contact.",
    params={
        "contact_id": "Contact ID to retrieve"
    },
    parameters={
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "string",
                "description": "Contact ID to retrieve"
            }
        },
        "required": ["contact_id"]
    }
)
# @validated("get_contact_details")
def get_contact_details_handler(current_user, data):
    return common_handler(get_contact_details, contact_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_contact",
    tags=["default", "integration", "microsoft_contacts", "microsoft_contacts_write"],
    name="createContact",
    description="Create a new contact.",
    params={
        "given_name": "Contact's first name",
        "surname": "Contact's last name",
        "email_addresses": "List of email addresses for the contact"
    },
    parameters={
        "type": "object",
        "properties": {
            "given_name": {
                "type": "string",
                "description": "Contact's first name"
            },
            "surname": {
                "type": "string",
                "description": "Contact's last name"
            },
            "email_addresses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of email addresses for the contact"
            }
        },
        "required": ["email_addresses"]
    }
)
# @validated("create_contact")
def create_contact_handler(current_user, data):
    return common_handler(create_contact, given_name="", surname="", 
                        email_addresses=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_contact",
    tags=["default", "integration", "microsoft_contacts", "microsoft_contacts_write"],
    name="deleteContact",
    description="Delete a contact.",
    params={
        "contact_id": "Contact ID to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "string",
                "description": "Contact ID to delete"
            }
        },
        "required": ["contact_id"]
    }
)
# @validated("delete_contact")
def delete_contact_handler(current_user, data):
    return common_handler(delete_contact, contact_id=None)(current_user, data)

### calendar ###
@vop(
    path="/microsoft/integrations/route?op=create_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="createEvent",
    description="Creates a new calendar event.",
    params={
        "title": "Event title",
        "start_time": "Start time in ISO format",
        "end_time": "End time in ISO format",
        "description": "Event description"
    },
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Event title"
            },
            "start_time": {
                "type": "string",
                "description": "Start time in ISO format"
            },
            "end_time": {
                "type": "string",
                "description": "End time in ISO format"
            },
            "description": {
                "type": "string",
                "description": "Event description",
                "default": ""
            }
        },
        "required": ["title", "start_time", "end_time"]
    }
)
# @validated("create_event")
def create_event_handler(current_user, data):
    return common_handler(create_event, title=None, start_time=None, 
                        end_time=None, description="")(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=update_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="updateEvent",
    description="Updates an existing calendar event.",
    params={
        "event_id": "Event ID to update",
        "updated_fields": "Dictionary of fields to update"
    },
    parameters={
        "type": "object",
        "properties": {
            "event_id": {
                "type": "string",
                "description": "Event ID to update"
            },
            "updated_fields": {
                "type": "object",
                "description": "Dictionary of fields to update"
            }
        },
        "required": ["event_id", "updated_fields"]
    }
)
# @validated("update_event")
def update_event_handler(current_user, data):
    return common_handler(update_event, event_id=None, updated_fields=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="deleteEvent",
    description="Deletes a calendar event.",
    params={
        "event_id": "Event ID to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "event_id": {
                "type": "string",
                "description": "Event ID to delete"
            }
        },
        "required": ["event_id"]
    }
)
# @validated("delete_event")
def delete_event_handler(current_user, data):
    return common_handler(delete_event, event_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_event_details",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="getEventDetails",
    description="Gets details for a specific calendar event.",
    params={
        "event_id": "Event ID to retrieve"
    },
    parameters={
        "type": "object",
        "properties": {
            "event_id": {
                "type": "string",
                "description": "Event ID to retrieve"
            }
        },
        "required": ["event_id"]
    }
)
# @validated("get_event_details")
def get_event_details_handler(current_user, data):
    return common_handler(get_event_details, event_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_events_between_dates",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="getEventsBetweenDates",
    description="Retrieves events between two dates.",
    params={
        "start_dt": "Start datetime in ISO format",
        "end_dt": "End datetime in ISO format",
        "page_size": "Maximum number of events to retrieve per page MAX 50"
    },
    parameters={
        "type": "object",
        "properties": {
            "start_dt": {
                "type": "string",
                "description": "Start datetime in ISO format"
            },
            "end_dt": {
                "type": "string",
                "description": "End datetime in ISO format"
            },
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum number of events to retrieve per page",
                "default": 50
            }
        },
        "required": ["start_dt", "end_dt"]
    }
)
# @validated("get_events_between_dates")
def get_events_between_dates_handler(current_user, data):
    return common_handler(get_events_between_dates, start_dt=None, end_dt=None, 
                        page_size=50)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=update_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="updateMessage",
    description="Updates a specific message with provided changes.",
    params={
        "message_id": "ID of the message to update",
        "changes": "Dictionary of fields and values to update"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "ID of the message"},
            "changes": {"type": "object", "description": "Dictionary of updates"}
        },
        "required": ["message_id", "changes"]
    }
)
def update_message_handler(current_user, data):
    return common_handler(update_message, message_id=None, changes=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_draft",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="createDraft",
    description="Creates a draft email message.",
    params={
        "subject": "Draft email subject",
        "body": "Draft email body content",
        "to_recipients": "Optional list of recipient email addresses",
        "cc_recipients": "Optional list of CC recipient email addresses",
        "bcc_recipients": "Optional list of BCC recipient email addresses",
        "importance": "Email importance (low, normal, high)"
    },
    parameters={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Draft email subject"},
            "body": {"type": "string", "description": "Draft email body content"},
            "to_recipients": {"type": "array", "items": {"type": "string"}, "description": "List of recipient email addresses"},
            "cc_recipients": {"type": "array", "items": {"type": "string"}, "description": "List of CC email addresses"},
            "bcc_recipients": {"type": "array", "items": {"type": "string"}, "description": "List of BCC email addresses"},
            "importance": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal", "description": "Email importance"}
        },
        "required": ["subject", "body"]
    }
)
def create_draft_handler(current_user, data):
    return common_handler(create_draft, subject=None, body=None, to_recipients=None, cc_recipients=None, bcc_recipients=None, importance="normal")(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=send_draft",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="sendDraft",
    description="Sends a draft email message.",
    params={
        "message_id": "ID of the draft message to send"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Draft message ID"}
        },
        "required": ["message_id"]
    }
)
def send_draft_handler(current_user, data):
    return common_handler(send_draft, message_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=reply_to_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="replyToMessage",
    description="Replies to a specific message.",
    params={
        "message_id": "ID of the message to reply to",
        "comment": "Reply content"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "comment": {"type": "string", "description": "Reply content"}
        },
        "required": ["message_id", "comment"]
    }
)
def reply_to_message_handler(current_user, data):
    return common_handler(reply_to_message, message_id=None, comment=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=reply_all_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="replyAllMessage",
    description="Replies to all recipients of a specific message.",
    params={
        "message_id": "ID of the message to reply to",
        "comment": "Reply-all content"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "comment": {"type": "string", "description": "Reply-all content"}
        },
        "required": ["message_id", "comment"]
    }
)
def reply_all_message_handler(current_user, data):
    return common_handler(reply_all_message, message_id=None, comment=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=forward_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="forwardMessage",
    description="Forwards a specific message.",
    params={
        "message_id": "ID of the message to forward",
        "comment": "Optional comment",
        "to_recipients": "List of recipient email addresses"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "comment": {"type": "string", "description": "Forward comment", "default": ""},
            "to_recipients": {"type": "array", "items": {"type": "string"}, "description": "Recipient email addresses"}
        },
        "required": ["message_id", "to_recipients"]
    }
)
def forward_message_handler(current_user, data):
    return common_handler(forward_message, message_id=None, comment="", to_recipients=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=move_message",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="moveMessage",
    description="Moves a specific message to a different folder.",
    params={
        "message_id": "ID of the message to move",
        "destination_folder_id": "Destination folder ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "destination_folder_id": {"type": "string", "description": "Destination folder ID"}
        },
        "required": ["message_id", "destination_folder_id"]
    }
)
def move_message_handler(current_user, data):
    return common_handler(move_message, message_id=None, destination_folder_id=None)(current_user, data)

@op(
    path="/microsoft/integrations/route?op=list_folders",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="listFolders",
    method="GET",
    description="Lists all mail folders.",
    parameters={}
)
def list_folders_handler(current_user, data):
    return common_handler(list_folders)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_folder_details",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="getFolderDetails",
    description="Retrieves details of a specific mail folder.",
    params={
        "folder_id": "Folder ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "folder_id": {"type": "string", "description": "Folder ID"}
        },
        "required": ["folder_id"]
    }
)
def get_folder_details_handler(current_user, data):
    return common_handler(get_folder_details, folder_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=add_attachment",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="addAttachment",
    description="Adds an attachment to a specific message.",
    params={
        "message_id": "Message ID",
        "name": "Attachment file name",
        "content_type": "Attachment MIME type",
        "content_bytes": "Base64 encoded attachment content",
        "is_inline": "Whether the attachment is inline"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "name": {"type": "string", "description": "Attachment name"},
            "content_type": {"type": "string", "description": "Attachment MIME type"},
            "content_bytes": {"type": "string", "description": "Base64 encoded content"},
            "is_inline": {"type": "boolean", "description": "Is the attachment inline", "default": False}
        },
        "required": ["message_id", "name", "content_type", "content_bytes"]
    }
)
def add_attachment_handler(current_user, data):
    return common_handler(add_attachment, message_id=None, name=None, content_type=None, content_bytes=None, is_inline=False)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_attachment",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_write"],
    name="deleteAttachment",
    description="Deletes an attachment from a message.",
    params={
        "message_id": "Message ID",
        "attachment_id": "Attachment ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Message ID"},
            "attachment_id": {"type": "string", "description": "Attachment ID"}
        },
        "required": ["message_id", "attachment_id"]
    }
)
def delete_attachment_handler(current_user, data):
    return common_handler(delete_attachment, message_id=None, attachment_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=search_messages",
    tags=["default", "integration", "microsoft_outlook", "microsoft_outlook_read"],
    name="searchMessages",
    description="Searches messages for a given query string.",
    params={
        "search_query": "Query string to search messages",
        "top": "Maximum number of messages to retrieve",
        "skip": "Number of messages to skip for pagination"
    },
    parameters={
        "type": "object",
        "properties": {
            "search_query": {"type": "string", "description": "Search query"},
            "top": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10, "description": "Maximum messages"},
            "skip": {"type": "integer", "minimum": 0, "default": 0, "description": "Pagination offset"}
        },
        "required": ["search_query"]
    }
)
def search_messages_handler(current_user, data):
    return common_handler(search_messages, search_query=None, top=10, skip=0)(current_user, data)

# --- Calendar Routes ---

@vop(
    path="/microsoft/integrations/route?op=list_calendar_events",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="listCalendarEvents",
    description="Lists events for a given calendar.",
    params={
        "calendar_id": "ID of the calendar to list events from"
    },
    parameters={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "description": "Calendar ID"}
        },
        "required": ["calendar_id"]
    }
)
# @validated("list_calendar_events")
def list_calendar_events_handler(current_user, data):
    return common_handler(list_calendar_events, calendar_id=None)(current_user, data)


@op(
    path="/microsoft/integrations/route?op=list_calendars",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="listCalendars",
    method="GET",
    description="Lists all calendars in the user's mailbox.",
    parameters={}
)
# @validated("list_calendars")
def list_calendars_handler(current_user, data):
    return common_handler(list_calendars)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=create_calendar",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="createCalendar",
    description="Creates a new calendar.",
    params={
        "name": "Name of the new calendar",
        "color": "Optional color for the calendar"
    },
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Calendar name"},
            "color": {"type": "string", "description": "Calendar color"}
        },
        "required": ["name"]
    }
)
# @validated("create_calendar")
def create_calendar_handler(current_user, data):
    return common_handler(create_calendar, name=None, color=None)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=delete_calendar",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="deleteCalendar",
    description="Deletes a calendar.",
    params={
        "calendar_id": "ID of the calendar to delete"
    },
    parameters={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "description": "Calendar ID"}
        },
        "required": ["calendar_id"]
    }
)
# @validated("delete_calendar")
def delete_calendar_handler(current_user, data):
    return common_handler(delete_calendar, calendar_id=None)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=respond_to_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="respondToEvent",
    description="Responds to an event invitation.",
    params={
        "event_id": "ID of the event",
        "response_type": "Response type: accept, decline, or tentativelyAccept",
        "comment": "Optional comment for the response",
        "send_response": "Boolean indicating if a response email should be sent (default true)"
    },
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID"},
            "response_type": {"type": "string", "description": "Response type", "enum": ["accept", "decline", "tentativelyAccept"]},
            "comment": {"type": "string", "description": "Optional comment"},
            "send_response": {"type": "boolean", "description": "Send response email", "default": True}
        },
        "required": ["event_id", "response_type"]
    }
)
# @validated("respond_to_event")
def respond_to_event_handler(current_user, data):
    return common_handler(respond_to_event, event_id=None, response_type=None, comment=None, send_response=True)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=find_meeting_times",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="findMeetingTimes",
    description="Finds available meeting times for a set of attendees.",
    params={
        "attendees": "List of attendee objects with email addresses",
        "duration_minutes": "Duration of meeting in minutes (default 30)",
        "start_time": "Optional start time boundary (ISO format)",
        "end_time": "Optional end time boundary (ISO format)"
    },
    parameters={
        "type": "object",
        "properties": {
            "attendees": {"type": "array", "items": {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}, "description": "List of attendees"},
            "duration_minutes": {"type": "integer", "description": "Meeting duration", "default": 30},
            "start_time": {"type": "string", "description": "Start time boundary"},
            "end_time": {"type": "string", "description": "End time boundary"}
        },
        "required": ["attendees"]
    }
)
# @validated("find_meeting_times")
def find_meeting_times_handler(current_user, data):
    return common_handler(find_meeting_times, attendees=None, duration_minutes=30, start_time=None, end_time=None)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=create_recurring_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="createRecurringEvent",
    description="Creates a recurring calendar event.",
    params={
        "title": "Event title",
        "start_time": "Start time in ISO format",
        "end_time": "End time in ISO format",
        "description": "Event description",
        "recurrence_pattern": "Recurrence pattern object"
    },
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Event title"},
            "start_time": {"type": "string", "description": "Start time"},
            "end_time": {"type": "string", "description": "End time"},
            "description": {"type": "string", "description": "Event description"},
            "recurrence_pattern": {"type": "object", "description": "Recurrence pattern"}
        },
        "required": ["title", "start_time", "end_time", "description", "recurrence_pattern"]
    }
)
# @validated("create_recurring_event")
def create_recurring_event_handler(current_user, data):
    return common_handler(create_recurring_event, title=None, start_time=None, end_time=None, description=None, recurrence_pattern=None)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=update_recurring_event",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="updateRecurringEvent",
    description="Updates a recurring calendar event.",
    params={
        "event_id": "ID of the recurring event",
        "updated_fields": "Dictionary of fields to update",
        "update_type": "Specify 'series' for the entire series or 'occurrence' for a single occurrence (default 'series')"
    },
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID"},
            "updated_fields": {"type": "object", "description": "Fields to update"},
            "update_type": {"type": "string", "description": "Update type", "default": "series"}
        },
        "required": ["event_id", "updated_fields"]
    }
)
# @validated("update_recurring_event")
def update_recurring_event_handler(current_user, data):
    return common_handler(update_recurring_event, event_id=None, updated_fields=None, update_type="series")(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=calendar_add_attachment",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="calendarAddAttachment",
    description="Adds an attachment to a calendar event.",
    params={
        "event_id": "Event ID",
        "file_name": "Name of the file",
        "content_bytes": "Base64 encoded file content",
        "content_type": "MIME type of the file"
    },
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID"},
            "file_name": {"type": "string", "description": "File name"},
            "content_bytes": {"type": "string", "description": "Base64 encoded file content"},
            "content_type": {"type": "string", "description": "MIME type"}
        },
        "required": ["event_id", "file_name", "content_bytes", "content_type"]
    }
)
# @validated("calendar_add_attachment")
def calendar_add_attachment_handler(current_user, data):
    return common_handler(add_attachment, event_id=None, file_name=None, content_bytes=None, content_type=None)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=get_event_attachments",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="getEventAttachments",
    description="Retrieves attachments for a calendar event.",
    params={
        "event_id": "Event ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID"}
        },
        "required": ["event_id"]
    }
)
# @validated("get_event_attachments")
def get_event_attachments_handler(current_user, data):
    return common_handler(get_attachments, event_id=None)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=delete_event_attachment",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="deleteEventAttachment",
    description="Deletes an attachment from a calendar event.",
    params={
        "event_id": "Event ID",
        "attachment_id": "Attachment ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "Event ID"},
            "attachment_id": {"type": "string", "description": "Attachment ID"}
        },
        "required": ["event_id", "attachment_id"]
    }
)
# @validated("delete_event_attachment")
def delete_event_attachment_handler(current_user, data):
    return common_handler(delete_attachment, event_id=None, attachment_id=None)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=get_calendar_permissions",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_read"],
    name="getCalendarPermissions",
    description="Gets sharing permissions for a calendar.",
    params={
        "calendar_id": "Calendar ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "description": "Calendar ID"}
        },
        "required": ["calendar_id"]
    }
)
# @validated("get_calendar_permissions")
def get_calendar_permissions_handler(current_user, data):
    return common_handler(get_calendar_permissions, calendar_id=None)(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=share_calendar",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="shareCalendar",
    description="Shares a calendar with another user.",
    params={
        "calendar_id": "Calendar ID",
        "user_email": "Email address of the user to share with",
        "role": "Permission role: read, write, or owner (default read)"
    },
    parameters={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "description": "Calendar ID"},
            "user_email": {"type": "string", "description": "User email"},
            "role": {"type": "string", "description": "Role", "default": "read", "enum": ["read", "write", "owner"]}
        },
        "required": ["calendar_id", "user_email"]
    }
)
# @validated("share_calendar")
def share_calendar_handler(current_user, data):
    return common_handler(share_calendar, calendar_id=None, user_email=None, role="read")(current_user, data)


@vop(
    path="/microsoft/integrations/route?op=remove_calendar_sharing",
    tags=["default", "integration", "microsoft_calendar", "microsoft_calendar_write"],
    name="removeCalendarSharing",
    description="Removes sharing permissions from a calendar.",
    params={
        "calendar_id": "Calendar ID",
        "permission_id": "Permission ID to remove"
    },
    parameters={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "description": "Calendar ID"},
            "permission_id": {"type": "string", "description": "Permission ID"}
        },
        "required": ["calendar_id", "permission_id"]
    }
)
# @validated("remove_calendar_sharing")
def remove_calendar_sharing_handler(current_user, data):
    return common_handler(remove_calendar_sharing, calendar_id=None, permission_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_worksheet",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="getWorksheet",
    description="Retrieves details of a specific worksheet.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "worksheet_id": "Worksheet identifier"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            "worksheet_id": {"type": "string", "description": "Worksheet identifier"}
        },
        "required": ["item_id", "worksheet_id"]
    }
)
def get_worksheet_handler(current_user, data):
    return common_handler(get_worksheet, item_id=None, worksheet_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_worksheet",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="createWorksheet",
    description="Creates a new worksheet in the workbook.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "name": "Name of the new worksheet"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            "name": {"type": "string", "description": "Name of the new worksheet"}
        },
        "required": ["item_id", "name"]
    }
)
def create_worksheet_handler(current_user, data):
    return common_handler(create_worksheet, item_id=None, name=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_worksheet",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="deleteWorksheet",
    description="Deletes an existing worksheet in the workbook.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "worksheet_id": "Worksheet identifier"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            "worksheet_id": {"type": "string", "description": "Worksheet identifier"}
        },
        "required": ["item_id", "worksheet_id"]
    }
)
def delete_worksheet_handler(current_user, data):
    return common_handler(delete_worksheet, item_id=None, worksheet_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_table",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="createTable",
    description="Creates a new table in the specified worksheet.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "worksheet_id": "Worksheet identifier",
        "address": "Range address for the table (e.g., 'A1:D4')",
        "has_headers": "Specifies if the table has headers (default true)"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            "worksheet_id": {"type": "string", "description": "Worksheet identifier"},
            "address": {"type": "string", "description": "Range address for the table (e.g., 'A1:D4')"},
            "has_headers": {"type": "boolean", "default": True, "description": "Specifies if the table has headers"}
        },
        "required": ["item_id", "worksheet_id", "address"]
    }
)
def create_table_handler(current_user, data):
    return common_handler(create_table, item_id=None, worksheet_id=None, address=None, has_headers=True)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_table",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="deleteTable",
    description="Deletes an existing table from the workbook.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "table_id": "Identifier of the table"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            "table_id": {"type": "string", "description": "Identifier of the table"}
        },
        "required": ["item_id", "table_id"]
    }
)
def delete_table_handler(current_user, data):
    return common_handler(delete_table, item_id=None, table_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_table_range",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="getTableRange",
    description="Retrieves the data range of a specified table.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "table_id": "Identifier of the table"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            "table_id": {"type": "string", "description": "Identifier of the table"}
        },
        "required": ["item_id", "table_id"]
    }
)
def get_table_range_handler(current_user, data):
    return common_handler(get_table_range, item_id=None, table_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=list_charts",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="listCharts",
    description="Lists all charts in a specified worksheet.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "worksheet_id": "Worksheet identifier"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            "worksheet_id": {"type": "string", "description": "Worksheet identifier"}
        },
        "required": ["item_id", "worksheet_id"]
    }
)
def list_charts_handler(current_user, data):
    return common_handler(list_charts, item_id=None, worksheet_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_chart",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_read"],
    name="getChart",
    description="Retrieves details for a specific chart.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "worksheet_id": "Worksheet identifier",
        "chart_id": "Chart identifier"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            "worksheet_id": {"type": "string", "description": "Worksheet identifier"},
            "chart_id": {"type": "string", "description": "Chart identifier"}
        },
        "required": ["item_id", "worksheet_id", "chart_id"]
    }
)
def get_chart_handler(current_user, data):
    return common_handler(get_chart, item_id=None, worksheet_id=None, chart_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_chart",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="createChart",
    description="Creates a new chart in a worksheet.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "worksheet_id": "Worksheet identifier",
        "chart_type": "Type of chart (e.g., 'ColumnClustered', 'Line', etc.)",
        "source_range": "Range address for the data (e.g., 'A1:D5')",
        "series_by": "How series are grouped (e.g., 'Auto', 'Columns', or 'Rows')",
        "title": "Optional chart title"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            "worksheet_id": {"type": "string", "description": "Worksheet identifier"},
            "chart_type": {"type": "string", "description": "Chart type"},
            "source_range": {"type": "string", "description": "Range address for the data"},
            "series_by": {"type": "string", "description": "How series are grouped"},
            "title": {"type": "string", "description": "Optional chart title", "default": ""}
        },
        "required": ["item_id", "worksheet_id", "chart_type", "source_range", "series_by"]
    }
)
def create_chart_handler(current_user, data):
    return common_handler(create_chart, item_id=None, worksheet_id=None, chart_type=None, source_range=None, seriesBy=None, title="")(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_chart",
    tags=["default", "integration", "microsoft_excel", "microsoft_excel_write"],
    name="deleteChart",
    description="Deletes a chart from a worksheet.",
    params={
        "item_id": "OneDrive item ID of the workbook",
        "worksheet_id": "Worksheet identifier",
        "chart_id": "Chart identifier"
    },
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "OneDrive item ID of the workbook"},
            "worksheet_id": {"type": "string", "description": "Worksheet identifier"},
            "chart_id": {"type": "string", "description": "Chart identifier"}
        },
        "required": ["item_id", "worksheet_id", "chart_id"]
    }
)
def delete_chart_handler(current_user, data):
    return common_handler(delete_chart, item_id=None, worksheet_id=None, chart_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=add_comment",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="addComment",
    description="Adds a comment to a specific range in a Word document.",
    params={
        "document_id": "Document ID",
        "text": "Comment text",
        "content_range": "Range to comment on"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "text": {"type": "string", "description": "Comment text"},
            "content_range": {"type": "object", "description": "Range to comment on"}
        },
        "required": ["document_id", "text", "content_range"]
    }
)
def add_comment_handler(current_user, data):
    return common_handler(add_comment, document_id=None, text=None, content_range=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_document_statistics",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="getDocumentStatistics",
    description="Gets document statistics including word count, page count, etc.",
    params={
        "document_id": "Document ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"}
        },
        "required": ["document_id"]
    }
)
def get_document_statistics_handler(current_user, data):
    return common_handler(get_document_statistics, document_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=search_document",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="searchDocument",
    description="Searches for text within a document.",
    params={
        "document_id": "Document ID",
        "search_text": "Text to search for"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "search_text": {"type": "string", "description": "Text to search for"}
        },
        "required": ["document_id", "search_text"]
    }
)
def search_document_handler(current_user, data):
    return common_handler(search_document, document_id=None, search_text=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=apply_formatting",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="applyFormatting",
    description="Applies formatting to a specific range in the document.",
    params={
        "document_id": "Document ID",
        "format_range": "Range to format",
        "formatting": "Formatting options"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "format_range": {"type": "object", "description": "Range to format"},
            "formatting": {"type": "object", "description": "Formatting options"}
        },
        "required": ["document_id", "format_range", "formatting"]
    }
)
def apply_formatting_handler(current_user, data):
    return common_handler(apply_formatting, document_id=None, format_range=None, formatting=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_document_sections",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="getDocumentSections",
    description="Gets all sections/paragraphs in a document.",
    params={
        "document_id": "Document ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"}
        },
        "required": ["document_id"]
    }
)
def get_document_sections_handler(current_user, data):
    return common_handler(get_document_sections, document_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=insert_section",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="insertSection",
    description="Inserts a new section/paragraph at a specific position in the document.",
    params={
        "document_id": "Document ID",
        "content": "Content to insert",
        "position": "Position to insert at"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "content": {"type": "string", "description": "Content to insert"},
            "position": {"type": "integer", "description": "Position to insert at"}
        },
        "required": ["document_id", "content"]
    }
)
def insert_section_handler(current_user, data):
    return common_handler(insert_section, document_id=None, content=None, position=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=replace_text",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="replaceText",
    description="Replaces all occurrences of text in a document.",
    params={
        "document_id": "Document ID",
        "search_text": "Text to find",
        "replace_text": "Text to replace with"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "search_text": {"type": "string", "description": "Text to find"},
            "replace_text": {"type": "string", "description": "Text to replace with"}
        },
        "required": ["document_id", "search_text", "replace_text"]
    }
)
def replace_text_handler(current_user, data):
    return common_handler(replace_text, document_id=None, search_text=None, replace_text=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_table",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="createTable",
    description="Inserts a new table into the document.",
    params={
        "document_id": "Document ID",
        "rows": "Number of rows",
        "columns": "Number of columns",
        "position": "Position to insert table"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "rows": {"type": "integer", "description": "Number of rows"},
            "columns": {"type": "integer", "description": "Number of columns"},
            "position": {"type": "object", "description": "Position to insert table"}
        },
        "required": ["document_id", "rows", "columns"]
    }
)
def create_table_handler(current_user, data):
    return common_handler(create_table, document_id=None, rows=None, columns=None, position=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=update_table_cell",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="updateTableCell",
    description="Updates content and formatting of a table cell.",
    params={
        "document_id": "Document ID",
        "table_id": "ID of the table",
        "row": "Row index",
        "column": "Column index",
        "content": "Cell content",
        "formatting": "Cell formatting"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "table_id": {"type": "string", "description": "ID of the table"},
            "row": {"type": "integer", "description": "Row index"},
            "column": {"type": "integer", "description": "Column index"},
            "content": {"type": "string", "description": "Cell content"},
            "formatting": {"type": "object", "description": "Cell formatting"}
        },
        "required": ["document_id", "table_id", "row", "column", "content"]
    }
)
def update_table_cell_handler(current_user, data):
    return common_handler(update_table_cell, document_id=None, table_id=None, row=None, column=None, content=None, formatting=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_list",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="createList",
    description="Creates a bulleted or numbered list in the document.",
    params={
        "document_id": "Document ID",
        "items": "List of items to add",
        "list_type": "Type of list",
        "position": "Position to insert list"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "items": {"type": "array", "items": {"type": "string"}, "description": "List of items to add"},
            "list_type": {"type": "string", "description": "Type of list", "default": "bullet"},
            "position": {"type": "object", "description": "Position to insert list"}
        },
        "required": ["document_id", "items"]
    }
)
def create_list_handler(current_user, data):
    return common_handler(create_list, document_id=None, items=None, list_type="bullet", position=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=insert_page_break",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="insertPageBreak",
    description="Inserts a page break at the specified position.",
    params={
        "document_id": "Document ID",
        "position": "Position to insert page break"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "position": {"type": "object", "description": "Position to insert page break"}
        },
        "required": ["document_id"]
    }
)
def insert_page_break_handler(current_user, data):
    return common_handler(insert_page_break, document_id=None, position=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=set_header_footer",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="setHeaderFooter",
    description="Sets the header or footer content for the document.",
    params={
        "document_id": "Document ID",
        "content": "Content to set",
        "is_header": "True for header, False for footer"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "content": {"type": "string", "description": "Content to set"},
            "is_header": {"type": "boolean", "description": "True for header, False for footer", "default": True}
        },
        "required": ["document_id", "content"]
    }
)
def set_header_footer_handler(current_user, data):
    return common_handler(set_header_footer, document_id=None, content=None, is_header=True)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=insert_image",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="insertImage",
    description="Inserts an image into the document.",
    params={
        "document_id": "Document ID",
        "image_data": "Image bytes",
        "position": "Position to insert image",
        "name": "Image name"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "image_data": {"type": "string", "description": "Image bytes"},
            "position": {"type": "object", "description": "Position to insert image"},
            "name": {"type": "string", "description": "Image name"}
        },
        "required": ["document_id", "image_data"]
    }
)
def insert_image_handler(current_user, data):
    return common_handler(insert_image, document_id=None, image_data=None, position=None, name=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_document_versions",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="getDocumentVersions",
    description="Gets version history of a document.",
    params={
        "document_id": "Document ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"}
        },
        "required": ["document_id"]
    }
)
def get_document_versions_handler(current_user, data):
    return common_handler(get_document_versions, document_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=restore_version",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="restoreVersion",
    description="Restores a previous version of a document.",
    params={
        "document_id": "Document ID",
        "version_id": "Version ID to restore"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "version_id": {"type": "string", "description": "Version ID to restore"}
        },
        "required": ["document_id", "version_id"]
    }
)
def restore_version_handler(current_user, data):
    return common_handler(restore_version, document_id=None, version_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=delete_document",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="deleteDocument",
    description="Deletes a Word document.",
    params={
        "document_id": "Document ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"}
        },
        "required": ["document_id"]
    }
)
def delete_document_handler(current_user, data):
    return common_handler(delete_document, document_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=list_documents",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="listDocuments",
    description="Lists Word documents in a folder or root.",
    params={
        "folder_path": "Folder path"
    },
    parameters={
        "type": "object",
        "properties": {
            "folder_path": {"type": "string", "description": "Folder path"}
        }
    }
)
def list_documents_handler(current_user, data):
    return common_handler(list_documents, folder_path=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=share_document",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="shareDocument",
    description="Shares a Word document with another user.",
    params={
        "document_id": "Document ID",
        "user_email": "Email of the user to share with",
        "permission_level": "Permission level"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "user_email": {"type": "string", "description": "Email of the user to share with"},
            "permission_level": {"type": "string", "description": "Permission level", "default": "read"}
        },
        "required": ["document_id", "user_email"]
    }
)
def share_document_handler(current_user, data):
    return common_handler(share_document, document_id=None, user_email=None, permission_level="read")(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_document_permissions",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="getDocumentPermissions",
    description="Gets sharing permissions for a document.",
    params={
        "document_id": "Document ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"}
        },
        "required": ["document_id"]
    }
)
def get_document_permissions_handler(current_user, data):
    return common_handler(get_document_permissions, document_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=remove_permission",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="removePermission",
    description="Removes a sharing permission from a document.",
    params={
        "document_id": "Document ID",
        "permission_id": "Permission ID to remove"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "permission_id": {"type": "string", "description": "Permission ID to remove"}
        },
        "required": ["document_id", "permission_id"]
    }
)
def remove_permission_handler(current_user, data):
    return common_handler(remove_permission, document_id=None, permission_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=get_document_content",
    tags=["default", "integration", "microsoft_word", "microsoft_word_read"],
    name="getDocumentContent",
    description="Gets the content of a Word document.",
    params={
        "document_id": "Document ID"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"}
        },
        "required": ["document_id"]
    }
)
def get_document_content_handler(current_user, data):
    return common_handler(get_document_content, document_id=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=update_document_content",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="updateDocumentContent",
    description="Updates the content of a Word document.",
    params={
        "document_id": "Document ID",
        "content": "New content"
    },
    parameters={
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID"},
            "content": {"type": "string", "description": "New content"}
        },
        "required": ["document_id", "content"]
    }
)
def update_document_content_handler(current_user, data):
    return common_handler(update_document_content, document_id=None, content=None)(current_user, data)

@vop(
    path="/microsoft/integrations/route?op=create_document",
    tags=["default", "integration", "microsoft_word", "microsoft_word_write"],
    name="createDocument",
    description="Creates a new Word document.",
    params={
        "name": "Name of the document",
        "content": "Initial content",
        "folder_path": "Folder path"
    },
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the document"},
            "content": {"type": "string", "description": "Initial content"},
            "folder_path": {"type": "string", "description": "Folder path"}
        },
        "required": ["name"]
    }
)
def create_document_handler(current_user, data):
    return common_handler(create_document, name=None, content=None, folder_path=None)(current_user, data)

