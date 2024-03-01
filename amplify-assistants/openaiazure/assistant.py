from common.validate import validated
from . import assistant_api as assistants

@validated(op="get_messages")
def get_messages_assistant_thread(event, context, current_user, name, data):
  thread_key = data['data']['id']
  # Assuming get_openai_client function is defined elsewhere
  return assistants.fetch_messages_for_thread(thread_key, current_user)

@validated(op="run_status")
def get_run_status_assistant_thread(event, context, current_user, name, data):
  run_key = data['data']['id']
  # Assuming get_openai_client function is defined elsewhere
  return assistants.fetch_run_status(run_key, current_user)


@validated(op="run")
def run_assistant_thread(event, context, current_user, name, data):
  thread_id = data['data']['id']
  assistant_id = data['data']['assistantId']

  # Assuming get_openai_client is defined elsewhere and provides a client instance
  return assistants.run_thread(thread_id, current_user, assistant_id)


@validated(op="chat")
def chat_with_assistant(event, context, current_user, name, data):
  assistant_id = data['data'].get('id')
  messages = data['data'].get('messages')
  file_keys = data['data'].get('fileKeys')

  return assistants.chat_with_assistant(
    current_user,
    assistant_id,
    messages,
    file_keys
  )

@validated(op="add_message")
def add_message_assistant_thread(event, context, current_user, name, data):
  thread_id = data['data'].get('id')
  content = data['data'].get('content')
  message_id = data['data'].get('messageId')
  role = data['data'].get('role')
  file_keys = data['data'].get('fileKeys', [])
  metadata = data['data'].get('data',{})

  # Assuming get_openai_client and file_keys_to_file_ids are defined elsewhere
  # and both provide their respective functionality
  return assistants.add_message_to_thread(
    current_user,
    thread_id,
    message_id,
    content,
    role,
    file_keys,
    metadata
  )

@validated(op="delete")
def delete_assistant_thread(event, context, current_user, name, data):
  thread_id = data['data'].get('id')

  # Assuming get_openai_client is defined elsewhere and provides an instance of the OpenAI client
  return assistants.delete_thread_by_id(thread_id, current_user)


@validated(op="create")
def create_assistant_thread(event, context, current_user, name, data):
  # Assuming get_openai_client function is defined elsewhere
  return assistants.create_new_thread(current_user)

@validated(op="create")
def create_assistant(event, context, current_user, name, data):
  extracted_data = data['data']
  assistant_name = extracted_data['name']
  description = extracted_data['description']
  tags = extracted_data.get('tags', [])
  instructions = extracted_data['instructions']
  file_keys = extracted_data.get('fileKeys', [])
  tools = extracted_data.get('tools', [])

  # Assuming get_openai_client and file_keys_to_file_ids functions are defined elsewhere
  return assistants.create_new_assistant(
    user_id=current_user,
    assistant_name=assistant_name,
    description=description,
    instructions=instructions,
    tags=tags,
    file_keys=file_keys,
    tools=tools
  )


@validated(op="delete")
def delete_assistant(event, context, current_user, name, data):
  assistant_id = data['data']['id']
  print(f"Deleting assistant: {assistant_id}")

  # Assuming get_openai_client function is defined elsewhere
  return assistants.delete_assistant_by_id(assistant_id, current_user)
