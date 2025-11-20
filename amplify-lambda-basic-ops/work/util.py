import re


def extract_sections(prefixes, text):

    # Combine prefixes into a single regex pattern, escaping special characters
    pattern = "|".join(re.escape(prefix) for prefix in prefixes)

    # Split text based on the pattern
    sections = re.split(f"({pattern})", text)

    # If no sections, return one item with the entire text
    if len(sections) < 2:
        return [{"key": "Content:", "value": sections[0].strip()}]

    # Compile the list of dictionaries
    section_list = []
    for i in range(1, len(sections), 2):
        section_list.append({"key": sections[i], "value": sections[i + 1].strip()})

    return section_list
