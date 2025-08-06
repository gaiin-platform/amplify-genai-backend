import os
import json
import uuid
import decimal
from decimal import Decimal

import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# Custom JSON encoder to handle Decimal objects
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


def register_agent_conversation(
    access_token,
    input,
    result,
    name=None,
    session_id=None,
    tags=None,
    date=None,
    data=None,
):
    api_base = os.getenv("API_BASE_URL")

    session_id = session_id or str(uuid.uuid4())

    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not api_base:
        raise Exception("API_BASE_URL is not set in the environment")

    url = f"{api_base}/state/conversation/register"

    final_message = "No Agent Response"
    for m in result[::-1]:
        content = m["content"]
        if "total_token_cost" not in content:
            final_message = content
            try:
                if isinstance(final_message, str):
                    final_message = json.loads(final_message)
            except:
                pass

            if "result" in final_message:
                final_message = final_message["result"]
                if "message" in final_message:
                    final_message = final_message["message"]

            if not isinstance(final_message, str):
                final_message = json.dumps(final_message)
            break

    message_body = "Content Unavailable."
    if input.get("metadata") and "requestContent" in input["metadata"]:
        message_body = input["metadata"]["requestContent"]

    ast_name = input.get("metadata", {}).get("assistant", {}).get("name")
    user_message = f"{message_body}" + (
        f"\n\nAssistant: {ast_name}" if ast_name else ""
    )

    messages = [
        {"role": "user", "content": user_message, "data": {}},
        {
            "role": "assistant",
            "content": final_message + (f"\n\n{date}"),
            "data": {
                "state": {
                    "agentLog": {
                        "data": {
                            "session": session_id,
                            "handled": True,
                            "result": result,
                        }
                    }
                }
            },
        },
    ]

    # Ensure the token is properly formatted
    access_token = access_token.strip()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    friendly_date = datetime.now(timezone.utc).strftime("%b, %d, %Y")
    conversation_name = name or ast_name or "Conversation"

    payload = {
        "id": session_id,  # Optional
        "name": f"Agent: {conversation_name} {friendly_date}",  # Required
        "messages": messages,  # Required
        "tags": tags or [],  # Optional, defaults to None
        "date": date,
        "data": data or {},  # Optional metadata
    }

    try:
        response = requests.post(
            url, headers=headers, data=json.dumps({"data": payload}, cls=DecimalEncoder)
        )
        response.raise_for_status()
        response_content = response.json()
        if response_content.get("success", False):
            print("Successfully registered conversation")

        return response_content  # Return response data if successful

    except requests.exceptions.RequestException as e:
        raise Exception(
            f"Failed to register conversation: {e}, Response: {response.text if response else 'No response'}"
        )  # Return response data
