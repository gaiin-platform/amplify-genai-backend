import json
import re
from typing import List, Any

from agent.game.action import Action
from agent.game.environment import Environment
from agent.game.goal import Goal
from agent.game.memory import Memory




def ai_friendly_json_loads(input_str: str) -> dict:
    """
    Process a string that might contain JSON with triple-quoted blocks.
    First attempts direct JSON parsing, then handles triple-quoted blocks if needed.

    Sample Usage:

      test_str = "{\n    "tool": "exec_code",\n    "args": {\n        "code": \"\"\"\nimport subprocess\nimport json\n\n# List of common Linux commands to execute\ncommands = [\'ls\', \'pwd\', \'whoami\']\nresults = []\n\nfor command in commands:\n    try:\n        # Execute the command\n        output = subprocess.check_output(command, shell=True, text=True).strip()\n        results.append({\'command\': command, \'output\': output, \'error\': None})\n    except subprocess.CalledProcessError as e:\n        # Capture the error if the command fails\n        results.append({\'command\': command, \'output\': None, \'error\': e.output.strip()})\n    except Exception as e:\n        # General exception handling\n        results.append({\'command\': command, \'output\': None, \'error\': str(e)})\n\n# Write results to a JSON file in the working directory\nfile_path = \'/tmp/1/linux_command_results.json\'\nwith open(file_path, \'w\') as file:\n    json.dump(results, file)\n\n# Define the result to return the file path\nresult = {\'file_path\': file_path}\n\"\"\"\n    }}"
      json = ai_friendly_json_loads(test_str)
      print(f"JSON: {json}")

    Args:
        input_str: String that might contain JSON with triple-quoted blocks

    Returns:
        Parsed JSON dictionary

    Raises:
        json.JSONDecodeError: If string cannot be parsed as JSON after processing
    """
    # First try direct JSON parsing
    try:
        return json.loads(input_str)
    except json.JSONDecodeError:
        # Find all triple-quoted blocks
        pattern = r'"""((?:.|\n)*?)"""'

        def escape_block(match):
            """Helper function to escape content within a triple-quoted block."""
            content = match.group(1)
            # Escape newlines and quotes
            escaped = content.replace('\n', '\\n').replace('"', '\\"')
            # Return the content wrapped in regular quotes
            return f'"{escaped}"'

        # Replace all triple-quoted blocks with escaped versions
        processed_str = re.sub(pattern, escape_block, input_str)

        # Try parsing the processed string
        return json.loads(processed_str)



class AgentLanguage:
    def __init__(self):
        pass

    def construct_prompt(self,
                         actions: List[Action],
                         environment: Environment,
                         goals: List[Goal],
                         memory: Memory) -> List[dict]:
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
                                         prompt: List[dict],
                                         response: str,
                                         traceback: str,
                                         error: Any,
                                         retries_left: int) -> List[dict]:
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


class AgentNaturalLanguage:
    def __init__(self):
        pass

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
                         memory: Memory) -> List[dict]:

        prompt = []
        prompt += self.format_goals(goals)
        prompt += self.format_memory(memory)

        return prompt

    def adapt_prompt_after_parsing_error(self,
                                         prompt: List[dict],
                                         response: str,
                                         traceback: str,
                                         error: Any,
                                         retries_left: int) -> List[dict]:
        return prompt


    def parse_response(self, response: str) -> dict:
        """Parse LLM response into structured format by extracting the ```json block"""
        return {
            "tool": "terminate",
            "args": {
                "message": response
            }
        }



class AgentJsonActionLanguage:

    action_format = """
<Stop and think step by step. Insert a rich description of your step by step thoughts here.>

```action
{{
    "tool": "tool_name",
    "args": {{...fill in any required arguments here...}}
}}
```"""

    def __init__(self):
        pass

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
            [{"name": action.name, "description": action.description, "args": action.args} for
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
                         memory: Memory) -> List[dict]:

        prompt = []
        prompt += self.format_goals(goals)
        prompt += self.format_actions(actions)
        prompt += self.format_memory(memory)

        return prompt

    def adapt_prompt_after_parsing_error(self,
                         prompt: List[dict],
                         response: str,
                         traceback: str,
                         error: Any,
                         retries_left: int) -> List[dict]:

        return prompt + [
            {"role": "assistant", "content": f"{response}"},
            {"role": "user", "content": f"Your last output did not contain a valid ```action block that could be parsed. \n"
                                        f"Please fix your prior response. \n"
                                        f"Make sure that it has the correct format: \n"
                                        f"{AgentJsonActionLanguage.action_format}"}
        ]

    def parse_response(self, response: str) -> dict:
        """Parse LLM response into structured format by extracting the ```json block"""
        try:
            if response is None:
                raise ValueError("The agent's response is None.")

            start_marker = "```action"
            end_marker = "```"

            # Extract the json block from the response looking for the first ```json and then the ``` starting from the end
            stripped_response = response.strip()
            start_index = stripped_response.find(start_marker)
            end_index = stripped_response.rfind(end_marker)

            if start_index == -1:
                raise ValueError("The agent's response did not contain a starting ```action block.")
            if end_index == -1:
                raise ValueError("The agent's response did not contain a valid end to the ```action block. Missing terminating ``` after ```action")

            stripped_response = stripped_response[start_index + len(start_marker):end_index].strip()
            return ai_friendly_json_loads(stripped_response)
        except Exception as e:
            print(f"Agent language failed to parse response: {str(e)}")
            raise e