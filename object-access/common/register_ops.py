
import os
import requests
import json


def register_ops(access_token, ops):
    print("Initiate amplify assistants write ops call")

    endpoint = os.environ['API_BASE_URL'] + '/ops/register'
 
    request = {
        "data": {'ops': ops}
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
        print(f"Error amplify assistants writing ops: {e}")
        return False


