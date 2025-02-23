from common.permissions import get_permission_checker
import json
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from common.encoders import CombinedEncoder
from boto3.dynamodb.conditions import Key

import os
import requests
from jose import jwt

from dotenv import load_dotenv

import boto3
from datetime import datetime
import re

load_dotenv(dotenv_path=".env.local")

ALGORITHMS = ["RS256"]


class HTTPException(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class BadRequest(HTTPException):
    def __init__(self, message="Bad Request"):
        super().__init__(400, message)


class Unauthorized(HTTPException):
    def __init__(self, message="Unauthorized"):
        super().__init__(401, message)


class NotFound(HTTPException):
    def __init__(self, message="Not Found"):
        super().__init__(404, message)


"""
Every service must define the permissions for each operation here. 
The permission is related to a request path and to a specific operation.
"""

# Define comprehensive route data schema for all operations
route_data_schema = {
    "type": "object",
    "anyOf": [
        # OneDrive endpoints
        {
            "description": "list_drive_items",
            "type": "object",
            "properties": {
                "folder_id": {"type": "string", "default": "root"},
                "page_size": {"type": "integer", "minimum": 1, "default": 25}
            },
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "upload_file",
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "file_content": {"type": "string"},
                "folder_id": {"type": "string", "default": "root"}
            },
            "required": ["file_path", "file_content"],
            "additionalProperties": False
        },
        {
            "description": "download_file",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"}
            },
            "required": ["item_id"],
            "additionalProperties": False
        },
        {
            "description": "delete_item",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"}
            },
            "required": ["item_id"],
            "additionalProperties": False
        },
        {
            "description": "get_drive_item",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"}
            },
            "required": ["item_id"],
            "additionalProperties": False
        },
        {
            "description": "create_folder",
            "type": "object",
            "properties": {
                "folder_name": {"type": "string"},
                "parent_folder_id": {"type": "string", "default": "root"}
            },
            "required": ["folder_name"],
            "additionalProperties": False
        },
        {
            "description": "update_drive_item",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "updates": {"type": "object"}
            },
            "required": ["item_id", "updates"],
            "additionalProperties": False
        },
        {
            "description": "copy_drive_item",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "new_name": {"type": "string"},
                "parent_folder_id": {"type": "string", "default": "root"}
            },
            "required": ["item_id", "new_name"],
            "additionalProperties": False
        },
        {
            "description": "move_drive_item",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "new_parent_id": {"type": "string"}
            },
            "required": ["item_id", "new_parent_id"],
            "additionalProperties": False
        },
        {
            "description": "create_sharing_link",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "link_type": {"type": "string", "default": "view"},
                "scope": {"type": "string", "default": "anonymous"}
            },
            "required": ["item_id"],
            "additionalProperties": False
        },
        {
            "description": "invite_to_drive_item",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "recipients": {"type": "array", "items": {"type": "object"}},
                "message": {"type": "string", "default": ""},
                "require_sign_in": {"type": "boolean", "default": True},
                "send_invitation": {"type": "boolean", "default": True},
                "roles": {"type": "array", "items": {"type": "string"}, "default": ["read"]}
            },
            "required": ["item_id", "recipients"],
            "additionalProperties": False
        },
        # Excel endpoints
        {
            "description": "list_worksheets",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"}
            },
            "required": ["item_id"],
            "additionalProperties": False
        },
        {
            "description": "list_tables",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "worksheet_name": {"type": "string"}
            },
            "required": ["item_id"],
            "additionalProperties": False
        },
        {
            "description": "add_row_to_table",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "table_name": {"type": "string"},
                "row_values": {"type": "array"}
            },
            "required": ["item_id", "table_name", "row_values"],
            "additionalProperties": False
        },
        {
            "description": "read_range",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "worksheet_name": {"type": "string"},
                "address": {"type": "string"}
            },
            "required": ["item_id", "worksheet_name", "address"],
            "additionalProperties": False
        },
        {
            "description": "update_range",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "worksheet_name": {"type": "string"},
                "address": {"type": "string"},
                "values": {"type": "array"}
            },
            "required": ["item_id", "worksheet_name", "address", "values"],
            "additionalProperties": False
        },
        # Outlook endpoints
        {
            "description": "list_messages",
            "type": "object",
            "properties": {
                "folder_id": {"type": "string", "default": "Inbox"},
                "top": {"type": "integer", "minimum": 1, "default": 10},
                "skip": {"type": "integer", "minimum": 0, "default": 0},
                "filter_query": {"type": "string"}
            },
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "get_message_details",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "include_body": {"type": "boolean", "default": True}
            },
            "required": ["message_id"],
            "additionalProperties": False
        },
        {
            "description": "send_mail",
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "to_recipients": {"type": "array", "items": {"type": "string"}},
                "cc_recipients": {"type": "array", "items": {"type": "string"}},
                "bcc_recipients": {"type": "array", "items": {"type": "string"}},
                "importance": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"}
            },
            "required": ["subject", "body", "to_recipients"],
            "additionalProperties": False
        },
        {
            "description": "delete_message",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"}
            },
            "required": ["message_id"],
            "additionalProperties": False
        },
        {
            "description": "get_attachments",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"}
            },
            "required": ["message_id"],
            "additionalProperties": False
        },
        {
            "description": "update_message",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "changes": {"type": "object"}
            },
            "required": ["message_id", "changes"],
            "additionalProperties": False
        },
        {
            "description": "create_draft",
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "to_recipients": {"type": "array", "items": {"type": "string"}},
                "cc_recipients": {"type": "array", "items": {"type": "string"}},
                "bcc_recipients": {"type": "array", "items": {"type": "string"}},
                "importance": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"}
            },
            "required": ["subject", "body"],
            "additionalProperties": False
        },
        {
            "description": "send_draft",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"}
            },
            "required": ["message_id"],
            "additionalProperties": False
        },
        {
            "description": "reply_to_message",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "comment": {"type": "string"}
            },
            "required": ["message_id", "comment"],
            "additionalProperties": False
        },
        {
            "description": "reply_all_message",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "comment": {"type": "string"}
            },
            "required": ["message_id", "comment"],
            "additionalProperties": False
        },
        {
            "description": "forward_message",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "comment": {"type": "string", "default": ""},
                "to_recipients": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["message_id", "to_recipients"],
            "additionalProperties": False
        },
        {
            "description": "move_message",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "destination_folder_id": {"type": "string"}
            },
            "required": ["message_id", "destination_folder_id"],
            "additionalProperties": False
        },
        {
            "description": "list_folders",
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "get_folder_details",
            "type": "object",
            "properties": {
                "folder_id": {"type": "string"}
            },
            "required": ["folder_id"],
            "additionalProperties": False
        },
        {
            "description": "add_attachment",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "name": {"type": "string"},
                "content_type": {"type": "string"},
                "content_bytes": {"type": "string"},
                "is_inline": {"type": "boolean", "default": False}
            },
            "required": ["message_id", "name", "content_type", "content_bytes"],
            "additionalProperties": False
        },
        {
            "description": "delete_attachment",
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "attachment_id": {"type": "string"}
            },
            "required": ["message_id", "attachment_id"],
            "additionalProperties": False
        },
        {
            "description": "search_messages",
            "type": "object",
            "properties": {
                "search_query": {"type": "string"},
                "top": {"type": "integer", "minimum": 1, "default": 10},
                "skip": {"type": "integer", "minimum": 0, "default": 0}
            },
            "required": ["search_query"],
            "additionalProperties": False
        },
        # Planner endpoints
        {
            "description": "list_plans_in_group",
            "type": "object",
            "properties": {
                "group_id": {"type": "string"}
            },
            "required": ["group_id"],
            "additionalProperties": False
        },
        {
            "description": "list_buckets_in_plan",
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"}
            },
            "required": ["plan_id"],
            "additionalProperties": False
        },
        {
            "description": "list_tasks_in_plan",
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "include_details": {"type": "boolean", "default": False}
            },
            "required": ["plan_id"],
            "additionalProperties": False
        },
        {
            "description": "create_task",
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "bucket_id": {"type": "string"},
                "title": {"type": "string"},
                "assignments": {"type": "object"},
                "due_date": {"type": "string"},
                "priority": {"type": "integer", "minimum": 1}
            },
            "required": ["plan_id", "bucket_id", "title"],
            "additionalProperties": False
        },
        {
            "description": "update_task",
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "e_tag": {"type": "string"},
                "update_fields": {"type": "object"}
            },
            "required": ["task_id", "e_tag", "update_fields"],
            "additionalProperties": False
        },
        {
            "description": "delete_task",
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "e_tag": {"type": "string"}
            },
            "required": ["task_id", "e_tag"],
            "additionalProperties": False
        },
        # SharePoint endpoints
        {
            "description": "list_sites",
            "type": "object",
            "properties": {
                "search_query": {"type": "string"},
                "top": {"type": "integer", "minimum": 1, "default": 10},
                "skip": {"type": "integer", "minimum": 0, "default": 0}
            },
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "get_site_by_path",
            "type": "object",
            "properties": {
                "hostname": {"type": "string"},
                "site_path": {"type": "string"}
            },
            "required": ["hostname"],
            "additionalProperties": False
        },
        {
            "description": "list_site_lists",
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "top": {"type": "integer", "minimum": 1, "default": 10},
                "skip": {"type": "integer", "minimum": 0, "default": 0}
            },
            "required": ["site_id"],
            "additionalProperties": False
        },
        {
            "description": "get_list_items",
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "list_id": {"type": "string"},
                "expand_fields": {"type": "boolean", "default": True},
                "top": {"type": "integer", "minimum": 1, "default": 10},
                "skip": {"type": "integer", "minimum": 0, "default": 0},
                "filter_query": {"type": "string"}
            },
            "required": ["site_id", "list_id"],
            "additionalProperties": False
        },
        {
            "description": "create_list_item",
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "list_id": {"type": "string"},
                "fields_dict": {"type": "object"}
            },
            "required": ["site_id", "list_id", "fields_dict"],
            "additionalProperties": False
        },
        {
            "description": "update_list_item",
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "list_id": {"type": "string"},
                "item_id": {"type": "string"},
                "fields_dict": {"type": "object"}
            },
            "required": ["site_id", "list_id", "item_id", "fields_dict"],
            "additionalProperties": False
        },
        {
            "description": "delete_list_item",
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "list_id": {"type": "string"},
                "item_id": {"type": "string"}
            },
            "required": ["site_id", "list_id", "item_id"],
            "additionalProperties": False
        },
        # Teams endpoints
        {
            "description": "list_teams",
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "list_channels",
            "type": "object",
            "properties": {
                "team_id": {"type": "string"}
            },
            "required": ["team_id"],
            "additionalProperties": False
        },
        {
            "description": "create_channel",
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"}
            },
            "required": ["team_id", "name"],
            "additionalProperties": False
        },
        {
            "description": "send_channel_message",
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "channel_id": {"type": "string"},
                "message": {"type": "string"},
                "importance": {"type": "string", "enum": ["normal", "high", "urgent"], "default": "normal"}
            },
            "required": ["team_id", "channel_id", "message"],
            "additionalProperties": False
        },
        {
            "description": "get_chat_messages",
            "type": "object",
            "properties": {
                "chat_id": {"type": "string"},
                "top": {"type": "integer", "minimum": 1, "default": 50}
            },
            "required": ["chat_id"],
            "additionalProperties": False
        },
        {
            "description": "schedule_meeting",
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "subject": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["team_id", "subject", "start_time", "end_time"],
            "additionalProperties": False
        },
        # User Groups endpoints
        {
            "description": "list_users",
            "type": "object",
            "properties": {
                "search_query": {"type": "string"},
                "top": {"type": "integer", "minimum": 1, "default": 10},
                "skip": {"type": "integer", "minimum": 0, "default": 0},
                "order_by": {"type": "string"}
            },
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "get_user_details",
            "type": "object",
            "properties": {
                "user_id": {"type": "string"}
            },
            "required": ["user_id"],
            "additionalProperties": False
        },
        {
            "description": "list_groups",
            "type": "object",
            "properties": {
                "search_query": {"type": "string"},
                "group_type": {"type": "string", "enum": ["Unified", "Security"]},
                "top": {"type": "integer", "minimum": 1, "default": 10},
                "skip": {"type": "integer", "minimum": 0, "default": 0}
            },
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "get_group_details",
            "type": "object",
            "properties": {
                "group_id": {"type": "string"}
            },
            "required": ["group_id"],
            "additionalProperties": False
        },
        {
            "description": "create_group",
            "type": "object",
            "properties": {
                "display_name": {"type": "string"},
                "mail_nickname": {"type": "string"},
                "group_type": {"type": "string", "enum": ["Unified", "Security"], "default": "Unified"},
                "description": {"type": "string"},
                "owners": {"type": "array", "items": {"type": "string"}},
                "members": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["display_name"],
            "additionalProperties": False
        },
        {
            "description": "delete_group",
            "type": "object",
            "properties": {
                "group_id": {"type": "string"}
            },
            "required": ["group_id"],
            "additionalProperties": False
        },
        # OneNote endpoints
        {
            "description": "list_notebooks",
            "type": "object",
            "properties": {
                "top": {"type": "integer", "minimum": 1, "default": 10}
            },
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "list_sections_in_notebook",
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string"}
            },
            "required": ["notebook_id"],
            "additionalProperties": False
        },
        {
            "description": "list_pages_in_section",
            "type": "object",
            "properties": {
                "section_id": {"type": "string"}
            },
            "required": ["section_id"],
            "additionalProperties": False
        },
        {
            "description": "create_page_in_section",
            "type": "object",
            "properties": {
                "section_id": {"type": "string"},
                "title": {"type": "string"},
                "html_content": {"type": "string"}
            },
            "required": ["section_id", "title", "html_content"],
            "additionalProperties": False
        },
        {
            "description": "get_page_content",
            "type": "object",
            "properties": {
                "page_id": {"type": "string"}
            },
            "required": ["page_id"],
            "additionalProperties": False
        },
        {
            "description": "create_page_with_image_and_attachment",
            "type": "object",
            "properties": {
                "section_id": {"type": "string"},
                "title": {"type": "string"},
                "html_body": {"type": "string"},
                "image_name": {"type": "string"},
                "image_content": {"type": "string"},
                "image_content_type": {"type": "string"},
                "file_name": {"type": "string"},
                "file_content": {"type": "string"},
                "file_content_type": {"type": "string"}
            },
            "required": ["section_id", "title", "html_body", "image_name", "image_content", "image_content_type", "file_name", "file_content", "file_content_type"],
            "additionalProperties": False
        },
        # Contacts endpoints
        {
            "description": "list_contacts",
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "minimum": 1, "default": 10}
            },
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "get_contact_details",
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"}
            },
            "required": ["contact_id"],
            "additionalProperties": False
        },
        {
            "description": "create_contact",
            "type": "object",
            "properties": {
                "given_name": {"type": "string"},
                "surname": {"type": "string"},
                "email_addresses": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["email_addresses"],
            "additionalProperties": False
        },
        {
            "description": "delete_contact",
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"}
            },
            "required": ["contact_id"],
            "additionalProperties": False
        },
        # Calendar endpoints
        {
            "description": "create_event",
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
                "description": {"type": "string", "default": ""}
            },
            "required": ["title", "start_time", "end_time"],
            "additionalProperties": False
        },
        {
            "description": "update_event",
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "updated_fields": {"type": "object"}
            },
            "required": ["event_id", "updated_fields"],
            "additionalProperties": False
        },
        {
            "description": "delete_event",
            "type": "object",
            "properties": {
                "event_id": {"type": "string"}
            },
            "required": ["event_id"],
            "additionalProperties": False
        },
        {
            "description": "get_event_details",
            "type": "object",
            "properties": {
                "event_id": {"type": "string"}
            },
            "required": ["event_id"],
            "additionalProperties": False
        },
        {
            "description": "get_events_between_dates",
            "type": "object",
            "properties": {
                "start_dt": {"type": "string"},
                "end_dt": {"type": "string"},
                "page_size": {"type": "integer", "minimum": 1, "default": 50}
            },
            "required": ["start_dt", "end_dt"],
            "additionalProperties": False
        },
        {
            "description": "list_calendar_events",
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"}
            },
            "required": ["calendar_id"],
            "additionalProperties": False
        },
        {
            "description": "list_calendars",
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "create_calendar",
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "color": {"type": "string"}
            },
            "required": ["name"],
            "additionalProperties": False
        },
        {
            "description": "delete_calendar",
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"}
            },
            "required": ["calendar_id"],
            "additionalProperties": False
        },
        {
            "description": "respond_to_event",
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "response_type": {"type": "string", "enum": ["accept", "decline", "tentativelyAccept"]},
                "comment": {"type": "string"},
                "send_response": {"type": "boolean", "default": True}
            },
            "required": ["event_id", "response_type"],
            "additionalProperties": False
        },
        {
            "description": "find_meeting_times",
            "type": "object",
            "properties": {
                "attendees": {"type": "array", "items": {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}},
                "duration_minutes": {"type": "integer", "default": 30},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"}
            },
            "required": ["attendees"],
            "additionalProperties": False
        },
        {
            "description": "create_recurring_event",
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
                "description": {"type": "string"},
                "recurrence_pattern": {"type": "object"}
            },
            "required": ["title", "start_time", "end_time", "description", "recurrence_pattern"],
            "additionalProperties": False
        },
        {
            "description": "update_recurring_event",
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "updated_fields": {"type": "object"},
                "update_type": {"type": "string", "default": "series"}
            },
            "required": ["event_id", "updated_fields"],
            "additionalProperties": False
        },
        {
            "description": "calendar_add_attachment",
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "file_name": {"type": "string"},
                "content_bytes": {"type": "string"},
                "content_type": {"type": "string"}
            },
            "required": ["event_id", "file_name", "content_bytes", "content_type"],
            "additionalProperties": False
        },
        {
            "description": "get_event_attachments",
            "type": "object",
            "properties": {
                "event_id": {"type": "string"}
            },
            "required": ["event_id"],
            "additionalProperties": False
        },
        {
            "description": "delete_event_attachment",
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "attachment_id": {"type": "string"}
            },
            "required": ["event_id", "attachment_id"],
            "additionalProperties": False
        },
        {
            "description": "get_calendar_permissions",
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"}
            },
            "required": ["calendar_id"],
            "additionalProperties": False
        },
        {
            "description": "share_calendar",
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"},
                "user_email": {"type": "string"},
                "role": {"type": "string", "enum": ["read", "write", "owner"], "default": "read"}
            },
            "required": ["calendar_id", "user_email"],
            "additionalProperties": False
        },
        {
            "description": "remove_calendar_sharing",
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"},
                "permission_id": {"type": "string"}
            },
            "required": ["calendar_id", "permission_id"],
            "additionalProperties": False
        },
        {
            "description": "get_worksheet",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "worksheet_id": {"type": "string"}
            },
            "required": ["item_id", "worksheet_id"],
            "additionalProperties": False
        },
        {
            "description": "create_worksheet",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "name": {"type": "string"}
            },
            "required": ["item_id", "name"],
            "additionalProperties": False
        },
        {
            "description": "delete_worksheet",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "worksheet_id": {"type": "string"}
            },
            "required": ["item_id", "worksheet_id"],
            "additionalProperties": False
        },
        {
            "description": "create_table",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "worksheet_id": {"type": "string"},
                "address": {"type": "string"},
                "has_headers": {"type": "boolean", "default": True}
            },
            "required": ["item_id", "worksheet_id", "address"],
            "additionalProperties": False
        },
        {
            "description": "delete_table",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "table_id": {"type": "string"}
            },
            "required": ["item_id", "table_id"],
            "additionalProperties": False
        },
        {
            "description": "get_table_range",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "table_id": {"type": "string"}
            },
            "required": ["item_id", "table_id"],
            "additionalProperties": False
        },
        {
            "description": "list_charts",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "worksheet_id": {"type": "string"}
            },
            "required": ["item_id", "worksheet_id"],
            "additionalProperties": False
        },
        {
            "description": "get_chart",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "worksheet_id": {"type": "string"},
                "chart_id": {"type": "string"}
            },
            "required": ["item_id", "worksheet_id", "chart_id"],
            "additionalProperties": False
        },
        {
            "description": "create_chart",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "worksheet_id": {"type": "string"},
                "chart_type": {"type": "string"},
                "source_range": {"type": "string"},
                "series_by": {"type": "string"},
                "title": {"type": "string", "default": ""}
            },
            "required": ["item_id", "worksheet_id", "chart_type", "source_range", "series_by"],
            "additionalProperties": False
        },
        {
            "description": "delete_chart",
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "worksheet_id": {"type": "string"},
                "chart_id": {"type": "string"}
            },
            "required": ["item_id", "worksheet_id", "chart_id"],
            "additionalProperties": False
        },
        {
            "description": "add_comment",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "text": {"type": "string"},
                "content_range": {"type": "object"}
            },
            "required": ["document_id", "text", "content_range"],
            "additionalProperties": False
        },
        {
            "description": "get_document_statistics",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"}
            },
            "required": ["document_id"],
            "additionalProperties": False
        },
        {
            "description": "search_document",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "search_text": {"type": "string"}
            },
            "required": ["document_id", "search_text"],
            "additionalProperties": False
        },
        {
            "description": "apply_formatting",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "format_range": {"type": "object"},
                "formatting": {"type": "object"}
            },
            "required": ["document_id", "format_range", "formatting"],
            "additionalProperties": False
        },
        {
            "description": "get_document_sections",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"}
            },
            "required": ["document_id"],
            "additionalProperties": False
        },
        {
            "description": "insert_section",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "content": {"type": "string"},
                "position": {"type": "integer"}
            },
            "required": ["document_id", "content"],
            "additionalProperties": False
        },
        {
            "description": "replace_text",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "search_text": {"type": "string"},
                "replace_text": {"type": "string"}
            },
            "required": ["document_id", "search_text", "replace_text"],
            "additionalProperties": False
        },
        {
            "description": "create_table",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "rows": {"type": "integer"},
                "columns": {"type": "integer"},
                "position": {"type": "object"}
            },
            "required": ["document_id", "rows", "columns"],
            "additionalProperties": False
        },
        {
            "description": "update_table_cell",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "table_id": {"type": "string"},
                "row": {"type": "integer"},
                "column": {"type": "integer"},
                "content": {"type": "string"},
                "formatting": {"type": "object"}
            },
            "required": ["document_id", "table_id", "row", "column", "content"],
            "additionalProperties": False
        },
        {
            "description": "create_list",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "items": {"type": "array", "items": {"type": "string"}},
                "list_type": {"type": "string", "default": "bullet"},
                "position": {"type": "object"}
            },
            "required": ["document_id", "items"],
            "additionalProperties": False
        },
        {
            "description": "insert_page_break",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "position": {"type": "object"}
            },
            "required": ["document_id"],
            "additionalProperties": False
        },
        {
            "description": "set_header_footer",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "content": {"type": "string"},
                "is_header": {"type": "boolean", "default": True}
            },
            "required": ["document_id", "content"],
            "additionalProperties": False
        },
        {
            "description": "insert_image",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "image_data": {"type": "string"},
                "position": {"type": "object"},
                "name": {"type": "string"}
            },
            "required": ["document_id", "image_data"],
            "additionalProperties": False
        },
        {
            "description": "get_document_versions",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"}
            },
            "required": ["document_id"],
            "additionalProperties": False
        },
        {
            "description": "restore_version",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "version_id": {"type": "string"}
            },
            "required": ["document_id", "version_id"],
            "additionalProperties": False
        },
        {
            "description": "delete_document",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"}
            },
            "required": ["document_id"],
            "additionalProperties": False
        },
        {
            "description": "list_documents",
            "type": "object",
            "properties": {
                "folder_path": {"type": "string"}
            },
            "required": [],
            "additionalProperties": False
        },
        {
            "description": "share_document",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "user_email": {"type": "string"},
                "permission_level": {"type": "string", "default": "read"}
            },
            "required": ["document_id", "user_email"],
            "additionalProperties": False
        },
        {
            "description": "get_document_permissions",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"}
            },
            "required": ["document_id"],
            "additionalProperties": False
        },
        {
            "description": "remove_permission",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "permission_id": {"type": "string"}
            },
            "required": ["document_id", "permission_id"],
            "additionalProperties": False
        },
        {
            "description": "get_document_content",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"}
            },
            "required": ["document_id"],
            "additionalProperties": False
        },
        {
            "description": "update_document_content",
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["document_id", "content"],
            "additionalProperties": False
        },
        {
            "description": "create_document",
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string"},
                "folder_path": {"type": "string"}
            },
            "required": ["name"],
            "additionalProperties": False
        }
    ]
}



