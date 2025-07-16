import yaml
import os
import re
import pprint

class NoTagConstructorLoader(yaml.SafeLoader):
    pass

def unknown_tag_handler(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None

aws_tags = ['!Sub', '!Ref', '!GetAtt', '!Join', '!Select', '!ImportValue', '!If', '!Equals']
for tag in aws_tags:
    yaml.add_multi_constructor(tag, unknown_tag_handler, Loader=NoTagConstructorLoader)

def extract_contextual_info(filepath):
    with open(filepath, 'r') as f:
        try:
            doc = yaml.load(f, Loader=NoTagConstructorLoader)
        except Exception as e:
            print(f"Failed to parse {filepath}: {e}")
            return None

    service = doc.get('service', '')
    stage = doc.get('provider', {}).get('stage', 'dev')  # fallback to 'dev'

    env = doc.get('provider', {}).get('environment', {})
    table_refs = []

    for key, value in env.items():
        if 'DYNAMO_TABLE' in key or 'DYNAMODB_TABLE' in key or 'TABLE' in key:
            table_refs.append((key, value, filepath, service, stage))

    return table_refs

def get_nested_value(data, path):
    """Resolve a nested dot-separated path like custom.stageVars.DEP_NAME"""
    try:
        for part in path.split('.'):
            data = data[part]
        return data
    except (KeyError, TypeError):
        return '${self:custom.stageVars.DEP_NAME}'

def resolve_vars(table_refs):
    resolved = []

    for key, name, path, service, stage in table_refs:
        with open(path, 'r') as f:
            doc = yaml.load(f, Loader=NoTagConstructorLoader)

        # # Add this debug print to inspect the YAML's 'custom' section
        # print(f"\n--- YAML 'custom' section from: {path} ---")
        # pprint.pprint(doc.get('custom', {}))

        resolved_name = name

        # Resolve all ${self:...} variables
        self_vars = re.findall(r"\${self:([a-zA-Z0-9_.]+)}", resolved_name)
        for var in self_vars:
            val = get_nested_value(doc, var)
            resolved_name = resolved_name.replace(f"${{self:{var}}}", str(val))

        # Resolve ${sls:stage}
        if stage:
            resolved_name = resolved_name.replace('${sls:stage}', stage)

        # Resolve ${self:service}
        resolved_name = resolved_name.replace('${self:service}', service)

        # Resolve ${opt:stage, 'dev'} or ${opt:stage, "dev"}
        opt_matches = re.findall(r"\${opt:stage, ?['\"]([^'\"]+)['\"]}", resolved_name)
        for match in opt_matches:
            resolved_name = re.sub(r"\${opt:stage, ?['\"][^'\"]+['\"]}", stage or match, resolved_name)

        resolved.append((key, name, resolved_name, path))

    return resolved


def scan_and_resolve(directory):
    refs = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.yaml') or file.endswith('.yml'):
                full_path = os.path.join(root, file)
                result = extract_contextual_info(full_path)
                if result:
                    refs.extend(result)
    return resolve_vars(refs)

def write_resolved_markdown(data, output_file):
    with open(output_file, 'w') as f:
        f.write("# Resolved DynamoDB Environment Table References\n\n")
        f.write("| # | Env Var Key | Raw Table Name | Resolved Table Name | File |\n")
        f.write("|---|-------------|----------------|----------------------|------|\n")
        for i, (key, raw, resolved, path) in enumerate(data, 1):
            f.write(f"| {i} | `{key}` | `{raw}` | `{resolved}` | `{path}` |\n")
    print(f"✅ Saved resolved table list to: {output_file}")

if __name__ == "__main__":
    directory = "./"  # Change if needed
    output_file = "table_test.md"

    data = scan_and_resolve(directory)
    write_resolved_markdown(data, output_file)
