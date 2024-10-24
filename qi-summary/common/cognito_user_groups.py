import os
import requests
import json

def get_user_cognito_groups(access_token):
    print("Initiate cognito groups call")

    cognito_group_endpoint = os.environ['API_BASE_URL'] + '/utilities/in_cognito_group'

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    try:
        response = requests.get(
            cognito_group_endpoint,
            headers=headers,
        )
        response_content = response.json() # to adhere to object access return response dict
        print("Response: ", response_content)
        body = json.loads(response_content['body'])
        if response.status_code != 200  or response_content.get('statusCode', None) != 200 or not "cognitoGroups" in body:
            print("Error calling get user cognito groups: ",  response_content['body'].get("error", response.text))
            return []
        elif response.status_code == 200:
            return body["cognitoGroups"]

    except Exception as e:
        print(f"Error getting user cognito groups: {e}")
        return []
