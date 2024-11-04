import os
import requests
import json

def share_assistant(access_token, data):
    print("Initiate share assistant call")

    share_assistant_endpoint = os.environ['API_BASE_URL'] + '/assistant/share'
 
    request = {
        "data": data
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    try:
        response = requests.post(
            share_assistant_endpoint,
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
        print(f"Error updating permissions: {e}")
        return False
