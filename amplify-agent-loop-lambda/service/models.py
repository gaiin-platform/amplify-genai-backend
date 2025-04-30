import os
import requests

def get_default_models(access_token):
    api_url = os.environ.get('API_BASE_URL') + '/default_models'
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    
    try:
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()  # Raise an exception for HTTP errors
        
        data = response.json()
        # print(f"Data: {data}")
        
        if not data or not data.get('success') or not data.get('data'):
            print("Missing data in default models response")
            return {}
        
        data = data.get('data')
        cheapest_model_id = data.get('cheapest')
        default_model_id = data.get('user')
        return { 
            "agent_model" : data.get('agent') or cheapest_model_id,
            "advanced_model" : data.get('advanced') or default_model_id,
        }
     
    except requests.exceptions.RequestException as e:
        print(f"Error fetching ops: {str(e)}")
        return {}
