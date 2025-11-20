import json
from typing import List, Any

from agent.core import (
    AgentLanguage,
    Goal,
    Memory,
    Environment,
    Action,
    UnknownActionError,
)
from agent.prompt import Prompt


def to_json_memory_messages_format(items):
    mapped_items = []
    for item in items:

        content = item.get("content", None)
        if not content:
            content = json.dumps(item, indent=4)

        if item["type"] == "prompt":
            continue
        if item["type"] == "assistant":
            if "skipped" in content:
                skip_reason = content.get("skipped", "No reason provided")
                content = f"Skipped step: '{content.get('tool', 'Unknown tool')}' \nSkipped reason: {skip_reason}"
            mapped_items.append({"role": "assistant", "content": content})
        elif item["type"] == "system":
            mapped_items.append({"role": "system", "content": content})
        elif item["type"] == "environment":
            mapped_items.append({"role": "user", "content": content})
        else:
            mapped_items.append({"role": "user", "content": content})
    return mapped_items


class AgentNaturalLanguage(AgentLanguage):
    def __init__(self):
        super().__init__()

    def format_goals(self, goals: List[Goal]) -> List:
        # Map all goals to a single string that concatenates their description
        # and combine into a single message of type system
        goal_instructions = "\n".join([f"{goal.description}" for goal in goals])
        return [{"role": "system", "content": goal_instructions}]

    def format_memory(self, memory: Memory) -> List:
        """Generate response from language model"""
        # Map all environment results to a role:user messages
        # Map all assistant messages to a role:assistant messages
        # Map all user messages to a role:user messages
        items = memory.get_memories()
        mapped_items = to_json_memory_messages_format(items)

        return mapped_items

    def construct_prompt(
        self,
        actions: List[Action],
        environment: Environment,
        goals: List[Goal],
        memory: Memory,
    ) -> Prompt:

        prompt = []
        prompt += self.format_goals(goals)
        prompt += self.format_memory(memory)

        return Prompt(messages=prompt, tools=[])

    def adapt_prompt_after_parsing_error(
        self,
        prompt: Prompt,
        response: str,
        traceback: str,
        error: Any,
        retries_left: int,
    ) -> Prompt:
        return prompt

    def parse_response(self, response: str) -> dict:
        """Parse LLM response into structured format by extracting the ```json block"""
        return {"tool": "terminate", "args": {"message": response}}


class AgentJsonActionLanguage(AgentLanguage):

    action_format = """
<Stop and think step by step. Insert a rich description of your step by step thoughts here.>

```action
{
    "tool": "tool_name",
    "args": {...fill in any required arguments here...}
}
```"""

    def __init__(self):
        super().__init__()

    def format_goals(self, goals: List[Goal]) -> List:
        # Map all goals to a single string that concatenates their description
        # and combine into a single message of type system
        sep = "\n-------------------\n"
        goal_instructions = "\n\n".join(
            [f"{goal.name}:{sep}{goal.description}{sep}" for goal in goals]
        )
        return [{"role": "system", "content": goal_instructions}]

    def format_memory(self, memory: Memory) -> List:
        """Generate response from language model"""
        # Map all environment results to a role:user messages
        # Map all assistant messages to a role:assistant messages
        # Map all user messages to a role:user messages
        items = memory.get_memories()
        mapped_items = to_json_memory_messages_format(items)

        return mapped_items

    def format_actions(self, actions: List[Action]) -> List:
        """Generate response from language model"""

        action_descriptions = [
            {
                "name": action.name,
                "description": action.description,
                "parameters": action.parameters,
            }
            for action in actions
        ]

        return [
            {
                "role": "system",
                "content": f"""
Available Tools: {json.dumps(action_descriptions, indent=4)}

When you are done, terminate the conversation by using the "terminate" tool and I will 
provide the results to the user.

Important!!! Every response MUST have an 'action' which is defined by outputting an  ```action block containing valid json.
You must ALWAYS respond in this format:

{AgentJsonActionLanguage.action_format}
""",
            }
        ]

    def construct_prompt(
        self,
        actions: List[Action],
        environment: Environment,
        goals: List[Goal],
        memory: Memory,
    ) -> Prompt:

        prompt = []
        prompt += self.format_goals(goals)
        prompt += self.format_actions(actions)
        prompt += self.format_memory(memory)

        return Prompt(messages=prompt, tools=[])

    def adapt_prompt_after_parsing_error(
        self,
        prompt: Prompt,
        response: str,
        traceback: str,
        error: Any,
        retries_left: int,
    ) -> Prompt:

        if isinstance(error, UnknownActionError):
            feedback = f"Your last output contained an unknown action. {error}."
        else:
            feedback = f"Your last output did not contain a valid ```action block that could be parsed. \n"
            f"Please fix your prior response. \n"
            f"Make sure that it has the correct format: \n"
            f"{AgentJsonActionLanguage.action_format}"

        new_messages = prompt.messages + [
            {"role": "assistant", "content": f"{response}"},
            {"role": "user", "content": feedback},
        ]

        return Prompt(messages=new_messages, tools=[])

    def parse_response(self, response: str) -> dict:
        """Parse LLM response into structured format by extracting the ```json block"""
        try:
            start_marker = "```action"
            end_marker = "```"

            # Extract the json block from the response looking for the first ```json and then the ``` starting from the end
            stripped_response = response.strip()
            start_index = stripped_response.find(start_marker)
            end_index = stripped_response.rfind(end_marker)
            stripped_response = stripped_response[
                start_index + len(start_marker) : end_index
            ].strip()
            return json.loads(stripped_response)
        except Exception as e:
            print(f"Agent language failed to parse response: {str(e)}")
            raise e