validators = {

     "/microsoft/integrations/route" : {
        "route" : route_data_schema
    },

    "/microsoft/integrations" : {
        "get" : {},
    },
}

api_validators = validators



def validate_data(name, op, data, api_accessed):
    validator = api_validators if api_accessed else validators
    if name in validator and op in validator[name]:
        schema = validator[name][op]
        try:
            validate(instance=data.get("data"), schema=schema)
        except ValidationError as e:
            print(e)
            raise ValidationError(f"Invalid data: {e.message}")
        print("Data validated")
    else:
        print(f"Invalid data or path: {name} - op:{op} - data: {data}")
        raise Exception("Invalid data or path")


def parse_and_validate(current_user, event, op, api_accessed, validate_body=True):
    data = {}
    if validate_body:
        try:
            data = json.loads(event["body"]) if event.get("body") else {}
        except json.decoder.JSONDecodeError as e:
            raise BadRequest("Unable to parse JSON body.")

    name = event["path"] if event.get("path") else "/"

    if not name:
        raise BadRequest("Unable to perform the operation, invalid request.")

    try:
        if validate_body:
            validate_data(name, op, data, api_accessed)
    except ValidationError as e:
        raise BadRequest(e.message)

    permission_checker = get_permission_checker(current_user, name, op, data)

    if not permission_checker(current_user, data):
        # Return a 403 Forbidden if the user does not have permission to append data to this item
        raise Unauthorized("User does not have permission to perform the operation.")

    return [name, data]


