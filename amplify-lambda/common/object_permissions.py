import os
import requests
import json


def update_object_permissions(access_token,
                              shared_with_users,
                              keys,
                              object_type,
                              principal_type="user",
                              permission_level="read",
                              policy=""):
    permissions_endpoint = os.environ['OBJECT_ACCESS_SET_PERMISSIONS_ENDPOINT']
    request = {
        "data": {
            "emailList": shared_with_users,
            "dataSources": keys,
            "objectType": object_type,
            "principalType": principal_type,
            "permissionLevel": permission_level,
            "policy": policy
        }
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    try:
        response = requests.post(
            permissions_endpoint,
            headers=headers,
            data=json.dumps(request)
        )

        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200 or response_content.get('statusCode', None) != 200: 
            return False
        elif response.status_code == 200 and response_content.get('statusCode', None) == 200:
            return True

    except Exception as e:
        print(f"Error updating permissions: {e}")
        return False
