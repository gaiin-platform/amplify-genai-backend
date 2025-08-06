import json
import random
import re
import time
from functools import wraps
from typing import Dict, Any

import yaml
from pydantic import ValidationError

from llm.chat import chat_simple
from flow.spec import validate_dict, convert_keys_to_strings_based_on_spec


def generate_example(
    spec,
    string_instructions='Either enclose it in "" or make sure it is a yaml | string if it has quotes or new lines.',
):
    def generate_value(type_desc):
        parts = type_desc.split("-")
        type_info = parts[0].strip()
        note = parts[1].strip() if len(parts) > 1 else None
        if type_info == "bool":
            return random.choice([True, False])
        elif type_info == "str":
            return f"{note} {string_instructions}"
        elif type_info == "int":
            return random.randint(1, 100)
        elif type_info == "float":
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
    example["thought"] = (
        f"Some important thought about solving the problem step by step"
    )

    return example


def format_example(example):
    def represent_str(dumper, data):
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    yaml.add_representer(str, represent_str)

    return yaml.dump(example, sort_keys=False, default_flow_style=False)


def find_template_vars(template):
    pattern = r"\{\{(.*?)\}\}"
    paths = re.findall(pattern, template)
    return paths, pattern


def get_path_keys(path):
    path = path.replace("[", ".[")
    keys = path.split(".")
    return keys


def get_root_key(path):
    return get_path_keys(path)[0]


def resolve_and_set(data, path, new_data):
    """
    Set a value in a nested dictionary/list structure given a dot-notated or bracket-notated path.

    :param data: The dictionary or list to modify
    :param path: A string path like "foo.bar.baz" or "foo.bar[0].baz"
    :param new_data: The value to set at the end of the path
    """

    def parse_key(k):
        if k.startswith("[") and k.endswith("]"):
            return int(k[1:-1])
        return k

    keys = get_path_keys(path)
    result = data
    for key in keys[:-1]:
        if key:
            try:
                key = parse_key(key)
                if key not in result:
                    result[key] = (
                        {}
                        if isinstance(parse_key(keys[keys.index(key) + 1]), str)
                        else []
                    )
                result = result[key]
            except (KeyError, IndexError, TypeError):
                return None  # Return None if the path is invalid or cannot be set

    last_key = parse_key(keys[-1])
    result[last_key] = new_data
    return data


def resolve(data, path):
    """
    Resolve a dot-notated or bracket-notated path in a nested dictionary/list structure.

    :param data: The dictionary or list to traverse
    :param path: A string path like "foo.bar.baz" or "foo.bar[0].baz"
    :return: The value at the end of the path
    """

    def parse_key(k):
        if k.startswith("[") and k.endswith("]"):
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


def prompt_llm(prompt, system_prompt, access_token=None, model="gpt-4o"):
    # Placeholder response for demonstration purposes

    model = model or "gpt-4o"

    response = chat_simple(access_token, model, system_prompt, prompt)

    return response


def extract_yaml(response: str) -> str:
    start_marker = "```yaml"
    end_marker = "```"
    start = response.find(start_marker) + len(start_marker)
    # find in reverse
    end = response.rfind(end_marker)
    return response[start:end].strip()


def extract_json(response: str) -> str:
    start_marker = "```json"
    end_marker = "```"
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
                    time.sleep(delay * (backoff**retries))

        return wrapper

    return decorator


def fix_truncated_yaml_output(output):
    system_prompt = f"""
    The user made a mistake at the end of their YAML. The last part got cut off and now you need to fix it.
    You need to figure out a string suffix that can be appended to what the user wrote to complete the needed
    thought as succinctly as possible and turn it into valid yaml. This will likely just need to be 4-5 words
    and an ending " new line, etc.

    Your output with the data should be in the YAML format:
    \`\`\`yaml
thought: <INSERT THOUGHT>
suffix_to_fix: <INSERT JUST THE SUFFIX to concatenate with the provided data to make it valid yaml>
    \`\`\`
    
    You MUST provide the requested data. Make sure strings are YAML multiline strings
    that properly escape special characters.
    
    You ALWAYS output a \`\`\`yaml code block.
    """

    prompt_with_yaml = f"""
        The user's truncated YAML:
        ```yaml
        {output}
        ```
        
        Think about what got cut off. Output only the suffix that needs to be appended to
        the YAML to make it valid. 
        
        Examples:
        ---------
users_truncated_yaml: |
  person:
    name: "John
thought: "The quoted string 'John' is not closed and the name is not complete. It should be terminated with another double quote."
suffix_to_fix: ' Doe"'

users_truncated_yaml: |
  person:
    name: 'Jane
thought: "The quoted string 'Jane' is not closed. It should be terminated with another single quote."
suffix_to_fix: "'"

users_truncated_yaml: |
  greeting: "Hello, World
thought: "The quoted string 'Hello, World' is not closed. It should be terminated with another double quote."
suffix_to_fix: '"'

users_truncated_yaml: |
  multiline_string: >
    This is a multiline string
    that is missing its end new line
thought: "The multiline string is not properly closed with a line terminator. Ensure it is properly formatted to complete the entire string content."
suffix_to_fix: '\n'

users_truncated_yaml: |
  text: |
    "This is a block scalar text that is missing its end quote
thought: "The block scalar text defined by '|' is missing its closing quote. Ensure the string is properly terminated."
suffix_to_fix: '"\n'       
        
        
        ---------
        
        Here is what will be done with your suffix_to_fix:
        
        corrected_yaml = users_truncated_yaml + suffix_to_fix
        data = yaml.safe_load(corrected_yaml)
        
        """

    try:

        llm_response = prompt_llm(prompt_with_yaml, system_prompt)

        # Extract YAML from the LLM response
        yaml_content = extract_yaml(llm_response)
        parsed_data = yaml.safe_load(yaml_content)
        return parsed_data["suffix_to_fix"]
    except:
        return None


