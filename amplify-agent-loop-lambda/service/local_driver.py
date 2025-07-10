import os

import service.core
from service.handlers import handle_event

# Example usage:
if __name__ == "__main__":

    access_token = os.environ.get("AMPLIFY_ACCESS_TOKEN", "")
    current_user = input("Enter the current user: ")
    agent_prompt = input("Enter the agent prompt: ")

    metadata = {
        "assistant": {
            "instructions": "Act really happy and have over the top happiness in your answers",
            "data": {
                "operations": [
                    {
                        "method": "POST",
                        "name": "getOperations",
                        "description": "Get a list of available operations for an assistant.",
                        "id": "getOperations",
                    }
                ]
            },
        }
    }

    handle_event(current_user, access_token, "1", agent_prompt, metadata)
