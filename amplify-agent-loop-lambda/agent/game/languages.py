import json
from typing import List, Any

from agent.prompt import Prompt
from agent.game.action import Action
from agent.game.environment import Environment
from agent.game.goal import Goal
from agent.game.memory import Memory

class AgentLanguage:
    def __init__(self):
        pass

    def construct_prompt(self,
                         actions: List[Action],
                         environment: Environment,
                         goals: List[Goal],
                         memory: Memory) -> Prompt:
        """
        Construct the prompt to send to the language model.

        :param actions:
        :param environment:
        :param goals:
        :param memory:
        :return:
        """
        raise NotImplementedError("Subclasses must implement this method")

    def adapt_prompt_after_parsing_error(self,
                                         prompt: Prompt,
                                         response: str,
                                         traceback: str,
                                         error: Any,
                                         retries_left: int) -> Prompt:
        """
        Adapt the prompt after a parsing error. This method is called when the language model fails to parse the response.
        You can throw custom errors in the parse_response that will be passed to this as the error parameter so that
        you can adapt the prompt based on the error.

        :param prompt:
        :param traceback:
        :param error:
        :param retries_left:
        :return:
        """
        return prompt

    def parse_response(self, response: str) -> dict:
        """
        Parse the response from the language model into a structured format
        :param response:
        :return:
        """
        raise NotImplementedError("Subclasses must implement this method")


class AgentNaturalLanguage(AgentLanguage):
    def __init__(self):
        super().__init__()

    def format_goals(self, goals: List[Goal]) -> List:
        # Map all goals to a single string that concatenates their description
        # and combine into a single message of type system
        goal_instructions = "\n".join([f"{goal.description}" for goal in goals])
        return [
            {"role": "system", "content": goal_instructions}
        ]

    def format_memory(self, memory: Memory) -> List:
        """Generate response from language model"""
        # Map all environment results to a role:user messages
        # Map all assistant messages to a role:assistant messages
        # Map all user messages to a role:user messages
        items = memory.get_memories()
        mapped_items = []
        for item in items:

            content = item.get("content", None)
            if not content:
                content = json.dumps(item, indent=4)

            if item["type"] == "assistant":
                mapped_items.append({"role": "assistant", "content": content})
            elif item["type"] == "environment":
                mapped_items.append({"role": "assistant", "content": content})
            else:
                mapped_items.append({"role": "user", "content": content})

        return mapped_items

    def construct_prompt(self,
                         actions: List[Action],
                         environment: Environment,
                         goals: List[Goal],
                         memory: Memory) -> Prompt:

        prompt = []
        prompt += self.format_goals(goals)
        prompt += self.format_memory(memory)

        return Prompt(messages=prompt, tools=[])

    def adapt_prompt_after_parsing_error(self,
                                         prompt: Prompt,
                                         response: str,
                                         traceback: str,
                                         error: Any,
                                         retries_left: int) -> Prompt:
        return prompt


    def parse_response(self, response: str) -> dict:
        """Parse LLM response into structured format by extracting the ```json block"""
        return {
            "tool": "terminate",
            "args": {
                "message": response
            }
        }