class AgentFunctionCallingActionLanguage(AgentLanguage):

    def __init__(self, allow_non_tool_output=True):
        super().__init__()
        self.allow_non_tool_output = allow_non_tool_output

    def format_goals(self, goals: List[Goal]) -> List:
        # Map all goals to a single string that concatenates their description
        # and combine into a single message of type system
        sep = "\n-------------------\n"
        goal_instructions = "\n\n".join(
            [f"{goal.name}:{sep}{goal.description}{sep}" for goal in goals]
        )
        return [{"role": "system", "content": goal_instructions}]

    def format_memory(self, memory: Memory) -> List:
        """Generate response from language model"""
        # Map all environment results to a role:user messages
        # Map all assistant messages to a role:assistant messages
        # Map all user messages to a role:user messages
        items = memory.get_memories()
        mapped_items = to_json_memory_messages_format(items)

        return mapped_items

    def format_actions(self, actions: List[Action]) -> List[List[Any]]:
        """Generate response from language model"""

        tools = [
            {
                "type": "function",
                "function": {
                    "name": action.name,
                    # Include up to 1024 characters of the description
                    "description": action.description[:1024],
                    "parameters": action.parameters,
                },
            }
            for action in actions
        ]

        return tools

    def construct_prompt(
        self,
        actions: List[Action],
        environment: Environment,
        goals: List[Goal],
        memory: Memory,
    ) -> Prompt:

        prompt = []
        prompt += self.format_goals(goals)
        prompt += self.format_memory(memory)

        tools = self.format_actions(actions)

        return Prompt(messages=prompt, tools=tools)

    def adapt_prompt_after_parsing_error(
        self,
        prompt: Prompt,
        response: str,
        traceback: str,
        error: Any,
        retries_left: int,
    ) -> Prompt:

        new_messages = prompt.messages + [
            {"role": "assistant", "content": f"{response}"},
            {
                "role": "system",
                "content": "CRITICAL!!! You must ALWAYS choose a tool to use. ",
            },
            {
                "role": "user",
                "content": "You did not call a valid tool. "
                "Please choose an available tool and output a tool call.",
            },
        ]

        return Prompt(messages=new_messages, tools=prompt.tools)

    def parse_response(self, response: str) -> dict:
        """Parse LLM response into structured format by extracting the ```json block"""

        try:
            return json.loads(response)

        except Exception as e:

            if self.allow_non_tool_output:
                # if the agent dumps out a string, it is almost always because it just wants to tell
                # the user something. In this case, we will just return the string as the message
                # to terminate.
                return {"tool": "terminate", "args": {"message": response}}
            else:
                print(f"Agent language failed to parse response: {response}")
                # Added Exit logic
                if isinstance(response, str):
                    if "EXIT_AGENT_LOOP" in response:
                        print("Agent loop terminated early")
                        return {
                            "tool": "terminate",
                            "args": {
                                "message": response.replace(
                                    "EXIT_AGENT_LOOP", ""
                                ).strip()
                            },
                            "error": "Agent Loop Terminated Early",
                        }
                raise ValueError(
                    f"The agent did not respond with a valid tool invocation: {str(e)}"
                )
