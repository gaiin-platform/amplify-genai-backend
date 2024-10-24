
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

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
    permissions_endpoint = os.environ['API_BASE_URL'] +  "/utilities/update_object_permissions"
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
        print("Update object access permissions response: ", response_content)

        if response.status_code != 200 or response_content.get('statusCode', None) != 200: 
            return False
        elif response.status_code == 200 and response_content.get('statusCode', None) == 200:
            return True

    except Exception as e:
        print(f"Error updating permissions: {e}")
        return False




def can_access_objects(access_token, data_sources, permission_level="read"):
    print(f"Checking access on data sources: {data_sources}")

    # If there is a protocol on the ID, we need to strip it off
    access_levels = {
        ds['id']: permission_level
        for ds in data_sources
    }

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
    permissions_endpoint = os.environ['API_BASE_URL'] + '/utilities/can_access_objects'
    try:
        response = requests.post(
            permissions_endpoint,
            headers=headers,
            data=json.dumps(request_data)
        )

        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200 or response_content.get('statusCode', None) != 200:
            print(f"User does not have access to data sources: {response.status_code}")
            return False
        elif response.status_code == 200 and response_content.get('statusCode', None) == 200:
            return True

    except Exception as e:
        print(f"Error checking access on data sources: {e}")
        return False

    return False