class AgentJsonActionLanguage(AgentLanguage):

    action_format = """
<Stop and think step by step. Insert a rich description of your step by step thoughts here.>

```action
{{
    "tool": "tool_name",
    "args": {{...fill in any required arguments here...}}
}}
```"""

    def __init__(self):
        super().__init__()

    def format_goals(self, goals: List[Goal]) -> List:
        # Map all goals to a single string that concatenates their description
        # and combine into a single message of type system
        sep = "\n-------------------\n"
        goal_instructions = "\n\n".join([f"{goal.name}:{sep}{goal.description}{sep}" for goal in goals])
        return [
            {"role": "system", "content": goal_instructions}
        ]

    def format_memory(self, memory: Memory) -> List:
        """Generate response from language model"""
        # Map all environment results to a role:user messages
        # Map all assistant messages to a role:assistant messages
        # Map all user messages to a role:user messages
        items = memory.get_memories()
        mapped_items = []
        for item in items:

            content = item.get("content", None)
            if not content:
                content = json.dumps(item, indent=4)

            if item["type"] == "assistant":
                mapped_items.append({"role": "assistant", "content": content})
            elif item["type"] == "environment":
                mapped_items.append({"role": "assistant", "content": content})
            else:
                mapped_items.append({"role": "user", "content": content})

        return mapped_items

    def format_actions(self, actions: List[Action]) -> List:
        """Generate response from language model"""

        action_descriptions = \
            [{"name": action.name, "description": action.description, "parameters": action.parameters} for
             action in actions]

        return [
            {"role":"system",
             "content":f"""
Available Tools: {json.dumps(action_descriptions, indent=4)}

When you are done, terminate the conversation by using the "terminate" tool and I will 
provide the results to the user.

Important!!! Every response MUST have an action.
You must ALWAYS respond in this format:

{AgentJsonActionLanguage.action_format}
"""
             }
        ]

    def construct_prompt(self,
                         actions: List[Action],
                         environment: Environment,
                         goals: List[Goal],
                         memory: Memory) -> Prompt:

        prompt = []
        prompt += self.format_goals(goals)
        prompt += self.format_actions(actions)
        prompt += self.format_memory(memory)

        return Prompt(messages=prompt, tools=[])

    def adapt_prompt_after_parsing_error(self,
                         prompt: Prompt,
                         response: str,
                         traceback: str,
                         error: Any,
                         retries_left: int) -> Prompt:

        new_messages = prompt.messages + [
            {"role": "assistant", "content": f"{response}"},
            {"role": "user", "content": f"Your last output did not contain a valid ```action block that could be parsed. \n"
                                        f"Please fix your prior response. \n"
                                        f"Make sure that it has the correct format: \n"
                                        f"{AgentJsonActionLanguage.action_format}"}
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
            stripped_response = stripped_response[start_index + len(start_marker):end_index].strip()
            return json.loads(stripped_response)
        except Exception as e:
            print(f"Agent language failed to parse response: {str(e)}")
            raise e




class AgentFunctionCallingActionLanguage(AgentLanguage):

    def __init__(self):
        super().__init__()

    def format_goals(self, goals: List[Goal]) -> List:
        # Map all goals to a single string that concatenates their description
        # and combine into a single message of type system
        sep = "\n-------------------\n"
        goal_instructions = "\n\n".join([f"{goal.name}:{sep}{goal.description}{sep}" for goal in goals])
        return [
            {"role": "system", "content": goal_instructions}
        ]

    def format_memory(self, memory: Memory) -> List:
        """Generate response from language model"""
        # Map all environment results to a role:user messages
        # Map all assistant messages to a role:assistant messages
        # Map all user messages to a role:user messages
        items = memory.get_memories()
        mapped_items = []
        for item in items:

            content = item.get("content", None)
            if not content:
                content = json.dumps(item, indent=4)

            if item["type"] == "assistant":
                mapped_items.append({"role": "assistant", "content": content})
            elif item["type"] == "environment":
                mapped_items.append({"role": "assistant", "content": content})
            else:
                mapped_items.append({"role": "user", "content": content})

        return mapped_items

    def format_actions(self, actions: List[Action]) -> [List,List]:
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
            } for action in actions
        ]

        return tools

    def construct_prompt(self,
                         actions: List[Action],
                         environment: Environment,
                         goals: List[Goal],
                         memory: Memory) -> Prompt:

        prompt = []
        prompt += self.format_goals(goals)
        prompt += self.format_memory(memory)

        tools = self.format_actions(actions)

        return Prompt(messages=prompt, tools=tools)

    def adapt_prompt_after_parsing_error(self,
                                         prompt: Prompt,
                                         response: str,
                                         traceback: str,
                                         error: Any,
                                         retries_left: int) -> Prompt:

        return prompt

    def parse_response(self, response: str) -> dict:
        """Parse LLM response into structured format by extracting the ```json block"""

        try:
            return json.loads(response)

        except Exception as e:
            # if the agent dumps out a string, it is almost always because it just wants to tell
            # the user something. In this case, we will just return the string as the message
            # to terminate.
            return {
                "tool": "terminate",
                "args": {"message": response}
            }