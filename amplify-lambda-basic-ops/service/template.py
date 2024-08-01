from typing import Dict, Any, List, Optional, Type
import yaml
from pydantic import BaseModel, create_model, ValidationError, validator, Field, field_validator

from llm.chat import chat_simple


# Placeholder for LLM function
def prompt_llm(prompt, system_prompt):
    # Placeholder response for demonstration purposes
    response = chat_simple(None, "gpt-4o", system_prompt, prompt)
    return response


def fill_template(template: str, context: Dict[str, Any]) -> str:
    return template.format(**context)


def extract_yaml(response: str) -> str:
    start_marker = '```yaml'
    end_marker = '```'
    start = response.find(start_marker) + len(start_marker)
    # find in reverse
    end = response.rfind(end_marker)
    return response[start:end].strip()


def validate_dict(spec, data):
    """
    Validate a dictionary against a specification.

    Example spec:

    spec = {
        'key1': 'str',
        'key2': 'int',
        'key3': 'float',
        'key4': 'bool',
        'key5': ['str'],
        'key6': {
            'subkey1': 'str',
            'subkey2': 'int',
            'subkey3': ['float']
        },
        'key7': [
            {
                'subkey1': 'str',
                'subkey2': 'int'
            }
        ]
    }

    :param spec:
    :param data:
    :return:
    """
    def parse_spec_entry(spec_entry):
        parts = spec_entry.split('-')
        description = ''
        if len(parts) == 2:
            spec = parts[0].strip()
            description = parts[1].strip()
        else:
            spec = parts[0].strip()
        return spec, description

    def validate(spec, data, path=''):
        if isinstance(spec, dict):
            if not isinstance(data, dict):
                raise ValueError(f"Expected dict at '{path}', got {type(data).__name__}")
            for key, subspec in spec.items():
                if key not in data:
                    raise ValueError(f"Missing key '{key}' at '{path}'")
                validate(subspec, data[key], path + '.' + key)
        elif isinstance(spec, list):
            if not isinstance(data, list):
                raise ValueError(f"Expected list at '{path}', got {type(data).__name__}")
            item_type = spec[0]
            for index, item in enumerate(data):
                validate(item_type, item, path + f'[{index}]')
        elif isinstance(spec, str):
            spec, description = parse_spec_entry(spec)

            if spec.startswith('list['):
                item_type = spec[5:-1]
                if not isinstance(data, list):
                    raise ValueError(f"Expected list at '{path}', got {type(data).__name__}")
                for index, item in enumerate(data):
                    validate(item_type, item, path + f'[{index}]')
            elif spec.startswith('dict['):
                if not isinstance(data, dict):
                    raise ValueError(f"Expected dict at '{path}', got {type(data).__name__}")
                inner_spec = parse_dict_spec(spec[5:-1])
                for key, value in inner_spec.items():
                    if key not in data:
                        raise ValueError(f"Missing key '{key}' at '{path}'")
                    validate(value, data[key], path + '.' + key)
            else:
                if not isinstance(data, eval_type(spec)):
                    raise ValueError(f"Expected {spec} at '{path}', got {type(data).__name__}")

    def eval_type(type_str):
        type_mapping = {
            'str': str,
            'int': int,
            'float': float,
            'bool': bool,
            'list': list,
            'dict': dict
        }
        if type_str in type_mapping:
            return type_mapping[type_str]
        raise ValueError(f"Unknown type '{type_str}'")

    def parse_dict_spec(spec_str):
        spec_str = spec_str.strip('{}')
        spec_items = spec_str.split(', ')
        spec = {}
        for item in spec_items:
            key, val = item.split(': ')
            spec[key] = val
        return spec

    try:
        validate(spec, data)
    except ValueError as e:
        return False, str(e)
    return True, "Validation successful"


def dynamic_prompt(context: Dict[str, Any], template: str, system_prompt: str, output_spec: Dict[str, Any]) -> Dict[str, Any]:
    # Fill in the template
    filled_prompt = fill_template(template, context)

    #prompt_template = f"{system_prompt}\n{docstring}\nInputs:\n{input_descriptions}\nOutputs:\n{output_descriptions}"

    #print(f"Prompt Template: {prompt_template}")

    system_data_prompt = f"""
    {system_prompt}
    
    Follow the user's instructions very carefully.
    Analyze the task or question and output the requested data.

    You output with the data should be in the YAML format:
    \`\`\`yaml
thought: <INSERT THOUGHT>
{yaml.dump(spec)}
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

# Example context, template, and system prompt
context = {"notes": "Some notes", "document_type": "university"}
template = """
Produce a sample of the desired output.
"""

    # Example usage:
spec = {
    'lines': ['str - the lines in the poem'],
    'full': {'lines': ['str']}
}

output = dynamic_prompt(context, template, "System prompt", spec)

print(output.get('lines'))


spec = {
    'key1': 'str - the name of the ',
    'key2': 'int',
    'key3': 'float',
    'key4': 'bool',
    'key5': ['str'],
    'key6': {
        'subkey1': 'str',
        'subkey2': 'int',
        'subkey3': ['float']
    },
    'key7': [
        {
            'subkey1': 'str',
            'subkey2': 'int'
        }
    ]
}


print(output.get('lines'))