import json
import os
import traceback

from agent.agents import actions_agent
from agent.game.action import ActionRegistry
from agent.game.agent_registry import AgentRegistry
from agent.game.environment import Environment
from agent.game.goal import Goal
from agent.prompt import create_llm
from agent.util import event_printer
from common.ops import vop


@vop(
    path="/vu-agent/handle-event",
    tags=["default"],
    name="agentHandleEvent",
    description="Trigger an agent to handle an event.",
    params={
        "sessionId": "The session ID.",
        "eventType": "The name of the event type.",
        "eventData": "The data for the event.",
    },
    schema={
        "type": "object",
        "properties": {
            "sessionId": {"type": "string"},
            "prompt": {"type": "string"},
            "metadata": {"type": "object"},
        },
        "required": ["prompt", "sessionId"],
    }
)
def handle_event(current_user, access_token, session_id, prompt, metadata=None):
    print(f"[{session_id}] Handling event '{prompt}'")

    try:
        metadata = metadata or {}
        environment = Environment()
        action_registry = ActionRegistry()

        llm = create_llm(access_token, metadata.get('model', os.getenv("AGENT_MODEL")))

        agent = actions_agent.build(environment, action_registry, llm)

        agent_registry = AgentRegistry()
        agent_registry.register("Action Agent", "Can use tools to take actions on behalf of the user.", agent)

        action_context_props={
            "event_handler": event_printer,
            "agent_registry": agent_registry
        }

        user_input = prompt

        result = agent.run(user_input=user_input, action_context_props=action_context_props)

        def load_memory_content(memory):
            content = memory['content']
            try:
                return json.loads(content)
            except:
                return content

        # Convert memory to a list of dicts
        result = [
            {
             "role":item['type'],
             "content": load_memory_content(item)
             }
            for item in result.items]

        return {
            "handled": True,
            "result": result
        }
    except Exception as e:
        # print a stack trace for the exception
        traceback.print_exc()
        print(f"Error handling event: {e}")
        return {
            "handled": False,
            "error": "Error handling event"
        }