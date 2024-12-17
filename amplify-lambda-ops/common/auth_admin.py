
import os
import requests
import json


def verify_user_as_admin(access_token, purpose):
    print("Initiate authenticate user as admin call")

    print(access_token)

    update_model_endpoint = os.environ['API_BASE_URL'] + '/amplifymin/auth'
 
    request = {
        "data": {'purpose': purpose}
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    try:
        response = requests.post(
            update_model_endpoint,
            headers=headers,
            data=json.dumps(request)
        )
        print("Response: ", response.content)
        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get('success', False):
            return False
        elif response.status_code == 200 and response_content.get('success', False):
            return response_content.get('isAdmin', False)

    except Exception as e:
        print(f"Error authenticating user as admin: {e}")
        return False


