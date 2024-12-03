import os
import requests
import json

def deactivate_key(access_token, api_owner_id):
    print("Initiate deactivate key call")

    amplify_group_endpoint = os.environ['API_BASE_URL'] + '/apiKeys/deactivate_key'

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    data = {
        "data": {'apiKeyId': api_owner_id}
    }

    try:
        response = requests.post(
            amplify_group_endpoint,
            headers=headers,
            data=json.dumps(data)
        )
        response_content = response.json() # to adhere to object access return response dict
        print("Response: ", response_content)
        
        
        if response.status_code != 200  or not "success" in response_content:
            print("Error calling deactivate api key: ", response_content)
            return False
        elif response.status_code == 200:
            return True

    except Exception as e:
        print(f"Error getting user amplify groups: {e}")
        return False







