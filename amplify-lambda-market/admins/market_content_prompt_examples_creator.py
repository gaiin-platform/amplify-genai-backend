import uuid

from openai import OpenAI
import yaml
import argparse
import boto3
import os
import re
import concurrent.futures
import requests
import json

import market.market

# parser = argparse.ArgumentParser(description='Load a YAML taxonomy into a database.')
# parser.add_argument('filename', type=str, help='The YAML file containing the taxonomy.')
# parser.add_argument('profile', type=str, help='The AWS profile to use.')
# args = parser.parse_args()
#
# print(f"Using profile {args.profile}")

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

def prompt_llm(client, system, user):
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user}
    ]

    result = ""
    response = client.chat.completions.create(
        model="gpt-4-1106-preview",
        messages=messages
    )

    return response.choices[0].message.content

def extract_prompt_template(prompt):
    pattern = r"```template\s*\n(.*?)```"
    matches = re.findall(pattern, prompt, re.DOTALL)
    if matches:
        return matches[0]
    else:
        return None


def prompt_for_template_usage_idea(client, description):
    value = prompt_llm(client,
                       f"""
                            Act as a user working with the following prompt template for ChatGPT.
                            
                            ----------------
                            {description}
                            ----------------
                            
                            You will come up with a task that you could use this template for. You will
                            describe this task in a usage scenario with 3-5 sentences and enough information
                            to understand the task.
                            
                            You will provide the scenario in a ```template code block.
                            """,
                       f"""
                            My scenario / task to use the template for is: 
                            ```template
                            
                            """)
    # check if ```template is in value
    if "```template" in value:
        value = extract_prompt_template(value)

    return value

def prompt_for_variable_value(client, task, description, variable):
    value = prompt_llm(client,
                          f"""
                            Act as a user working with the following prompt template for ChatGPT.
                            
                            You have decided to use the template for the following task:
                            ----------------
                            {task}
                            ----------------
                            
                            The template has the following description.
                            ----------------
                            {description}
                            ----------------
                          
                            You need to come up with sample data for all the variables in the template
                            based on your task. 
                          
                            Please provide a value for the variable: {variable}
                            
                            You will provide the value in a ```template code block.
                            """,
                            f"""
                            My value for this variable is: 
                            ```template
                            
                            """)
    # check if ```template is in value
    if "```template" in value:
        value = extract_prompt_template(value)

    return value

def describe_prompt(client, prompt):
    system = """
You will be provided a prompt template for a prompt to be sent to an LLM. The information that the user provides is denoted with <USER: PLACEHOLDER>.
What the LLM will infer and output is denoted with <LLM: PLACEHOLDER>. In 2-3 sentences, describe what the user will
provide and what the LLM will infer and output. The output of the LLM should always be described as a "draft" that
that should be double checked by the user and serve as a starting point for the user to iterate on.
"""

    user = f"""The prompt template is: 
```template
{prompt}
```

The 2-3 sentences describing it are:

"""
    return prompt_llm(client, system, user)

