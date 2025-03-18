import json
import os
import re
import boto3
import requests
from integrations.oauth import IntegrationType, provider_case
from common.validate import validated
from integrations.oauth import get_user_credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from io import BytesIO
from google.oauth2.credentials import Credentials


API_URL = os.environ['API_BASE_URL']
# unifies location for functions needed in datasource file manager component. 
@validated("list_files")
def list_integration_files(event, context, current_user, name, data):
   token = data['access_token']
   data = data['data']
   integration = data['integration']
   integration_provider = provider_case(integration)
   folder_id = data.get('folder_id')


   print(f"Listing files for integration: {integration_provider}")
   result = list_files(integration_provider, token, folder_id)
   if result:
      return {"success": True, "data": result}

   return {"success": False, "error": "No integration files found"}


def list_files(integration_provider, token, folder_id = None):
   """
   Creates an OAuth client for either Google or Microsoft integrations.
   Returns a tuple of (client, is_google_flow) where is_google_flow is used to determine
   how to handle the client in other functions.
   """
   match integration_provider:
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
         
   print(f"No result from list_files for integration: {integration_provider}")
   return None



@validated("download_file")
def download_integration_file(event, context, current_user, name, data):

    token = data['access_token']
    data = data['data']
    integration = data['integration']
    integration_provider = provider_case(integration)
    file_id = data.get('file_id')
    direct_download = data.get('direct_download', True)

    print(f"Starting download for integration {integration_provider}, file {file_id}")               
    result = request_download_link(integration_provider, file_id, token)
    print(f"Download link result: {result}")

    if result and "downloadLink" in result:
        try:
            download_url = result['downloadLink']

            download_file_id = result.get('id')
            requires_cleanup = file_id != download_file_id

            # when downloaded directly to the user 
            if direct_download and not requires_cleanup :
               return {"success": True, "data": download_url}
            else:
               file_name = result.get('name', 'downloaded_file')
               file_mime_type = result.get('mimeType', 'application/octet-stream')
               file_extension = MIME_TO_EXT.get(file_mime_type, '')
               print(f"File name: {file_name}, mime type: {file_mime_type}, extension: {file_extension}")
               safe_file_name = re.sub(r'[^a-zA-Z0-9._-]', '', file_name)

               if '.' in safe_file_name:
                  safe_file_name = safe_file_name.rsplit('.', 1)[0]

               safe_file_name += file_extension

               credentials = get_user_credentials(current_user, integration)
               print(f"Downloading file: {file_name}, mime type: {file_mime_type}, safe name: {safe_file_name}")
               file_content = get_file_contents(integration_provider, credentials, download_file_id, download_url)
               if not file_content:
                  return {"success": False, "error": "Failed to get file contents"}

               # Create an S3 key using the safe file name; no double extension.
               key = f"temp_integration_file/{current_user}/{safe_file_name}"

               bucket = os.environ['S3_CONVERSION_OUTPUT_BUCKET_NAME']
               print(f"Saving file to S3 bucket: {bucket}, key: {key}")

               try:
                  s3 = boto3.client('s3')
                  s3.put_object(
                     Bucket=bucket,
                     Key=key,
                     Body=file_content,
                     ContentType=file_mime_type
                  )
                  print("File successfully saved to S3")
               except Exception as s3_error:
                  print(f"S3 upload error details: {s3_error}")
                  raise

               # Set disposition header to use the original filename for download
               response_headers = {
                  'ResponseContentDisposition': f'attachment; filename="{file_name}"',
                  'ResponseContentType': file_mime_type
               }

               presigned_url = s3.generate_presigned_url(
                  'get_object',
                  Params={'Bucket': bucket, 'Key': key, **response_headers},
                  ExpiresIn=3600  # URL will be valid for 1 hour
               )
               if (requires_cleanup): cleanup_after_download_file(integration_provider, download_file_id, token)

               return {"success": True, "data": presigned_url}

        except Exception as e:
            print(f"Error saving file to S3: {e}")
            return {"success": False, "error": f"Error saving file to S3: {str(e)}"}
    else:
        print(f"No download link in result: {result}")
        return {"success": False, "error": "Failed to get download link for file"}
    


def request_download_link(integration_provider, file_id, token):
    """
    Downloads a file from the integration.
    """
    match integration_provider:
        case IntegrationType.GOOGLE:
           return execute_request(token, "/google/integrations/route?op=get_download_link", {'fileId': file_id})
        case IntegrationType.MICROSOFT:
           return execute_request(token, "/microsoft/integrations/route?op=download_file", {'item_id': file_id})
   

def get_file_contents(integration_provider, credentials, file_id, download_url):
   print(f"Getting file contents for integration: {integration_provider}, file_id: {file_id}")
   try:
      match integration_provider:
         case IntegrationType.GOOGLE:
            credentials = Credentials.from_authorized_user_info(credentials)
            service = build('drive', 'v3', credentials=credentials)
            request = service.files().get_media(fileId=file_id)
            file = BytesIO()
            downloader = MediaIoBaseDownload(file, request)
            done = False
            while done is False:
                     status, done = downloader.next_chunk()
            return file.getvalue()
         case IntegrationType.MICROSOFT:
               integration_token = credentials["token"]
               headers = {
                  'Authorization': f'Bearer {integration_token}'
               }
               response = requests.get(download_url, headers=headers, timeout=30)
               if not response.ok:
                  print(f"Error downloading Microsoft file: HTTP {response.status_code} - {response.reason}")
                  return None
               
               # Use BytesIO to accumulate streamed content
               file_content = BytesIO()
               for chunk in response.iter_content(chunk_size=8192):
                  if chunk:
                     file_content.write(chunk)
               
               return file_content.getvalue()
   except Exception as e:
      print(f"Error getting file contents for integration: {integration_provider} - error: {e}")
      return None

      


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
   


def cleanup_after_download_file(integration_provider, download_file_id, token):
   match integration_provider:
      case IntegrationType.GOOGLE:
         # Clean up converted file if it was created during this process
         try:
            print(f"Cleaning up converted file with ID: {download_file_id}")
            cleanup_result = execute_request(token, "/google/integrations/route?op=delete_item_permanently", {'itemId': download_file_id})
            if cleanup_result:
               print(f"Successfully deleted converted file")
            else:
               print(f"Failed to delete converted file")
         except Exception as e:
               print(f"Error deleting converted file: {e}\n continuing...")
               # Continue even if cleanup fails, don't fail the whole operation
      # case IntegrationType.MICROSOFT:
          # no cleanup required

          
MIME_TO_EXT = {
    'application/pdf': '.pdf',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.ms-excel': '.xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.ms-powerpoint': '.ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'text/plain': '.txt',
    'text/html': '.html',
    'text/csv': '.csv',
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif'
}