@with_retry(max_retries=3, delay=1, backoff=2, exceptions=(Exception,))
def dynamic_prompt(
    context: Dict[str, Any],
    template: str,
    system_prompt: str,
    output_spec: Dict[str, Any],
    access_token=None,
    model=None,
    output_mode="yaml",
) -> Dict[str, Any]:
    # Fill in the template
    filled_prompt = fill_prompt_template(context, template)
    filled_system_prompt = fill_prompt_template(context, system_prompt)

    # prompt_template = f"{system_prompt}\n{docstring}\nInputs:\n{input_descriptions}\nOutputs:\n{output_descriptions}"

    # print(f"Prompt Template: {prompt_template}")

    yaml_instructions = f"""
    You output with the data should be in the YAML format:
    \`\`\`yaml
thought: <INSERT THOUGHT>
{yaml.dump(generate_example(output_spec))}
    \`\`\`
    
    You MUST provide the requested data. Make sure strings are YAML multiline strings
    that properly escape special characters.
    
    You ALWAYS output a \`\`\`yaml code block.
    """

    mode_instructions = yaml_instructions
    # Just do a regular prompt
    if len(output_spec) == 0:
        output_mode = "plain"
        mode_instructions = ""
    elif output_mode == "json":
        example = generate_example(
            output_spec,
            "Make sure that all quotes and newlines are escaped properly for valid json.",
        )
        example["thought"] = "<Insert 1-2 sentence thought of thinking step by step>"
        mode_instructions = f"""
You output with the data should be in the JSON format:
\`\`\`json
{json.dumps(example)}
\`\`\`

You MUST provide the requested data. Make sure strings are valid JSON strings
that properly escape special characters, newlines, etc.

IMPORTANT!!! Make sure and escape any new line or special characters in the JSON string.

You ALWAYS output a \`\`\`json code block with all new lines and quotes escaped within json property values.
"""

    system_data_prompt = f"""
    {filled_system_prompt}
    
    Follow the user's instructions very carefully.
    Analyze the task or question and output the requested data.

    {mode_instructions}
    """

    # Call the LLM with the prompt
    llm_response = prompt_llm(filled_prompt, system_data_prompt, access_token, model)
    structured_data = None

    if output_mode == "yaml":
        # Extract YAML from the LLM response
        yaml_content = extract_yaml(llm_response)
        if not yaml_content:
            # see if it got cut off producing out and try to recover
            yaml_content = extract_yaml(f"{llm_response}\n\n```")
            fix = fix_truncated_yaml_output(yaml_content)
            yaml_content = f"{yaml_content}{fix}\n"

        # Parse the YAML content
        parsed_data = yaml.safe_load(yaml_content)
        structured_data = convert_keys_to_strings_based_on_spec(
            output_spec, parsed_data
        )
    elif output_mode == "json":
        # Extract JSON from the LLM response
        json_content = extract_json(llm_response)
        # if not json_content:
        #     # see if it got cut off producing out and try to recover
        #     json_content = extract_json(f"{llm_response}\n\n```")
        #     fix = fix_truncated_json_output(json_content)
        #     json_content = f"{json_content}{fix}\n"

        # Parse the JSON content
        try:
            parsed_data = json.loads(json_content)
            structured_data = convert_keys_to_strings_based_on_spec(
                output_spec, parsed_data
            )
        except json.JSONDecodeError as e:
            print("Failed to parse:", json_content)
            print("JSON Decode Error:", e)
            raise
    elif output_mode == "plain":
        parsed_data = llm_response

    # Validate and parse the data using the Pydantic model
    try:
        if output_spec and output_mode != "plain":
            valid, msg = validate_dict(output_spec, structured_data)
            if not valid:
                raise ValueError(f"LLM output validation Error: {msg}")
        else:
            print("No output spec provided. Skipping validation.")

        return parsed_data, {
            "llm_response": llm_response,
            "prompt": template,
            "system_prompt": system_prompt,
            "filled_prompt": filled_prompt,
            "filled_system_prompt": system_data_prompt,
        }
    except ValidationError as e:
        print("Validation Error:", e)
        raise
