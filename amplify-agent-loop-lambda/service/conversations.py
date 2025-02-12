import os
import json
import uuid

import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def register_agent_conversation(access_token, input, memory, name=None, session_id=None, tags=None, date=None,
                                data=None):
    api_base = os.getenv("API_BASE_URL")

    session_id = session_id or str(uuid.uuid4())

    date = date or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    if not api_base:
        raise Exception("API_BASE_URL is not set in the environment")

    url = f"{api_base}/state/conversation/register"

    def make_valid_message(msg):
        if not "role" in msg:
            msg["role"] = "user"
        if not "data" in msg:
            msg["data"] = {}
        return msg

    final_message = memory.get_memories()[-1]["content"]
    try:
        final_message = json.loads(final_message)
        if "result" in final_message:
            final_message = final_message["result"]
            if "message" in final_message:
                final_message = final_message["message"]
    except:
        pass

    def get_message(memory):

        role = memory["type"] if memory.get("type") in ["assistant", "user", "environment"] else "user"

        try:
            return {"role":role, "content":json.loads(memory["content"])}
        except:
            return {"role":role, "content":memory["content"]}

    messages = [
        {"role": "user", "content": input, "data": {}},
        {"role": "assistant", "content": final_message,
         "data": {
             "state": {
                 "agentLog": {
                     "data":
                         {
                             "session": session_id,
                             "handled": True,
                             "result": [get_message(m) for m in memory.get_memories()]
                         }
                 }

             }
         }
         }
    ]

    # Ensure the token is properly formatted
    access_token = access_token.strip()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # Get current date in "America/Chicago" timezone
    chicago_tz = ZoneInfo("America/Chicago")
    now_chicago = datetime.now(chicago_tz)

    payload = {
        "id": session_id,  # Optional
        "name": name or "Agent Conversation "+date,  # Required
        "messages": messages,  # Required
        "tags": tags or [],  # Optional, defaults to None
        "date": date,
        "data": data or {}  # Optional metadata
    }

    try:
        response = requests.put(url, headers=headers, data=json.dumps({"data": payload}))
        response.raise_for_status()
        return response.json()  # Return response data if successful

    except requests.exceptions.RequestException as e:
        raise Exception(
            f"Failed to register conversation: {e}, Response: {response.text if response else 'No response'}")  # Return response data

# os.environ["API_BASE_URL"] = "https://dev-api.vanderbilt.ai"
# access_token = os.getenv("ACCESS_TOKEN")
# result = register_agent_conversation(access_token, {
#     "name": "Test Conversation",
#     "messages": [
#         {
#             "role": "user",
#             "content": "Hello"
#         },
#         {
#             "role": "assistant",
#             "content": "Yo!"
#         }
#     ]
# })
