import os
import requests
import json

def delete_embeddings(access_token, data_sources):
    delete_embeddings_endpoint = os.environ['API_BASE_URL'] + '/embedding-delete'

    # If data_sources is a single string, convert it to a list
    if isinstance(data_sources, str):
        data_sources = [data_sources]
        
    request = {
        "data": {
            "dataSources": data_sources
        }
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    try:
        response = requests.post(
            delete_embeddings_endpoint,
            headers=headers,
            data=json.dumps(request)
        )

        response_content = response.json()
        print("Delete embeddings response: ", response_content)

        if response.status_code != 200:
            print(f"Error deleting embeddings: {response.status_code}")
            return False, response_content
        else:
            return True, response_content['result']

    except Exception as e:
        print(f"Error deleting embeddings: {e}")
        return False, str(e)