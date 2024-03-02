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

        if response.status_code != 200:
            return False
        elif response.status_code == 200:
            return True

    except Exception as e:
        print(f"Error updating permissions: {e}")
        return False


def can_access_objects(access_token, data_sources, permission_level="read"):
    print(f"Checking access on data sources: {data_sources}")

    access_levels = {ds['id']: permission_level for ds in data_sources}

    print(f"With access levels: {access_levels}")

    request_data = {
        'data': {
            'dataSources': access_levels
        }
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    # Replace 'permissions_endpoint' with the actual permissions endpoint URL
    permissions_endpoint = os.environ['OBJECT_ACCESS_API_ENDPOINT']

    try:
        response = requests.post(
            permissions_endpoint,
            headers=headers,
            data=json.dumps(request_data)
        )

        if response.status_code != 200:
            print(f"User does not have access to data sources: {response.status_code}")
            return False
        elif response.status_code == 200:
            return True

    except Exception as e:
        print(f"Error checking access on data sources: {e}")
        return False

    return False