def validated(op, validate_body=True):
    def decorator(f):
        def wrapper(event, context):
            try:
                token = parseToken(event)
                api_accessed = token[:4] == "amp-"

                claims = (
                    api_claims(event, context, token)
                    if (api_accessed)
                    else get_claims(event, context, token)
                )

                current_user = claims["username"]
                print(f"User: {current_user}")
                if current_user is None:
                    raise Unauthorized("User not found.")

                [name, data] = parse_and_validate(
                    current_user, event, op, api_accessed, validate_body
                )

                data["access_token"] = token
                data["account"] = claims["account"]
                data["allowed_access"] = claims["allowed_access"]
                data["api_accessed"] = api_accessed

                # additional validator change from other lambdas
                data["is_group_sys_user"] = claims.get("is_group_sys_user", False)
                ###

                result = f(event, context, current_user, name, data)

                return {
                    "statusCode": 200,
                    "body": json.dumps(result, cls=CombinedEncoder),
                }
            except HTTPException as e:
                return {
                    "statusCode": e.status_code,
                    "body": json.dumps({"error": f"Error: {e.status_code} - {e}"}),
                }

        return wrapper

    return decorator


def get_claims(event, context, token):
    # https://cognito-idp.<Region>.amazonaws.com/<userPoolId>/.well-known/jwks.json

    oauth_issuer_base_url = os.getenv("OAUTH_ISSUER_BASE_URL")
    oauth_audience = os.getenv("OAUTH_AUDIENCE")

    jwks_url = f"{oauth_issuer_base_url}/.well-known/jwks.json"
    jwks = requests.get(jwks_url).json()

    header = jwt.get_unverified_header(token)
    rsa_key = {}
    for key in jwks["keys"]:
        if key["kid"] == header["kid"]:
            rsa_key = {
                "kty": key["kty"],
                "kid": key["kid"],
                "use": key["use"],
                "n": key["n"],
                "e": key["e"],
            }

    if rsa_key:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=ALGORITHMS,
            audience=oauth_audience,
            issuer=oauth_issuer_base_url,
        )

        idp_prefix = os.getenv('IDP_PREFIX')
        get_email = lambda text: text.split(idp_prefix + '_', 1)[1] if idp_prefix and text.startswith(idp_prefix + '_') else text

        user = get_email(payload['username'])

        # grab deafault account from accounts table
        dynamodb = boto3.resource("dynamodb")
        accounts_table_name = os.getenv("ACCOUNTS_DYNAMO_TABLE")
        if not accounts_table_name:
            raise ValueError("ACCOUNTS_DYNAMO_TABLE is not provided.")

        table = dynamodb.Table(accounts_table_name)
        account = None
        try:
            response = table.get_item(Key={"user": user})
            if "Item" not in response:
                raise ValueError(f"No item found for user: {user}")

            accounts = response["Item"].get("accounts", [])
            for acct in accounts:
                if acct["isDefault"]:
                    account = acct["id"]

        except Exception as e:
            print(f"Error retrieving default account: {e}")

        if not account:
            print("setting account to general_account")
            account = "general_account"

        payload["account"] = account
        payload["username"] = user
        # Here we can established the allowed access according to the feature flags in the future
        # For now it is set to full_access, which says they can do the operation upon entry of the validated function
        # current access types include: asssistants, share, dual_embedding, chat, file_upload
        payload["allowed_access"] = ["full_access"]
        return payload
    else:
        print("No RSA Key Found, likely an invalid OAUTH_ISSUER_BASE_URL")

    raise Unauthorized("No Valid Access Token Found")


