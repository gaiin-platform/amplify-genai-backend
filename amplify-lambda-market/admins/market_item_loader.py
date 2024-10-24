
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import yaml
import uuid
import requests
import json
import argparse

# Define API root here
API_ROOT = 'https://9o28pcdzkd.execute-api.us-east-1.amazonaws.com/dev'  # Replace with the actual API root URL


def post_publish_data(data, auth_token):
    url = f"{API_ROOT}/market/item/publish"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f"Bearer {auth_token}"  # Include the Authorization header
    }
    response = requests.post(url, json={'data': data}, headers=headers)
    return response


# Loop through each item in all_publish_data and post it to the API
def publish_all_items(all_publish_data, auth_token):
    for publish_data in all_publish_data:
        print(f"Publishing {publish_data['name']}...")
        response = post_publish_data(publish_data, auth_token)
        if response.status_code == 200:
            print(f"Success: {response.json()}")  # Or any other way you want to handle the successful response
        else:
            print(f"Error: {response.status_code}, Details: {response.text}")
            raise Exception(f"Error: {response.status_code}, Details: {response.text}")



def create_prompt(item):
    prompt_name = item['name'] #.strip('```\ntemplate\n')

    prompt = {
        'id': str(uuid.uuid4()),
        'name': prompt_name,
        'description': item['description'],
        'content': item['prompt'],
        'folderId': '',  # Placeholder, to be filled in create_export with the new folder's id
        'type': 'prompt',
        'data': {
            'author': 'ai:gpt-4-1106-preview'
        }
    }
    return prompt


def create_export(item):
    prompt = create_prompt(item)

    # Generate a unique id for the folder using UUIDv4
    folder_id = str(uuid.uuid4())

    # Set the folderId of the prompt to this new folder's id
    prompt['folderId'] = folder_id

    # Create a new folder with the 'Mkt: ' prefix followed by the prompt's name
    folder_name = 'Mkt: ' + prompt['name']
    folder = {
        'id': folder_id,
        'name': folder_name,
        'type': 'prompt',
    }

    export_content = {
        'version': 4,
        'prompts': [prompt],
        'folders': [folder],
        'history': []
    }

    # Create publish data for the export
    publish_data = create_publish_data(
        name=prompt['name'],
        description=prompt['description'],
        category=item['path'],  # Set category as per item.path
        tags=item.get('tags', []),  # Assuming tags may be included in the YAML data
        content=export_content
    )

    return publish_data


def create_publish_data(name, description, category, tags, content):
    publish_data = {
        'name': name,
        'description': description,
        'category': category,  # Now category is populated from item.path
        'tags': tags,
        'content': content
    }
    return publish_data


# Process to generate publish_data for all YAML items
def generate_publish_data_for_all_items(yaml_data):
    all_publish_data = []

    for key, item in yaml_data.items():
        publish_data = create_export(item)
        all_publish_data.append(publish_data)

    return all_publish_data


def publish_prompts(file_path, auth_token):
    with open(file_path, 'r') as file:
        yaml_data = yaml.safe_load(file)
        exports = generate_publish_data_for_all_items(yaml_data)
        publish_all_items(exports, auth_token)


def main():
    # Set up command-line argument parsing
    parser = argparse.ArgumentParser(description="Publish prompts from a YAML file to an API endpoint.")
    parser.add_argument("file_path", type=str, help="Path to the YAML file containing prompts.")
    parser.add_argument(
        "--endpoint",
        type=str,
        default='https://9o28pcdzkd.execute-api.us-east-1.amazonaws.com/dev',
        help="URL of the market (e.g., https://xyz.us-east-1.amazonaws.com/dev)."
    )
    parser.add_argument(
        "--auth",
        type=str,
        required=True,
        help="Bearer token for authorization."
    )

    # Parse the command line arguments
    args = parser.parse_args()
    file_path = args.file_path
    global API_ROOT  # Declare API_ROOT to set it from within main
    API_ROOT = args.endpoint  # Set API_ROOT based on provided endpoint argument
    auth_token = args.auth  # Get the provided auth token

    # Invoke the publish function with the provided file path and auth token
    publish_prompts(file_path, auth_token)


if __name__ == "__main__":
    main()