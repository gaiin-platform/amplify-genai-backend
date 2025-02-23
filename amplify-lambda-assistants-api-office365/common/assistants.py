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
        # print("Response: ", response.content)
        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get('success', False):
            return False
        elif response.status_code == 200 and response_content.get('success', False):
            return True

    except Exception as e:
        print(f"Error updating permissions: {e}")
        return False



def list_assistants(access_token):
    print("Initiate list assistant call")

    assistant_endpoint = os.environ['API_BASE_URL'] + '/assistant/list'

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    try:
        response = requests.get(
            assistant_endpoint,
            headers=headers,
        )
        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200 or not 'success' in response_content:
            print("Response: ", response.content)
            return {"success": False}
        else:
            return response_content
        
    except Exception as e:
        print(f"Error listing asts: {e}")
        return {"success": False}
    


def remove_astp_perms(access_token, data):
    print("Initiate remove astp perms assistant call")

    assistant_endpoint = os.environ['API_BASE_URL'] + '/assistant/remove_astp_permissions'

    request = {
        "data": data
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    try:
        response = requests.post(
            assistant_endpoint,
            headers=headers,
            data=json.dumps(request)
        )
        # print("Response: ", response.content)
        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200 or not 'success' in response_content:
            return {"success": False}
        else:
            return response_content

    except Exception as e:
        print(f"Error updating permissions: {e}")
        return {"success": False}
    


def delete_assistant(access_token, data):
    print("Initiate delete assistant call")

    assistant_endpoint = os.environ['API_BASE_URL'] + '/assistant/delete'

    request = {
        "data": data
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    try:
        response = requests.post(
            assistant_endpoint,
            headers=headers,
            data=json.dumps(request)
        )
        # print("Response: ", response.content)
        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200 or not 'success' in response_content:
            return {"success": False}
        else:
            return response_content

    except Exception as e:
        print(f"Error deleting ast: {e}")
        return {"success": False}
    



def create_assistant(access_token, data):
    print("Initiate create assistant call")

    assistant_endpoint = os.environ['API_BASE_URL'] + '/assistant/create'

    request = {
        "data": data
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    try:
        response = requests.post(
            assistant_endpoint,
            headers=headers,
            data=json.dumps(request)
        )
        # print("Response: ", response.content)
        response_content = response.json() # to adhere to object access return response dict
        # print(response_content)

        if response.status_code != 200 or not 'success' in response_content:
            return {"success": False}
        else:
            return response_content

    except Exception as e:
        print(f"Error creating ast: {e}")
        return {"success": False}
    