def generate_example(client, root_prompt, prompt):

    try:
        name = prompt['name']
        content = prompt['content']
        original_content = content
        description = prompt['description']

        if len(description) < 150:
            print("Generating a better description...")
            description = describe_prompt(client, original_content)

        print(f"Generating example for: {name}")
        print(f"with Root Prompt: {root_prompt}")

        task = prompt_for_template_usage_idea(client, description)
        print(f"Task: {task}")

        matches = re.findall(r'{{(.*?)(?::.*?)?}}', content)
        matches = [match.strip() for match in matches]

        print(f"Variables: {matches}")

        replacements = {}

        for template_var in matches:
            value = prompt_for_variable_value(client, task, description, template_var)
            # print(f"Value for {template_var}: {value}")
            replacements[template_var] = value

        for old, new in replacements.items():
            content = re.sub(r'{{\s*' + old + r'(\s*:.*?)?}}', '' + new + '', content)


        # print("Updated template:")
        # print(content)

        messages = [
            {"role": "system", "content": root_prompt},
            {"role": "user", "content": content}
        ]

        example = ""
        response = client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=messages
        )

        example = response.choices[0].message.content

        # print("=============================")
        # print("Example:")
        # print(example)

        def new_message(role, content):
            return {
                'role': role,
                'content': content,
                'type': 'prompt',
                'data': {},
                'id': str(uuid.uuid4()),
            }

        conversation = {
            "id": str(uuid.uuid4()),
            "name": f"Example of '{name}'",
            "messages": [new_message('user', content),
                         new_message('assistant', example)],
            "model": {
                'id': "gpt-4-1106-preview",
                    'name': 'GPT-4-Turbo',
                    'maxLength': 24000,
                    'tokenLimit': 8000,
                    'visible': True,
                },
            'temperature': 1.0,
            'folderId': None,
            'promptTemplate': prompt,
        }

        return {
            "name": name,
            "id": prompt['id'],
            "task": task,
            "description": description,
            "variables": replacements,
            "conversation": conversation,
            "prompt": prompt,
            "root_prompt": root_prompt
        }

    except Exception as e:
        # Try again
        print(f"An error occurred while generating an idea: {e}")
        print("Trying again...")
        return generate_example(client, prompt)


def get_market_item(api_url, auth_token, item_id):
    url = f"{api_url}/item/get"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {auth_token}"}
    data = {"data": {"id": item_id}}
    response = requests.post(url, headers=headers, data=json.dumps(data))

    if response.status_code == 200:
        response_content = response.json()
        success = response_content.get('success', False)
        if success:
            return response_content.get('data')
        else:
            raise Exception(response_content.get('message', 'Unknown error'))
    else:
        response.raise_for_status()

def get_root_prompt(root_prompts_by_id, prompt):
    root_prompt_id = prompt.get('data', {}).get('rootPromptId', 'default')
    root_prompt = root_prompts_by_id.get(root_prompt_id, root_prompts_by_id['default'])
    return root_prompt

def generate_examples_in_parallel(client, root_prompts_by_id, prompts):
    results = []
    remaining = len(prompts)
    with concurrent.futures.ThreadPoolExecutor() as executor:
        # Schedule the function to be executed multiple times with the same arguments
        futures = {executor.submit(generate_example, client, get_root_prompt(root_prompts_by_id, prompt), prompt) for prompt in prompts}
        for future in concurrent.futures.as_completed(futures):
            try:
                timeout_duration = 60 * 3  # 3 minutes
                # Get the result of each future as they complete
                results.append(future.result(timeout=timeout_duration))
                remaining -= 1
                print(f"Remaining examples to generate: {remaining}")
            except Exception as e:
                # Handle exception accordingly
                print(f"An exception occurred: {e}")

    return results

def get_item(item_table_name, item_id):
    dynamodb = boto3.resource('dynamodb')
    item_table = dynamodb.Table(item_table_name) #dynamodb.Table(os.environ['MARKET_ITEMS_DYNAMO_TABLE'])

    # Check if category exists in the category table by seeing if an item with that
    # ID exists
    response = item_table.get_item(Key={'id': item_id})
    if 'Item' not in response:
        print(f"Item {item_id} does not exist")
        raise ValueError(f"Item {item_id} does not exist")

    return response['Item']