def parseToken(event):
    token = None
    normalized_headers = {k.lower(): v for k, v in event["headers"].items()}
    authorization_key = "authorization"

    if authorization_key in normalized_headers:
        parts = normalized_headers[authorization_key].split()

        if len(parts) == 2:
            scheme, token = parts
            if scheme.lower() != "bearer":
                token = None

    if not token:
        raise Unauthorized("No Access Token Found")

    return token


def api_claims(event, context, token):
    print("API route was taken")

    # Set up DynamoDB connection
    dynamodb = boto3.resource("dynamodb")
    api_keys_table_name = os.getenv("API_KEYS_DYNAMODB_TABLE")
    if not api_keys_table_name:
        raise ValueError("API_KEYS_DYNAMODB_TABLE is not provided.")

    table = dynamodb.Table(api_keys_table_name)

    try:
        # Retrieve item from DynamoDB
        response = table.query(
            IndexName="ApiKeyIndex",
            KeyConditionExpression="apiKey = :apiKeyVal",
            ExpressionAttributeValues={":apiKeyVal": token},
        )
        items = response["Items"]

        if not items:
            print("API key does not exist.")
            raise LookupError("API key not found.")

        item = items[0]

        # Check if the API key is active
        if not item.get("active", False):
            print("API key is inactive.")
            raise PermissionError("API key is inactive.")

        # Optionally check the expiration date if applicable
        if (
                item.get("expirationDate")
                and datetime.strptime(item["expirationDate"], "%Y-%m-%d") <= datetime.now()
        ):
            print("API key has expired.")
            raise PermissionError("API key has expired.")

        # Check for access rights
        access = item.get("accessTypes", [])
        # if (
        #         "assistants" not in access
        #         and "share" not in access
        #         and "full_access" not in access
        # ):
        #     print("API doesn't have access to assistants")
        #     raise PermissionError(
        #         "API key does not have access to assistants functionality"
        #     )

        # Determine API user
        current_user = determine_api_user(item)

        rate_limit = item["rateLimit"]
        if is_rate_limited(current_user, rate_limit):
            rate = float(rate_limit["rate"])
            period = rate_limit["period"]
            print(f"You have exceeded your rate limit of ${rate:.2f}/{period}")
            raise Unauthorized(
                f"You have exceeded your rate limit of ${rate:.2f}/{period}"
            )

        # Update last accessed
        table.update_item(
            Key={"api_owner_id": item["api_owner_id"]},
            UpdateExpression="SET lastAccessed = :now",
            ExpressionAttributeValues={":now": datetime.now().isoformat()},
        )
        print("Last Access updated")

        # additional validator change from other lambdas
        is_group_sys_user = item.get("purpose", "") == "group"
        ###
        return {
            "username": current_user,
            "account": item["account"]["id"],
            "allowed_access": access,
            "is_group_sys_user": is_group_sys_user,
        }

    except Exception as e:
        print("Error during DynamoDB operation:", str(e))
        raise RuntimeError("Internal server error occurred: ", e)


