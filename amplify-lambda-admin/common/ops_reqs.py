
import os
import requests
import json


def get_all_op(access_token):
    print("Initiate get ops call")

    endpoint = os.environ['API_BASE_URL'] + '/ops/get_all'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    try:
        response = requests.get(
            endpoint,
            headers=headers,
        )
        # print("Response: ", response.content)
        response_content = response.json() # to adhere to object access return response dict

        if response.status_code != 200 or not response_content.get('success', False):
            return {'success': False, 'data': None}
        elif response.status_code == 200 and response_content.get('success', False):
            return response_content

    except Exception as e:
        print(f"Error getting all ops: {e}")
        return {'success': False, 'data': None}
