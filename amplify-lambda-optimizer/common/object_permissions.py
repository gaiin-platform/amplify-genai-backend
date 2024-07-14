import os
import boto3
import requests
import json

def can_access_objects(current_user, access_token, data_sources, permission_level="read"):
    print(f"Checking access on data sources: {data_sources}")

    # Check if the id of all the data_sources starts with the current_user
    if current_user and all([ds['id'].startswith(current_user+"/") for ds in data_sources]):
        return True

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

        response_content = response.json() # to adhere to object access return response dict

        print(f"Response: {response_content}")

        if response.status_code != 200 or response_content.get('statusCode', None) != 200:
            print(f"User does not have access to data sources: {response.status_code}")
            return False
        elif response.status_code == 200 and response_content.get('statusCode', None) == 200:
            return True

    except Exception as e:
        print(f"Error checking access on data sources: {e}")
        return False

    return False


def simulate_can_access_objects(access_token, object_ids, permission_levels=["read"]):
    print(f"Simulating access on data sources: {object_ids}")

    access_levels = {id: permission_levels for id in object_ids}

    # Set the access levels result for each object to false for every object id and permission level
    all_denied = {id: {pl: False for pl in permission_levels} for id in object_ids}

    print(f"With access levels: {access_levels}")

    request_data = {
        'data': {
            'objects': access_levels
        }
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    # Replace 'permissions_endpoint' with the actual permissions endpoint URL
    permissions_endpoint = os.environ['OBJECT_SIMULATE_ACCESS_API_ENDPOINT']

    try:
        response = requests.post(
            permissions_endpoint,
            headers=headers,
            data=json.dumps(request_data)
        )

        response_content = response.json() # to adhere to object access return response dict
        
        if response.status_code != 200 or response_content.get('statusCode', None) != 200:
            print(f"Error simulating user access")
            return all_denied
        elif response.status_code == 200 and response_content.get('statusCode', None) == 200:
            result = response.json()
            if 'data' in result:
                return result['data']
            else:
                return all_denied

    except Exception as e:
        print(f"Error simulating access on data sources: {e}")
        return all_denied

    return all_denied

