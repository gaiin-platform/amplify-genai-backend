
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from common.validate import validated
from . import assistant_api as assistants
import random
import string
import re


@validated(op="chat") 
def chat_with_code_interpreter(event, context, current_user, name, data):
  access = data['allowed_access']
  if ('assistants' not in access and 'full_access' not in access):
      return {'success': False, 'message': 'API key does not have access to assistant functionality'}
  
  print("Chat_with_code_interpreter validated")
  assistant_id = data['data']['assistantId']
  messages = data['data']['messages']
  
  api_accessed =  data['api_accessed']
  account_id = data['account'] if api_accessed else data['data']['accountId']
  request_id = generate_req_id() if api_accessed else data['data']['requestId']
  thread_id = data['data'].get('threadId', None)

  return assistants.chat_with_code_interpreter(
    current_user,
    assistant_id,
    thread_id,
    messages,
    account_id,
    request_id,
    api_accessed
  )

def generate_req_id():
   return ''.join(random.choices(string.ascii_lowercase + string.digits, k=7)) 

@validated(op="create")
def create_code_interpreter_assistant (event, context, current_user, name, data):
  extracted_data = data['data']
  assistant_name = extracted_data['name']
  description = extracted_data['description']
  tags = extracted_data.get('tags', [])
  instructions = extracted_data['instructions']
  file_keys = extracted_data.get('dataSources', [])

  # Assuming get_openai_client and file_keys_to_file_ids functions are defined elsewhere
  return assistants.create_new_assistant(
    user_id=current_user,
    assistant_name=assistant_name,
    description=description,
    instructions=instructions,
    tags=tags,
    file_keys=file_keys,
  )

@validated(op="delete")
def delete_assistant(event, context, current_user, name, data):
  query_params = event.get('queryStringParameters', {})
  print("Query params: ", query_params)
  assistant_id = query_params.get('assistantId', '')
  if (not assistant_id or not is_valid_query_param_id(assistant_id, current_user, 'ast')):
      return {
              'success': False,
              'message': 'Invalid or missing assistant id parameter'
              }
  print(f"Deleting assistant: {assistant_id}")
  return assistants.delete_assistant_by_id(assistant_id, current_user)

@validated(op="delete")
def delete_assistant_thread(event, context, current_user, name, data):
  query_params = event.get('queryStringParameters', {})
  print("Query params: ", query_params)
  thread_id = query_params.get('threadId', '')
  if (not thread_id or not is_valid_query_param_id(thread_id, current_user, 'thr')):
      return {
              'success': False,
              'message': 'Invalid or missing thread id parameter'
              }
  # Assuming get_openai_client is defined elsewhere and provides an instance of the OpenAI client
  return assistants.delete_thread_by_id(thread_id, current_user)


@validated(op="download")                      
def get_presigned_url_code_interpreter(event, context, current_user, name, data):
  data = data['data']
  key = data['key']
  file_name = data.get('fileName', None)

  return assistants.get_presigned_download_url(key, current_user, file_name)


def is_valid_query_param_id(id, current_user, prefix):
  pattern = f'^{re.escape(current_user)}/{re.escape(prefix)}/[0-9a-fA-F-]{{36}}$'
  if re.match(pattern, id):
      return True
  return False
   