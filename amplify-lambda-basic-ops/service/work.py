import re
import json
from datetime import datetime

from common.ops import vop
from common.validate import validated
from work.session import create_session, delete_record, list_records, add_record


@vop(
    path="/work/session/create",
    tags=["work", "default"],
    name="createWorkProductSession",
    description="Create a new session to store intermediate work products as they are created.",
    params={
        "conversation_id": "Optional ID of the conversation this work session belongs to.",
        "tags": "Optional list of tags for the session.",
        "metadata": "Optional dictionary of metadata for the session."
    }
)
@validated(op="create")
def create_user_session(event, context, current_user, name, data):
    try:
        # Extract data from the request
        conversation_id = data['data'].get('conversation_id', None)
        tags = data['data'].get('tags', [])
        metadata = data['data'].get('metadata', {})

        # Create the session using the function from work.session
        new_session = create_session(
            username=current_user,
            conversation_id=conversation_id,
            tags=tags,
            metadata=metadata
        )

        return {
            'success': True,
            'data': {
                'session_id': new_session['session_id'],
                'created_at': new_session['created_at'],
                'conversation_id': new_session['conversation_id'],
                'tags': new_session['tags'],
                'metadata': new_session['metadata']
            }
        }

    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': f"Failed to create the session: {str(e)}"
        }


@vop(
    path="/work/session/add_record",
    tags=["work", "default"],
    name="addWorkProductRecord",
    description="Add a new record to an existing work product session.",
    params={
        "session_id": "ID of the session to add the record to.",
        "record_data": "The JSON data to be stored in the record on a single line with no line breaks."
    }
)
@validated(op="add_record")
def add_user_record(event, context, current_user, name, data):
    try:
        # Extract data from the request
        session_id = int(data['data'].get('session_id'))
        record_data = data['data'].get('record_data', {})
        attachments = data['data'].get('attachments', {})

        if not session_id:
            return {
                'success': False,
                'message': "session_id is required"
            }

        # Add the record using the function from work.session
        new_record = add_record(
            username=current_user,
            session_id=session_id,
            record_data=record_data,
            attachments=attachments
        )

        return {
            'success': True,
            'data': {
                'record_id': new_record['record_id'],
                'created_at': new_record['created_at'],
                'data': new_record['data'],
                'attachment_pointers': new_record['attachment_pointers']
            }
        }

    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': f"Failed to add the record: {str(e)}"
        }


@vop(
    path="/work/session/list_records",
    tags=["work", "default"],
    name="listWorkProductRecords",
    description="List all records in a work product session.",
    params={
        "session_id": "ID of the session to list records from."
    }
)
@validated(op="list_records")
def list_user_records(event, context, current_user, name, data):
    try:
        # Extract data from the request
        session_id = int(data['data'].get('session_id'))

        if not session_id:
            return {
                'success': False,
                'message': "session_id is required"
            }

        # List the records using the function from work.session
        records = list_records(
            username=current_user,
            session_id=session_id
        )

        sorted_records = sorted(records, key=lambda x: datetime.strptime(x['created_at'], '%Y-%m-%dT%H:%M:%S'))

        return {
            'success': True,
            'data': {
                'records': [
                    {
                        'record_id': record['record_id'],
                        'created_at': record['created_at'],
                        'data': record['data'],
                        'attachment_pointers': record.get('attachment_pointers', {})
                    } for record in sorted_records
                ]
            }
        }

    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': f"Failed to list the records: {str(e)}"
        }


@vop(
    path="/work/session/delete_record",
    tags=["work", "default"],
    name="deleteWorkProductRecord",
    description="Delete a specific record from a work product session.",
    params={
        "session_id": "ID of the session containing the record.",
        "record_id": "ID of the record to be deleted."
    }
)
@validated(op="delete_record")
def delete_user_record(event, context, current_user, name, data):
    try:
        # Extract data from the request
        session_id = int(data['data'].get('session_id'))
        record_id = int(data['data'].get('record_id'))

        if not session_id or not record_id:
            return {
                'success': False,
                'message': "Both session_id and record_id are required"
            }

        # Delete the record using the function from work.session
        delete_record(
            username=current_user,
            session_id=session_id,
            record_id=record_id
        )

        return {
            'success': True,
            'data': {
                'message': f"Record {record_id} has been successfully deleted from session {session_id}"
            }
        }

    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': f"Failed to delete the record: {str(e)}"
        }


@vop(
    path="/work/echo",
    tags=["work", "default", "simple"],
    name="echoMessage",
    description="Echo a message back to the user as a pause.",
    params={
        "message": "The message to be echoed back."
    }
)
@validated(op="echo")
def echo(event, context, current_user, name, data):
    try:
        # Extract the message from the request data
        message = data['data'].get('message', '')

        return {
            'success': True,
            'data': {
                'pause': {
                    'message': message
                }
            }
        }

    except Exception as e:
        print(f"Error in echo function: {str(e)}")
        return {
            'success': False,
            'message': f"Failed to echo message: {str(e)}"
        }


@vop(
    path="/work/session/stitch_records",
    tags=["work", "default"],
    name="stitchWorkProductRecords",
    description="Stitch records from a session into a template, or concatenate all records with an optional separator if no template is provided.",
    params={
        "template": "Optional string template with placeholders for records as ?>someRecordId. If not provided, all records will be concatenated. The placeholder to insert a record is this:\n?><Insert Record ID>\n\nExamples:\n?>234234\n?>2asw4r2\n\nI will pull in all the records and put them in the content of the record where you put these placeholders. If you also output the content of the record yourself, it will get duplicated.\n\nHere are some examples of valid templates:\n=========\nExample 1:\n## Report on Vanderbilt\n?>a23r23a\n### Vanderbilt Endowment\n?>24rsae\n\nExample 2:\n## Authors and Prompts\n| Author | Prompt |\n-------------------\n| ?>awe22 | ?>a22ff |\n--------------------\n| ?>asf3e | ?>wef4 |\n=========\n\nYou can mix in any markdown and explanation you want, but don't repeat the content. Instead, use placeholders to have the content inserted.",
        "session_id": "ID of the session containing the records.",
        "separator": "Optional separator to use when concatenating records if no template is provided. Default is an empty string."
    }
)
@validated(op="stitch_records")
def stitch_records(event, context, current_user, name, data):
    try:
        # Extract data from the request
        template = data['data'].get('template')
        session_id = int(data['data'].get('session_id'))
        separator = data['data'].get('separator', '')

        if not session_id:
            return {
                'success': False,
                'message': "session_id is required"
            }

        # Get all records for the session
        records = list_records(current_user, session_id)

        def get_record_content(record):
            if 'text' in record.get('data', {}):
                return record['data']['text']
            else:
                return json.dumps(record, indent=2)

        if not template:
            # If no template is provided, concatenate all records with the specified separator
            concatenated_text = separator.join(
                get_record_content(record) for record in records
            )
            rendered_template = concatenated_text
        else:
            # If a template is provided, use it to stitch records
            record_dict = {record['record_id']: record for record in records}

            def replace_record(match):
                record_id = match.group(1)
                record = record_dict.get(record_id)
                if record:
                    return get_record_content(record)
                return f"[Record {record_id} not found]"

            # Replace placeholders with record content
            rendered_template = re.sub(r'\?>(\w+)', replace_record, template)

        return {
            'success': True,
            'data': {
                'pause': {
                    'message': rendered_template
                }
            }
        }

    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': f"Failed to stitch records: {str(e)}"
        }

