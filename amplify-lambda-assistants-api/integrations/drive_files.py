
import json
import os

import requests
from integrations.oauth import IntegrationType, provider_case
from common.validate import validated
API_URL = os.environ['API_BASE_URL']

@validated("list_files")
def list_integration_files(event, context, current_user, name, data):
   token = data['access_token']
   data = data['data']
   integration = data['integration']
   folder_id = data.get('folder_id')

   print(f"Listing files for integration: {integration}")
   result = list_files(integration, token, folder_id)
   if result:
      return {"success": True, "data": result}

   return {"success": False, "error": "No integration files found"}


def list_files(integration, token, folder_id = None):
   """
   Creates an OAuth client for either Google or Microsoft integrations.
   Returns a tuple of (client, is_google_flow) where is_google_flow is used to determine
   how to handle the client in other functions.
   """
   match provider_case(integration):
      case IntegrationType.GOOGLE:
         result = execute_request(token, "/google/integrations/route?op=list_files", {'folderId': folder_id if folder_id else ''})
         if result:
            files = []
            for file_list in result:
               files.append({
                  "id": file_list[0],
                  "name": file_list[1],
                  "mimeType": file_list[2],
                  "size": file_list[3] if len(file_list) > 3 else "N/A",
                  "downloadLink": file_list[4] if len(file_list) > 4 else None
               })
            return files
         
      case IntegrationType.MICROSOFT:
         return execute_request(token, "/microsoft/integrations/route?op=list_drive_items", {'folder_id': folder_id if folder_id else 'root', 'page_size': 100})
         
   print(f"No result from list_files for integration: {integration}")
   return None



@validated("download_file")
def download_integration_file(event, context, current_user, name, data):
   token = data['access_token']
   data = data['data']
   integration = data['integration']
   file_id = data.get('file_id')

   result = download_file(integration, file_id, token)
   if result:
      return {"success": True, "data": result}

   return {"success": False, "error": "No integration files found"}


def download_file(integration, file_id, token):
    """
    Downloads a file from the integration.
    """
    match provider_case(integration):
        case IntegrationType.GOOGLE:
           return execute_request(token, "/google/integrations/route?op=get_download_link", {'fileId': file_id})
        case IntegrationType.MICROSOFT:
           return execute_request(token, "/microsoft/integrations/route?op=download_file", {'item_id': file_id})
   


def execute_request(access_token, url_path, data):
   print(f"Executing request to {url_path}")
   request = {
        "data": data
    }

   headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
   }

   try:
      response = requests.post(
         f"{API_URL}{url_path}",
         headers=headers,
         data=json.dumps(request)
      )

      response_content = response.json() # to adhere to object access return response dict

      if response.status_code != 200 or not response_content.get('success'):
         return None
      elif response.status_code == 200 and response_content.get('success', False):
         return response_content.get('data', None)

   except Exception as e:
      print(f"Error updating permissions: {e}")
      return None
   


