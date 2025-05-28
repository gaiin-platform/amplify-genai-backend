
import os
import requests
import json


def get_all_ast_admin_groups(access_token):
    print("Initiate get ast admin call")

    endpoint =  os.environ['API_BASE_URL'] + '/groups/list_all'

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    try:
        response = requests.get(
            endpoint,
            headers=headers,
        )
        print("Response: ", response.content)
        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get('success', False):
            return {'success': False, 'data': None}
        elif response.status_code == 200 and response_content.get('success', False):
            return response_content

    except Exception as e:
        print(f"Error getting ast admin groups: {e}")
        return {'success': False, 'data': None}




def update_ast_admin_groups(access_token, data):
    print("Initiate update ast admin groups call")

    endpoint = os.environ['API_BASE_URL'] + '/groups/update'
    
    request = {
        "data": data
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
            return {'success': False, "message": response_content.get('message', 'Failed to update supported models')}
        elif response.status_code == 200 and response_content.get('success', False):
            return response_content

    except Exception as e:
        print(f"Error updating supported Models: {e}")
        return {'success': False, "message": "Failed to make request"}