def create_examples_of_market_item(item_bucket_name, item_table_name, item_id):
    try:
        default_root = "You are ChatGPT, a large language model trained by OpenAI. " \
                       "Follow the user's instructions carefully. Respond using markdown. " \
                       "You can use mermaid code blocks using mermaid.js syntax to draw diagrams."

        data = get_item(item_table_name, item_id)

        print(f"Data Item: {data}")

        category = data['category']

        if not category:
            raise ValueError(f"Item {item_id} does not have a category")

        prompts = data['content']['prompts']
        prompt_list = [prompt for prompt in prompts if prompt.get('type') == 'prompt']
        follow_up_list = [prompt for prompt in prompts if prompt.get('type') == 'follow_up']
        automation_list = [prompt for prompt in prompts if prompt.get('type') == 'automation']
        root_prompt_list = [prompt for prompt in prompts if prompt.get('type') == 'root_prompt']

        root_prompts_by_id = {prompt['id']: prompt['content'] for prompt in root_prompt_list}
        root_prompts_by_id['default'] = default_root

        results = generate_examples_in_parallel(get_openai_client(), root_prompts_by_id, prompt_list)

        key = market.market.save_item_example(item_bucket_name, category, item_id, results)
        print(f"Saved examples to {key}")

        return results

    except Exception as e:
        print(f"An error occurred generating examples of the market item: {str(e)}")
        raise e

def generate_examples_for_items_in_parallel(client, table_name, item_bucket_name, item_ids):
    results = []
    remaining = len(item_ids)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Schedule the function to be executed multiple times with the same arguments
        futures = {executor.submit(create_examples_of_market_item, item_bucket_name, table_name, item_id) for item_id in item_ids}
        for future in concurrent.futures.as_completed(futures):
            try:
                timeout_duration = 60 * 3  # 3 minutes
                # Get the result of each future as they complete
                results.append(future.result(timeout=timeout_duration))
                remaining -= 1
                print(f"Remaining examples to generate: {remaining}")
            except Exception as e:
                # Handle exception accordingly
                print(f"An exception occurred: {e}")

    return results

def main():
    parser = argparse.ArgumentParser(description='Send a POST request.')
    parser.add_argument('--item_id', type=str, required=True, help='The ID of the item.')
    args = parser.parse_args()

    try:
        table_name='amplify-support-dev-market-items'
        item_bucket_name='amplify-support-dev-market-index'

        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(table_name)

        response = table.scan()
        data = response['Items']

        id_list = [item['id'] for item in data if 'id' in item]  # replace 'id' with your id key if it's different

        print(f"Generating: {len(id_list)} examples")

        results = generate_examples_for_items_in_parallel(get_openai_client(), table_name, item_bucket_name, id_list)
        # results = create_examples_of_market_item(item_bucket_name, table_name, args.item_id)
        print(f"Generated: {len(results)} examples")
        # print(f"Examples:\n\n {results}")
        # print(data)
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main()

#
# prompt = {
#     "data": {
#         "author": "ai:gpt-4-1106-preview"
#     },
#     "name": "AI-Assisted Text Summarization Tool\n",
#     "description": "The user will provide the text of an article or report that they would like to be summarized. The LLM will infer the essential aspects of the provided text, such as the main goal, key findings, methodologies, implications, and conclusions presented. The output will be a structured draft summary, organized in bullet points as requested, which the user should review and refine for accuracy and coherence.",
#     "id": "8e7c83a1-e67f-44cc-bc0b-8ce216a64655",
#     "type": "prompt",
#     "content": "----------------\nHi ChatGPT, I need your assistance in summarizing a lengthy article/report for easier digestion of its key points and main ideas. \n\nHere's the text:\n{{ARTICLE/REPORT TEXT}}\n\nPlease provide a structured summary that includes:\n1. <LLM: The main goal or objective of the article/report.>\n2. <LLM: A brief overview of the key findings or results.>\n3. <LLM: Any important methods or approaches used in reaching these findings.>\n4. <LLM: The implications or significance of the results.>\n5. <LLM: Any conclusions or recommendations made by the authors.>\n\nI'm looking for a clear, concise, and coherent summary in about three paragraphs that allows someone to understand the essence of the article/report without having to go through the entire document.\n\nThank you!\n----------------\n",
#     "folderId": "35a4ea59-910d-4906-a357-4a09291cfe08"
# }
#
# generate_example(get_openai_client(), prompt)