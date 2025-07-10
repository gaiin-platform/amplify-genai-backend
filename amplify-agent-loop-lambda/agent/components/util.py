import json
from typing import Dict, Any


def event_printer(event_id: str, event: Dict[str, Any]):
    context_id_prefix = event.get("context_id", "na")
    correlation_id = event.get("correlation_id", None)

    if correlation_id:
        context_id_prefix = f"{context_id_prefix}/{correlation_id}"

    if event_id == "agent/prompt/action/raw_result":
        print("Raw Agent Response:")
        print(event["response"])
    elif event_id == "tools/code_exec/execute/error":
        print("Code Execution Error:")
        print(event["error"])
        print("  Traceback:")
        print(event["traceback"])
    elif event_id == "tools/code_exec/execute/end":
        print("Code Execution Result:")
        # Truncate result to first 1000 characters
        result_str = str(event["result"])
        print(result_str[:1000])
    elif event_id == "tools/code_exec/execute/start":
        print("Code Execution Start:")
        print(event["code"])
    # check if the event starts with tools/
    elif event_id.startswith("tools/"):
        if event_id.endswith("/start"):
            print("Tool Event:")
            print(json.dumps(event, indent=2))
        if event_id.endswith("/end"):
            print("Result:")
            print(json.dumps(event["result"], indent=2))
        if event_id.endswith("/error"):
            print("Error!!!!!:")
            print(event["exception"])
            print(event["traceback"])


def resolve_dict_references(args, results):
    return {
        k: v for k, v in [(k, resolve_references(v, results)) for k, v in args.items()]
    }


def resolve_list_references(args, results):
    return [resolve_references(v, results) for v in args]


def resolve_references(v, results):
    if isinstance(v, dict):
        if "ref" in v:
            return results[v["ref"]]
        else:
            return resolve_dict_references(v, results)
    elif isinstance(v, list):
        return resolve_list_references(v, results)
    elif isinstance(v, str):
        return resolve_string(v, results)
    else:
        return v


def resolve_string(v, results):
    # check if $# is in the string
    if "$#" not in v:
        return v

    for k, val in results.items():
        if not isinstance(val, str):
            val = json.dumps(val)
            # # escape all of the strings and newlines
            # val = val.replace('"', '\\"')
            # val = val.replace("\n", "\\n")
            # val = val.replace("\r", "\\r")
            # val = val.replace("'", "\\'")

        # check if k starts with $# and strip it off of k
        if k.startswith("$#"):
            k = k[2:]

        v = v.replace(f"$#{k}", val)

    return v


def extract_markdown_block(response, block_type="json"):
    """
    Extracts a markdown code block of a specified type from the response.

    Args:
        response (str): The response containing the markdown block.
        block_type (str, optional): The type of code block to extract (default is "json").

    Returns:
        dict or str or None: Parsed JSON if block_type is "json", raw string if another type, or None if not found.
    """
    if not isinstance(response, str) or not response.strip():
        return None  # Return None for non-string or empty input

    start_marker = f"```{block_type}"
    end_marker = "```"

    try:
        stripped_response = response.strip()
        start_index = stripped_response.find(start_marker)
        end_index = stripped_response.rfind(end_marker)

        if start_index >= end_index:
            end_index = len(stripped_response)

        if start_index == -1 or end_index == -1 or end_index <= start_index:
            return None  # No valid markdown block found

        extracted_block = stripped_response[
            start_index + len(start_marker) : end_index
        ].strip()

        if block_type == "json":
            try:
                return json.loads(extracted_block)  # Safely parse JSON
            except json.JSONDecodeError:
                return None  # Invalid JSON structure

        return extracted_block  # Return raw string if it's not JSON

    except Exception:
        return None  # Catch all other unexpected errors and return None


def add_line_numbers(text: str) -> str:
    """
    Adds line numbers to each line of the given text.
    """
    lines = text.splitlines()
    numbered_lines = [f"{i + 1}: {line}" for i, line in enumerate(lines)]
    return "\n".join(numbered_lines)
