import re
import yaml


def validate_output_spec(spec):
    def lint(spec, path=""):
        if isinstance(spec, dict):
            for key, value in spec.items():
                lint(value, path + "." + key if path else key)
        elif isinstance(spec, list):
            if not isinstance(spec[0], str):
                raise ValueError(
                    f"List type specification at '{path}' must be a string"
                )
            item_type, note = parse_type_and_note(spec[0])
            lint(item_type, path + "[list]")
        elif isinstance(spec, str):
            expected_type, note = parse_type_and_note(spec)
            if expected_type == "dict":
                return
            elif expected_type == "list":
                return
            elif expected_type.startswith("list["):
                item_type = expected_type[5:-1]
                lint(item_type, path + "[list]")
            elif expected_type.startswith("dict["):
                parse_dict_spec(expected_type[5:-1])
            else:
                validate_type(expected_type, path)

    def parse_type_and_note(type_note_str):
        # Split the type and note by the first occurrence of " - "
        parts = type_note_str.split(" - ", 1)
        return parts[0].strip(), parts[1] if len(parts) > 1 else ""

    def validate_type(type_str, path):
        type_mapping = {"str", "int", "float", "bool"}
        if type_str not in type_mapping:
            raise ValueError(f"Unknown type '{type_str}' at '{path}'")

    def parse_dict_spec(spec_str):
        spec_str = spec_str.strip("{}")
        spec_items = re.split(r",\s*(?=[a-zA-Z_]+:)", spec_str)
        spec = {}
        for item in spec_items:
            key, val = item.split(": ")
            validate_type(val, key)
        return spec

    try:
        lint(spec)
    except ValueError as e:
        return False, str(e)
    return True, "Specification is valid"


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
        parts = spec_entry.split("-")
        description = ""
        if len(parts) == 2:
            spec = parts[0].strip()
            description = parts[1].strip()
        else:
            spec = parts[0].strip()
        return spec, description

    def validate(spec, data, path=""):
        if isinstance(spec, dict):
            if not isinstance(data, dict):
                raise ValueError(
                    f"Expected dict at '{path}', got {type(data).__name__}"
                )
            for key, subspec in spec.items():
                if key not in data:
                    raise ValueError(f"Missing key '{key}' at '{path}'")
                validate(subspec, data[key], path + "." + key)
        elif isinstance(spec, list):
            if not isinstance(data, list):
                raise ValueError(
                    f"Expected list at '{path}', got {type(data).__name__}"
                )
            item_type = spec[0]
            for index, item in enumerate(data):
                validate(item_type, item, path + f"[{index}]")
        elif isinstance(spec, str):
            spec, description = parse_spec_entry(spec)

            if spec.startswith("list["):
                item_type = spec[5:-1]
                if not isinstance(data, list):
                    raise ValueError(
                        f"Expected list at '{path}', got {type(data).__name__}"
                    )
                for index, item in enumerate(data):
                    validate(item_type, item, path + f"[{index}]")
            elif spec.startswith("dict["):
                if not isinstance(data, dict):
                    raise ValueError(
                        f"Expected dict at '{path}', got {type(data).__name__}"
                    )
                inner_spec = parse_dict_spec(spec[5:-1])
                for key, value in inner_spec.items():
                    if key not in data:
                        raise ValueError(f"Missing key '{key}' at '{path}'")
                    validate(value, data[key], path + "." + key)
            else:
                if not isinstance(data, eval_type(spec)):
                    raise ValueError(
                        f"Expected {spec} at '{path}', got {type(data).__name__}"
                    )

    def eval_type(type_str):
        type_mapping = {
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
        }
        if type_str in type_mapping:
            return type_mapping[type_str]
        raise ValueError(f"Unknown type '{type_str}'")

    def parse_dict_spec(spec_str):
        spec_str = spec_str.strip("{}")
        spec_items = spec_str.split(", ")
        spec = {}
        for item in spec_items:
            key, val = item.split(": ")
            spec[key] = val
        return spec

    try:
        validate(spec, data)
    except ValueError as e:
        return False, str(e)
    return True, "Validation successful"


def convert_keys_to_strings_based_on_spec(spec, data):
    """
    Convert dictionary values to strings based on the specification.

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

    :param spec: Specification dict that defines which values should be converted to strings.
    :param data: Data dict to be processed.
    :return: Updated data dict with specified values converted to strings.
    """

    def convert(spec, data):
        if isinstance(spec, dict):
            if isinstance(data, dict):
                for key, subspec in spec.items():
                    if key in data:
                        data[key] = convert(subspec, data[key])
        elif isinstance(spec, list):
            if isinstance(data, list):
                item_type = spec[0]
                for index, item in enumerate(data):
                    data[index] = convert(item_type, item)
        elif isinstance(spec, str):
            if spec == "str" and not isinstance(data, str):
                return yaml.dump(data).strip()

        return data

    return convert(spec, data)
