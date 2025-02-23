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


# Added a new combined route schema which accepts one of the previous payloads.
route_data_schema = {
    "type": "object",
    "anyOf": [
       # sheets/get-rows (operation: get_rows)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "cellRange": {"type": "string"},
            },
            "required": ["spreadsheetId", "cellRange"],
            "additionalProperties": False
        },
       # sheets/get-info (operation: get_google_sheets_info)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
            },
            "required": ["spreadsheetId"],
            "additionalProperties": False
        },
       # sheets/get-sheet-names (operation: get_sheet_names)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
            },
            "required": ["spreadsheetId"],
            "additionalProperties": False
        },
       # sheets/insert-rows (operation: insert_rows)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "rowsData": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "sheetName": {"type": "string"},
                "insertionPoint": {"type": "integer"},
            },
            "required": ["spreadsheetId", "rowsData"],
            "additionalProperties": False
        },
       # sheets/delete-rows (operation: delete_rows)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "sheetName": {"type": "string"},
                "startRow": {"type": "integer", "minimum": 1},
                "endRow": {"type": "integer", "minimum": 1},
            },
            "required": ["spreadsheetId", "startRow", "endRow"],
            "additionalProperties": False
        },
       # sheets/update-rows (operation: update_rows)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "rowsData": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": ["string", "number"]},
                        "minItems": 2
                    },
                },
                "sheetName": {"type": "string"},
            },
            "required": ["spreadsheetId", "rowsData"],
            "additionalProperties": False
        },
       # sheets/create-spreadsheet (operation: create_spreadsheet)
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
            },
            "required": ["title"],
            "additionalProperties": False
        },
       # sheets/duplicate-sheet (operation: duplicate_sheet)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "sheetId": {"type": "integer"},
                "newSheetName": {"type": "string"},
            },
            "required": ["spreadsheetId", "sheetId", "newSheetName"],
        },
       # sheets/rename-sheet (operation: rename_sheet)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "sheetId": {"type": "integer"},
                "newName": {"type": "string"},
            },
            "required": ["spreadsheetId", "sheetId", "newName"],
            "additionalProperties": False
        },
       # sheets/clear-range (operation: clear_range)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "rangeName": {"type": "string"},
            },
            "required": ["spreadsheetId", "rangeName"],
            "additionalProperties": False
        },
       # sheets/apply-formatting (operation: apply_formatting)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "sheetId": {"type": "integer"},
                "startRow": {"type": "integer", "minimum": 1},
                "endRow": {"type": "integer", "minimum": 1},
                "startCol": {"type": "integer", "minimum": 1},
                "endCol": {"type": "integer", "minimum": 1},
                "formatJson": {"type": "object"},
            },
            "required": ["spreadsheetId", "sheetId", "startRow", "endRow", "startCol", "endCol", "formatJson"],
            "additionalProperties": False
        },
       # sheets/add-chart (operation: add_chart)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "sheetId": {"type": "integer"},
                "chartSpec": {"type": "object"},
            },
            "required": ["spreadsheetId", "sheetId", "chartSpec"],
            "additionalProperties": False
        },
       # sheets/get-cell-formulas (operation: get_cell_formulas)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "rangeName": {"type": "string"},
            },
            "required": ["spreadsheetId", "rangeName"],
            "additionalProperties": False
        },
       # sheets/find-replace (operation: find_replace)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "find": {"type": "string"},
                "replace": {"type": "string"},
                "sheetId": {"type": "integer"},
            },
            "required": ["spreadsheetId", "find", "replace"],
            "additionalProperties": False
        },
       # sheets/sort-range (operation: sort_range)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "sheetId": {"type": "integer"},
                "startRow": {"type": "integer", "minimum": 1},
                "endRow": {"type": "integer", "minimum": 1},
                "startCol": {"type": "integer", "minimum": 1},
                "endCol": {"type": "integer", "minimum": 1},
                "sortOrder": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["spreadsheetId", "sheetId", "startRow", "endRow", "startCol", "endCol", "sortOrder"],
            "additionalProperties": False
        },
       # sheets/apply-conditional-formatting (operation: apply_conditional_formatting)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "sheetId": {"type": "integer"},
                "startRow": {"type": "integer", "minimum": 1},
                "endRow": {"type": "integer", "minimum": 1},
                "startCol": {"type": "integer", "minimum": 1},
                "endCol": {"type": "integer", "minimum": 1},
                "condition": {"type": "object"},
                "format": {"type": "object"},
            },
            "required": ["spreadsheetId", "sheetId", "startRow", "endRow", "startCol", "endCol", "condition", "format"],
            "additionalProperties": False
        },
       # sheets/execute-query (operation: execute_query)
        {
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "sheetName": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["spreadsheetId", "query"],
            "additionalProperties": False
        },
       # docs/create-document (operation: create_new_document)
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
            },
            "required": ["title"],
            "additionalProperties": False
        },
       # docs/get-contents (operation: get_document_contents)
        {
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
            },
            "required": ["documentId"],
            "additionalProperties": False
        },
       # docs/insert-text (operation: insert_text)
        {
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
                "text": {"type": "string"},
                "index": {"type": "integer", "minimum": 1},
            },
            "required": ["documentId", "text", "index"],
            "additionalProperties": False
        },
       # docs/replace-text (operation: replace_text)
        {
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
                "oldText": {"type": "string"},
                "newText": {"type": "string"},
            },
            "required": ["documentId", "oldText", "newText"],
            "additionalProperties": False
        },
       # docs/create-outline (operation: create_document_outline)
        {
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
                "outlineItems": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "integer", "minimum": 1},
                            "end": {"type": "integer", "minimum": 1},
                        },
                        "required": ["start", "end"],
                    },
                },
            },
            "required": ["documentId", "outlineItems"],
            "additionalProperties": False
        },
       # docs/export-document (operation: export_document)
        {
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
                "mimeType": {"type": "string"},
            },
            "required": ["documentId", "mimeType"],
            "additionalProperties": False
        },
       # docs/share-document (operation: share_document)
        {
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
                "email": {"type": "string", "format": "email"},
                "role": {"type": "string", "enum": ["writer", "reader", "commenter"]},
            },
            "required": ["documentId", "email", "role"],
            "additionalProperties": False
        },
       # docs/find-text-indices (operation: find_text_indices)
        {
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
                "searchText": {"type": "string"},
            },
            "required": ["documentId", "searchText"],
            "additionalProperties": False
        },
       # docs/append-text (operation: append_text)
        {
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["documentId", "text"],
            "additionalProperties": False
        },
       # calendar/create-event (operation: create_event)
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "startTime": {"type": "string", "format": "date-time"},
                "endTime": {"type": "string", "format": "date-time"},
                "description": {"type": "string"},
            },
            "required": ["title", "startTime", "endTime", "description"],
            "additionalProperties": False
        },
       # calendar/update-event (operation: update_event)
        {
            "type": "object",
            "properties": {
                "eventId": {"type": "string"},
                "updatedFields": {"type": "object"},
            },
            "required": ["eventId", "updatedFields"],
            "additionalProperties": False
        },
       # calendar/delete-event (operation: delete_event)
        {
            "type": "object",
            "properties": {
                "eventId": {"type": "string"},
            },
            "required": ["eventId"],
            "additionalProperties": False
        },
       # calendar/get-event-details (operation: get_event_details)
        {
            "type": "object",
            "properties": {
                "eventId": {"type": "string"},
            },
            "required": ["eventId"],
            "additionalProperties": False
        },
       # calendar/get-events-between-dates (operation: get_events_between_dates)
        {
            "type": "object",
            "properties": {
                "startDate": {"type": "string", "format": "date-time"},
                "endDate": {"type": "string", "format": "date-time"},
                "includeDescription": {"type": "boolean"},
                "includeAttendees": {"type": "boolean"},
                "includeLocation": {"type": "boolean"},
            },
            "required": ["startDate", "endDate"],
            "additionalProperties": False
        },
       # calendar/get-events-for-date (operation: get_events_for_date)
        {
            "type": "object",
            "properties": {
                "date": {"type": "string", "format": "date"},
                "includeDescription": {"type": "boolean"},
                "includeAttendees": {"type": "boolean"},
                "includeLocation": {"type": "boolean"},
            },
            "required": ["date"],
            "additionalProperties": False
        },
       # calendar/get-upcoming-events (operation: get_upcoming_events)
        {
            "type": "object",
            "properties": {
                "endDate": {"type": "string", "format": "date-time"},
                "includeDescription": {"type": "boolean"},
                "includeAttendees": {"type": "boolean"},
                "includeLocation": {"type": "boolean"},
            },
            "required": ["endDate"],
            "additionalProperties": False
        },
       # calendar/get-free-time-slots (operation: get_free_time_slots)
        {
            "type": "object",
            "properties": {
                "startDate": {"type": "string", "format": "date-time"},
                "endDate": {"type": "string", "format": "date-time"},
                "duration": {"type": "integer", "minimum": 1},
                "userTimeZone": {"type": "string"},
            },
            "required": ["startDate", "endDate", "duration"],
            "additionalProperties": False
        },
       # calendar/check-event-conflicts (operation: check_event_conflicts)
        {
            "type": "object",
            "properties": {
                "proposedStartTime": {"type": "string", "format": "date-time"},
                "proposedEndTime": {"type": "string", "format": "date-time"},
                "returnConflictingEvents": {"type": "boolean"},
            },
            "required": ["proposedStartTime", "proposedEndTime"],
            "additionalProperties": False
        },
       # drive/list-files (operation: list_files)
        {
            "type": "object",
            "properties": {
                "folderId": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False
        },
       # drive/search-files (operation: search_files)
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False
        },
       # drive/get-file-metadata (operation: get_file_metadata)
        {
            "type": "object",
            "properties": {
                "fileId": {"type": "string"},
            },
            "required": ["fileId"],
            "additionalProperties": False
        },
       # drive/get-file-content (operation: get_file_content)
        {
            "type": "object",
            "properties": {
                "fileId": {"type": "string"},
            },
            "required": ["fileId"],
            "additionalProperties": False
        },
       # drive/create-file (operation: create_file)
        {
            "type": "object",
            "properties": {
                "fileName": {"type": "string"},
                "content": {"type": "string"},
                "mimeType": {"type": "string"},
            },
            "required": ["fileName", "content"],
            "additionalProperties": False
        },
       # drive/get-download-link (operation: get_download_link)
        {
            "type": "object",
            "properties": {
                "fileId": {"type": "string"},
            },
            "required": ["fileId"],
            "additionalProperties": False
        },
       # drive/create-shared-link (operation: create_shared_link)
        {
            "type": "object",
            "properties": {
                "fileId": {"type": "string"},
                "permission": {"type": "string", "enum": ["view", "edit"]},
            },
            "required": ["fileId", "permission"],
            "additionalProperties": False
        },
       # drive/share-file (operation: share_file)
        {
            "type": "object",
            "properties": {
                "fileId": {"type": "string"},
                "emails": {"type": "array", "items": {"type": "string"}},
                "role": {"type": "string", "enum": ["reader", "commenter", "writer"]},
            },
            "required": ["fileId", "emails", "role"],
            "additionalProperties": False
        },
       # drive/convert-file (operation: convert_file)
        {
            "type": "object",
            "properties": {
                "fileId": {"type": "string"},
                "targetMimeType": {"type": "string"},
            },
            "required": ["fileId", "targetMimeType"],
            "additionalProperties": False
        },
       # drive/list-folders (operation: list_folders)
        {
            "type": "object",
            "properties": {
                "parentFolderId": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False
        },
       # drive/move-item (operation: move_item)
        {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "destinationFolderId": {"type": "string"},
            },
            "required": ["itemId", "destinationFolderId"],
            "additionalProperties": False
        },
       # drive/copy-item (operation: copy_item)
        {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "newName": {"type": "string"},
            },
            "required": ["itemId"],
            "additionalProperties": False
        },
       # drive/rename-item (operation: rename_item)
        {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "newName": {"type": "string"},
            },
            "required": ["itemId", "newName"],
            "additionalProperties": False
        },
       # drive/get-file-revisions (operation: get_file_revisions)
        {
            "type": "object",
            "properties": {
                "fileId": {"type": "string"},
            },
            "required": ["fileId"],
            "additionalProperties": False
        },
       # drive/create-folder (operation: create_folder)
        {
            "type": "object",
            "properties": {
                "folderName": {"type": "string"},
                "parentId": {"type": "string"},
            },
            "required": ["folderName"],
            "additionalProperties": False
        },
       # drive/delete-item-permanently (operation: delete_item_permanently)
        {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
            },
            "required": ["itemId"],
            "additionalProperties": False
        },
       # drive/get-root-folder-ids (operation: get_root_folder_ids)
        {
            "type": "object",
            "properties": {},
            "additionalProperties": False
        },
       # forms/create-form (operation: create_form)
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title"],
            "additionalProperties": False
        },
       # forms/get-form-details (operation: get_form_details)
        {
            "type": "object",
            "properties": {
                "formId": {"type": "string"},
            },
            "required": ["formId"],
            "additionalProperties": False
        },
       # forms/add-question (operation: add_question)
        {
            "type": "object",
            "properties": {
                "formId": {"type": "string"},
                "questionType": {"type": "string"},
                "title": {"type": "string"},
                "required": {"type": "boolean"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["formId", "questionType", "title"],
            "additionalProperties": False
        },
       # forms/update-question (operation: update_question)
        {
            "type": "object",
            "properties": {
                "formId": {"type": "string"},
                "questionId": {"type": "string"},
                "title": {"type": "string"},
                "required": {"type": "boolean"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["formId", "questionId"],
            "additionalProperties": False
        },
       # forms/delete-question (operation: delete_question)
        {
            "type": "object",
            "properties": {
                "formId": {"type": "string"},
                "questionId": {"type": "string"},
            },
            "required": ["formId", "questionId"],
            "additionalProperties": False
        },
       # forms/get-responses (operation: get_responses)
        {
            "type": "object",
            "properties": {
                "formId": {"type": "string"},
            },
            "required": ["formId"],
            "additionalProperties": False
        },
       # forms/get-response (operation: get_response)
        {
            "type": "object",
            "properties": {
                "formId": {"type": "string"},
                "responseId": {"type": "string"},
            },
            "required": ["formId", "responseId"],
            "additionalProperties": False
        },
       # forms/set-form-settings (operation: set_form_settings)
        {
            "type": "object",
            "properties": {
                "formId": {"type": "string"},
                "settings": {"type": "object"},
            },
            "required": ["formId", "settings"],
            "additionalProperties": False
        },
       # forms/get-form-link (operation: get_form_link)
        {
            "type": "object",
            "properties": {
                "formId": {"type": "string"},
            },
            "required": ["formId"],
            "additionalProperties": False
        },
       # forms/update-form-info (operation: update_form_info)
        {
            "type": "object",
            "properties": {
                "formId": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["formId"],
            "additionalProperties": False
        },
       # forms/list-user-forms (operation: list_user_forms)
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False
        },
       # gmail/compose-and-send (operation: compose_and_send_email)
        {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cc": {"type": "string"},
                "bcc": {"type": "string"},
                "scheduleTime": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False
        },
       # gmail/compose-draft (operation: compose_email_draft)
        {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cc": {"type": "string"},
                "bcc": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False
        },
       # gmail/get-recent-messages (operation: get_recent_messages)
        {
            "type": "object",
            "properties": {
                "n": {"type": "integer"},
                "label": {"type": "string"},
                "days": {"type": "integer"},
            },
            "required": ["n"],
            "additionalProperties": False
        },
       # gmail/search-messages (operation: search_messages)
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False
        },
       # gmail/get-attachment-links (operation: get_attachment_links)
        {
            "type": "object",
            "properties": {
                "messageId": {"type": "string"},
            },
            "required": ["messageId"],
            "additionalProperties": False
        },
       # gmail/get-attachment-content (operation: get_attachment_content)
        {
            "type": "object",
            "properties": {
                "messageId": {"type": "string"},
                "attachmentId": {"type": "string"},
            },
            "required": ["messageId", "attachmentId"],
            "additionalProperties": False
        },
       # gmail/create-filter (operation: create_filter)
        {
            "type": "object",
            "properties": {
                "criteria": {"type": "object"},
                "action": {"type": "object"},
            },
            "required": ["criteria", "action"],
            "additionalProperties": False
        },
       # gmail/create-label (operation: create_label)
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False
        },
       # gmail/create-auto-filter-label-rule (operation: create_auto_filter_label_rule)
        {
            "type": "object",
            "properties": {
                "criteria": {"type": "object"},
                "labelName": {"type": "string"},
            },
            "required": ["criteria", "labelName"],
            "additionalProperties": False
        },
       # gmail/get-message-details (operation: get_message_details)
        {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "ID of the message to retrieve details for"
                },
                "fields": {
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [
                                    "id", "threadId", "historyId", "sizeEstimate", "raw", "payload",
                                    "mimeType", "attachments", "sender", "subject", "labels", "date",
                                    "snippet", "body", "cc", "bcc", "deliveredTo", "receivedTime", "sentTime"
                                ]
                            },
                            "description": "List of fields to include in the response"
                        }
                    ]
                }
            },
            "required": ["message_id"],
            "additionalProperties": False
        },
       # people/search-contacts (operation: search_contacts)
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "page_size": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False
        },
       # people/get-contact-details (operation: get_contact_details)
        {
            "type": "object",
            "properties": {
                "resource_name": {"type": "string"},
            },
            "required": ["resource_name"],
            "additionalProperties": False
        },
       # people/create-contact (operation: create_contact)
        {
            "type": "object",
            "properties": {
                "contact_info": {"type": "object"},
            },
            "required": ["contact_info"],
            "additionalProperties": False
        },
       # people/update-contact (operation: update_contact)
        {
            "type": "object",
            "properties": {
                "resource_name": {"type": "string"},
                "contact_info": {"type": "object"},
            },
            "required": ["resource_name", "contact_info"],
            "additionalProperties": False
        },
       # people/delete-contact (operation: delete_contact)
        {
            "type": "object",
            "properties": {
                "resource_name": {"type": "string"},
            },
            "required": ["resource_name"],
            "additionalProperties": False
        },
       # people/list-contact-groups (operation: list_contact_groups)
        {
            "type": "object",
            "properties": {},
            "additionalProperties": False
        },
       # people/create-contact-group (operation: create_contact_group)
        {
            "type": "object",
            "properties": {
                "group_name": {"type": "string"},
            },
            "required": ["group_name"],
            "additionalProperties": False
        },
       # people/update-contact-group (operation: update_contact_group)
        {
            "type": "object",
            "properties": {
                "resource_name": {"type": "string"},
                "new_name": {"type": "string"},
            },
            "required": ["resource_name", "new_name"],
            "additionalProperties": False
        },
       # people/delete-contact-group (operation: delete_contact_group)
        {
            "type": "object",
            "properties": {
                "resource_name": {"type": "string"},
            },
            "required": ["resource_name"],
            "additionalProperties": False
        },
       # people/add-contacts-to-group (operation: add_contacts_to_group)
        {
            "type": "object",
            "properties": {
                "group_resource_name": {"type": "string"},
                "contact_resource_names": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["group_resource_name", "contact_resource_names"],
            "additionalProperties": False
        },
       # people/remove-contacts-from-group (operation: remove_contacts_from_group)
        {
            "type": "object",
            "properties": {
                "group_resource_name": {"type": "string"},
                "contact_resource_names": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["group_resource_name", "contact_resource_names"],
            "additionalProperties": False
        },
    ]
}


validators = {
    "/google/integrations" : {
        "get" : {}
    },
    "/google/integrations/route" : {
        "route" : route_data_schema
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
