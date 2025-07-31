import json
import re
from collections import defaultdict

def normalize_format(item_str):
    """Convert a JSON string into a format structure (dict with dummy values)"""
    try:
        item = json.loads(item_str)
        format_keys = {}
        for k, v in item.items():
            if isinstance(v, dict):
                format_keys[k] = "dict"
            elif isinstance(v, list):
                format_keys[k] = "list"
            else:
                format_keys[k] = type(v).__name__
        return json.dumps(format_keys, sort_keys=True)
    except json.JSONDecodeError:
        return None

def extract_templates_from_md(md_path, output_path=None):
    with open(md_path, "r") as f:
        content = f.read()

    # Split by table using the *** markers
    table_sections = re.split(r'\n\*\*\* \d+\. (.*?) \*\*\*\n', content)
    table_templates = defaultdict(list)

    # table_sections[0] is preamble, then [1]=table_name, [2]=table_content, repeat
    for i in range(1, len(table_sections), 2):
        table_name = f"*** {i//2 + 1}. {table_sections[i]} ***"
        table_content = table_sections[i + 1]

        # Extract JSON blocks from each section
        json_blocks = re.findall(r'```(?:json)?\n(.*?)\n```', table_content, re.DOTALL)
        seen_formats = set()

        for block in json_blocks:
            norm = normalize_format(block)
            if norm and norm not in seen_formats:
                seen_formats.add(norm)
                table_templates[table_name].append(json.loads(norm))

    # Build final output
    output_lines = []
    for table, formats in table_templates.items():
        output_lines.append(table + "\n")
        for idx, fmt in enumerate(formats, 1):
            output_lines.append(f"Template {idx}")
            output_lines.append(json.dumps(fmt, indent=2))
            output_lines.append("\n---------------------\n")
        output_lines.append("---------------------------------------------------------------\n")

    result = "\n".join(output_lines)

    if output_path:
        with open(output_path, "w") as out:
            out.write(result)

    return result


if __name__ == "__main__":
    md_input = "dynamodb_contents.md"
    output_file = "dynamodb_table_formats.md"
    result_text = extract_templates_from_md(md_input, output_file)
    print("✅ Done. Extracted unique formats per table to:")
    print(output_file)
