
import os
import requests
import json

APP_ID = 'amplify-action-sets'
ENTITY_TYPE = 'action-sets'

def load_action_set(access_token, action_set_id):
    print("Initiate get action set call")

    endpoint = os.environ['API_BASE_URL'] + '/user-data/get'
 
    request = {
        "data": {"appId": APP_ID, 
        "entityType" : ENTITY_TYPE,
        "itemId" : action_set_id
        }
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
            return None
        elif response.status_code == 200 and response_content.get('success', False):
            return response_content.get('data', None)

    except Exception as e:
        print(f"Error getting action set: {e}")
        return None


