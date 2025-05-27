
import os
import requests
import json

def send_email(access_token, email_to, email_subject, email_body):
    print("Initiate email call")

    endpoint = os.environ['API_BASE_URL'] + '/ses/send-email'
 
    request = {
        "data": {'email_to': email_to, 
                 'email_subject': email_subject, 
                 'email_body': email_body}
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
            return False
        elif response.status_code == 200 and response_content.get('success', False):
            return True

    except Exception as e:
        print(f"Error sending email: {e}")
        return False


