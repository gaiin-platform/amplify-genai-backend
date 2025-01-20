import json
from typing import Dict, Any


def event_printer(event_id: str, event: Dict[str, Any]):
    context_id_prefix = event.get("context_id", "na")
    correlation_id = event.get("correlation_id", None)

    if correlation_id:
        context_id_prefix = f"{context_id_prefix}/{correlation_id}"

    print(f"{context_id_prefix} Event: {event_id}")
    if event_id == "agent/prompt/action/raw_result":
        print("  Agent Response:")
        print(event["response"])


def resolve_dict_references(args, results):
    return {k: v for k, v in [(k, resolve_references(v, results)) for k, v in args.items()]}


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
        val = json.dumps(val)

        # check if k starts with $# and strip it off of k
        if k.startswith("$#"):
            k = k[2:]

        # escape all of the strings and newlines
        val = val.replace('"', '\\"')
        val = val.replace("\n", "\\n")
        val = val.replace("\r", "\\r")
        val = val.replace("'", "\\'")

        v = v.replace(f"$#{k}", val)

    return v