def determine_api_user(data):
    key_type_pattern = r"/(.*?)Key/"
    match = re.search(key_type_pattern, data["api_owner_id"])
    key_type = match.group(1) if match else None

    if key_type == "owner":
        return data.get("owner")
    elif key_type == "delegate":
        return data.get("delegate")
    elif key_type == "system":
        return data.get("systemId")
    else:
        print("Unknown or missing key type in api_owner_id:", key_type)
        raise Exception("Invalid or unrecognized key type.")


def get_groups(user, token):
    return ["Amplify_Dev_Api"]
    # amplify_groups = get_user_cognito_amplify_groups(token)
    # return amplify_groups


def is_rate_limited(current_user, rate_limit):
    print(rate_limit)
    if rate_limit["period"] == "Unlimited":
        return False
    cost_calc_table = os.getenv("COST_CALCULATIONS_DYNAMO_TABLE")
    if not cost_calc_table:
        raise ValueError(
            "COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables."
        )

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(cost_calc_table)

    try:
        print("Query cost calculation table")
        response = table.query(KeyConditionExpression=Key("id").eq(current_user))
        items = response["Items"]
        if not items:
            print("Table entry does not exist. Cannot verify if rate limited.")
            return False

        rate_data = items[0]

        period = rate_limit["period"]
        col_name = f"{period.lower()}Cost"

        spent = rate_data[col_name]
        if period == "Hourly":
            spent = spent[
                datetime.now().hour
            ]  # Get the current hour as a number from 0 to 23
        print(f"Amount spent {spent}")
        return spent >= rate_limit["rate"]

    except Exception as error:
        print(f"Error during rate limit DynamoDB operation: {error}")
        return False
