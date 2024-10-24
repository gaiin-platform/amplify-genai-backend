
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import uuid

from openai import OpenAI
import yaml
import argparse
import boto3
import os

MARKET_CATEGORIES_DYNAMO_TABLE='amplify-support-dev-market-categories'

parser = argparse.ArgumentParser(description='Load a YAML taxonomy into a database.')
parser.add_argument('filename', type=str, help='The YAML file containing the taxonomy.')
parser.add_argument('profile', type=str, help='The AWS profile to use.')
args = parser.parse_args()

print(f"Using profile {args.profile}")
boto3.setup_default_session(profile_name="vandy-amplify")

def get_secret_value(secret_name):
    # Create a Secrets Manager client
    client = boto3.client('secretsmanager')

    try:
        # Retrieve the secret value
        response = client.get_secret_value(SecretId=secret_name)
        secret_value = response['SecretString']
        return secret_value

    except Exception as e:
        raise ValueError(f"Failed to retrieve secret '{secret_name}': {str(e)}")

def get_openai_client():
    openai_api_key = get_secret_value("OPENAI_API_KEY")
    client = OpenAI(
        api_key=openai_api_key
    )
    return client


def interactive_chat_loop(client):
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
    ]

    while True:
        # Display current state of conversation and prompt user for input.
        for message in messages:
            print(f"{message['role'].capitalize()}: {message['content']}")

        user_input = input("Your message (type 'cancel' to exit or 'skip' to end this function): ")

        if user_input.lower() == 'cancel':
            print("Exiting...")
            exit()
        elif user_input.lower() == 'skip':
            print("Skipping...")
            return

        # Add user's message and get the response from the model.
        messages.append({"role": "user", "content": user_input})

        try:
            response = client.chat.completions.create(
                model="gpt-4-1106-preview",
                messages=messages
            )
            assistant_message = response.choices[0].message['content']
            messages.append({"role": "assistant", "content": assistant_message})
        except Exception as e:
            print(f"An error occurred while getting a response from the model: {e}")
            continue

        print("Assistant:", assistant_message)

        keep_going = input("Keep going? (type 'y' to continue): ")
        if keep_going.lower() != "y":
            print("Chat ended.")
            break


# Placeholder function to mimic creating and loading categories into the database.
dynamodb = boto3.resource('dynamodb')
# Assuming the DynamoDB table name is set as an environment variable
category_table = dynamodb.Table(MARKET_CATEGORIES_DYNAMO_TABLE)

def create_visible_categories_item():
    # Initialize boto3 client
    dynamodb = boto3.resource('dynamodb')

    # Scan the table for all items
    response = category_table.scan()
    items = response.get('Items', [])

    # Continue scanning if all items were not returned due to scan pagination
    while 'LastEvaluatedKey' in response:
        response = category_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response.get('Items', []))

    # Filter out the items that should not be included
    categories = [
        {'category': item['id'], 'name': item['id']}
        for item in items
        if item['id'] not in ['/', 'visible_categories']
    ]

    # Sort the list of category dictionaries by the 'name' field
    sorted_categories = sorted(categories, key=lambda x: x['name'])

    # Create the new item / update if it already exists
    visible_categories_item = {
        'id': 'visible_categories',
        'categories': sorted_categories
    }

    # Write the 'visible_categories' item to the table
    try:
        category_table.put_item(Item=visible_categories_item)
        print('Successfully updated the visible_categories item.')
    except Exception as e:
        print(f'Error updating the visible_categories item: {e}')

def create_category(name, path, description=None, tags=None):
    # Convert the snake_case name into title case for presentation
    readable_name = ' '.join(word.capitalize() for word in name.split('_'))

    # Prepare the item to insert into the DynamoDB table
    item = {
        'name': readable_name,  # or you might want to use the 'path' as DynamoDB primary key
        'id': path,
        'description': description if description else 'No description provided.',
        'tags': tags if tags else [],
        'icon': '',
        'updateId': str(uuid.uuid4()),
        'image': 'https://cdn.vanderbilt.edu/vu-URL/wp-content/uploads/sites/97/2021/09/19231133/Local-Color-campus-shot.jpg'
    }

    # Insert the item into the DynamoDB table
    try:
        response = category_table.put_item(Item=item)
        print(f'Successfully created category {readable_name}')
    except Exception as e:
        print(f'Error creating category {readable_name}: {e}')

# Function that constructs the full path for each subcategory by concatenating parent paths.
def construct_path_and_load(parent_path, category):
    if isinstance(category, dict):
        # Extract the description and tags if they are provided at the current level
        description = category.get('description', '')
        tags = category.get('tags', [])

        # Creating a category for this level.
        if description or tags:
            category_name = parent_path.split('/')[-1]  # Get the last part of the path as the name
            create_category(category_name, parent_path, description, tags)

        # Process any subcategories.
        for subcategory_name, subcategory_content in category.items():
            # Skip the description and tags keys
            if subcategory_name in ['description', 'tags', 'ideas', 'top_ideas', 'id']:
                continue
            subcategory_path = f"{parent_path}/{convert_to_snake_case(subcategory_name)}"
            construct_path_and_load(subcategory_path, subcategory_content)

# Helper function to convert strings into snake_case for consistent path names.
def convert_to_snake_case(text):
    return text.lower().replace(' ', '_').replace('-', '_')

# Set up command-line argument parsing.


# Load the YAML file specified as a command-line argument.
with open(args.filename, 'r') as stream:
    try:
        taxonomy = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        print(exc)
        exit()

# Start the loading process from the top level of the taxonomy.
for main_category_name, main_category_content in taxonomy["Marketplace_Taxonomy"].items():
    main_category_path = '/' + convert_to_snake_case(main_category_name)
    construct_path_and_load(main_category_path, main_category_content)
    create_visible_categories_item()