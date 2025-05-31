import os
import requests
import json

def check_embedding_completion(access_token, datasource_ids, request_id=None):
    print("Checking embedding completion for data sources", datasource_ids)
    
    endpoint = os.environ.get('API_BASE_URL', '') + '/embedding/check-completion'
    
    request = {
        "data": {"dataSources": datasource_ids}
    }
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    
    try:
        response = requests.post(
            endpoint,
            headers=headers,
            data=json.dumps(request)
        )
        
        print("Response: ", response.content)
        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get('success', False):
            return False
        elif response.status_code == 200 and response_content.get('success', False):
            return True
            
    except Exception as e:
        print(f"Error checking embedding completion: {e}")
        return False
