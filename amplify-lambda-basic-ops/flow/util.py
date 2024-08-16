import random
import re
import time
from functools import wraps
from typing import Dict, Any

import yaml
from pydantic import ValidationError

from llm.chat import chat_simple
from flow.spec import validate_dict


def generate_example(spec):
    def generate_value(type_desc):
        parts = type_desc.split('-')
        type_info = parts[0].strip()
        note = (parts[1].strip() if len(parts) > 1 else None)
        if type_info == 'bool':
            return random.choice([True, False])
        elif type_info == 'str':
            return f"{note} Either enclose it in \"\" or make sure it is a yaml | string if it has quotes or new lines."
        elif type_info == 'int':
            return random.randint(1, 100)
        elif type_info == 'float':
            return round(random.uniform(0, 100), 2)
        else:
            return type_info

    def process_item(item):
        if isinstance(item, dict):
            return {k: process_item(v) for k, v in item.items()}
        elif isinstance(item, list):
            return [process_item(v) for v in item]
        elif isinstance(item, str):
            value = generate_value(item)
            return value
        else:
            return item

    example = process_item(spec)

    # Add a random 'thought' field
    example['thought'] = f"Some important thought about solving the problem step by step"

    return example


def format_example(example):
    def represent_str(dumper, data):
        if '\n' in data:
            return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
        return dumper.represent_scalar('tag:yaml.org,2002:str', data)

    yaml.add_representer(str, represent_str)

    return yaml.dump(example, sort_keys=False, default_flow_style=False)


def find_template_vars(template):
    pattern = r'\{\{(.*?)\}\}'
    paths = re.findall(pattern, template)
    return paths, pattern


def get_path_keys(path):
    path = path.replace('[', '.[')
    keys = path.split('.')
    return keys


def get_root_key(path):
    return get_path_keys(path)[0]


def resolve(data, path):
    """
    Resolve a dot-notated or bracket-notated path in a nested dictionary/list structure.

    :param data: The dictionary or list to traverse
    :param path: A string path like "foo.bar.baz" or "foo.bar[0].baz"
    :return: The value at the end of the path
    """
    def parse_key(k):
        if k.startswith('[') and k.endswith(']'):
            return int(k[1:-1])
        return k

    keys = get_path_keys(path)
    result = data
    for key in keys:
        if key:
            try:
                result = result[parse_key(key)]
            except (KeyError, IndexError, TypeError):
                return None  # Return None if the path is invalid
    return result


def prompt_llm(prompt, system_prompt):
    # Placeholder response for demonstration purposes
    response = chat_simple(None, "gpt-4o", system_prompt, prompt)
    return response


def extract_yaml(response: str) -> str:
    start_marker = '```yaml'
    end_marker = '```'
    start = response.find(start_marker) + len(start_marker)
    # find in reverse
    end = response.rfind(end_marker)
    return response[start:end].strip()


def fill_prompt_template(context, template):
    def replace_var(match):
        path = match.group(1).strip()
        value = resolve(context, path)

        if isinstance(value, dict) or isinstance(value, list):
            return yaml.dump(value)
        if isinstance(value, str):
            return value
        else:
            return str(value) if value is not None else f"{{{path}}}"

    # Find all {{...}} patterns
    paths, pattern = find_template_vars(template)

    # Replace all {{...}} patterns with their resolved values
    filled_template = re.sub(pattern, replace_var, template)

    return filled_template


def with_retry(max_retries=3, delay=1, backoff=2, exceptions=(Exception,)):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    print(f"Attempt {retries + 1} failed: {e}")
                    retries += 1
                    if retries == max_retries:
                        raise
                    time.sleep(delay * (backoff ** retries))
        return wrapper
    return decorator


@with_retry(max_retries=3, delay=1, backoff=2, exceptions=(Exception,))
def dynamic_prompt(context: Dict[str, Any], template: str, system_prompt: str, output_spec: Dict[str, Any]) -> Dict[str, Any]:
    # Fill in the template
    filled_prompt = fill_prompt_template(context, template)
    filled_system_prompt = fill_prompt_template(context, system_prompt)

    #prompt_template = f"{system_prompt}\n{docstring}\nInputs:\n{input_descriptions}\nOutputs:\n{output_descriptions}"

    #print(f"Prompt Template: {prompt_template}")

    system_data_prompt = f"""
    {filled_system_prompt}
    
    Follow the user's instructions very carefully.
    Analyze the task or question and output the requested data.

    You output with the data should be in the YAML format:
    \`\`\`yaml
thought: <INSERT THOUGHT>
{yaml.dump(generate_example(output_spec))}
    \`\`\`
    
    You MUST provide the requested data. Make sure strings are YAML multiline strings
    that properly escape special characters.
    
    You ALWAYS output a \`\`\`yaml code block.
    """

    # Call the LLM with the prompt
    llm_response = prompt_llm(filled_prompt, system_data_prompt)

    # Extract YAML from the LLM response
    yaml_content = extract_yaml(llm_response)

    # Parse the YAML content
    parsed_data = yaml.safe_load(yaml_content)

    # Validate and parse the data using the Pydantic model
    try:
        if output_spec:
            valid, msg = validate_dict(output_spec, parsed_data)
            if not valid:
                raise ValueError(f"LLM output validation Error: {msg}")

        return parsed_data
    except ValidationError as e:
        print("Validation Error:", e)
        raise
