import os

import requests
import json


def can_read_data_sources(access_token, data_sources):

    print(f"Checking access on data sources: {data_sources}")

    access_levels = {ds['id']: 'read' for ds in data_sources}

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
    permissions_endpoint = os.environ['OBJECT_ACCESS_PERMISSIONS_ENDPOINT']

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
