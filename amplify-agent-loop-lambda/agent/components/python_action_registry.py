from typing import List
import json
import traceback

from agent.components import tool
from agent.core import Action, ActionRegistry
from agent.prompt import Prompt


class PythonActionRegistry(ActionRegistry):
    def __init__(self, tags: List[str] = [], tool_names: List[str] = []):
        super().__init__()

        self.terminate_tool = None

        for tool_name, tool_desc in tool.tools.items():
            if tool_name == "terminate":
                self.terminate_tool = tool_desc

            tool_tags = tool_desc.get("tags", [])
            if tool_name not in tool_names and not any(
                tag in tool_tags for tag in tags
            ):
                continue

            print(
                f"-- Action Registry: Registering Initial Built-In Tool -- {tool_name}"
            )

            self.register(
                Action(
                    name=tool_name,
                    function=tool_desc["function"],
                    description=tool_desc["description"],
                    parameters=tool_desc.get("parameters", {}),
                    output=tool_desc.get("output", {}),
                    terminal=tool_desc.get("terminal", False),
                )
            )

    def register_terminate_tool(self):
        if self.terminate_tool:
            self.register(
                Action(
                    name="terminate",
                    function=self.terminate_tool["function"],
                    description=self.terminate_tool["description"],
                    parameters=self.terminate_tool.get("parameters", {}),
                    output=self.terminate_tool.get("output", {}),
                    terminal=self.terminate_tool.get("terminal", False),
                )
            )
        else:
            raise Exception("Terminate tool not found in tool registry")

    def register_bound_tool_by_name(self, operation):
        print("Registering bound builtIn tool: ", operation)
        # {'name': 'get_current_directory', 'operation': {'name':
        tool_name = operation.get("name", None)
        return self.register_tool_by_name(tool_name)

    def register_tool_by_name(self, tool_name):
        if tool_name in tool.tools:
            tool_desc = tool.tools[tool_name]
            print(
                f"-- Action Registry: Registering Built-In Tool by Name -- {tool_name}"
            )
            self.register(
                Action(
                    name=tool_name,
                    function=tool_desc["function"],
                    description=tool_desc["description"],
                    parameters=tool_desc.get("parameters", {}),
                    output=tool_desc.get("output", {}),
                    terminal=tool_desc.get("terminal", False),
                )
            )
            return True
        else:
            print(f"Tool '{tool_name}' not found in tool registry")
            return False

    def filter_tools_by_relevance(self, llm, user_input, goals=None, max_tools=10):
        """
        Filter the action registry to only include relevant tools based on context and LLM relevance scoring.

        Args:
            llm: The language model function to use for determining tool relevance
            user_input: The user's message or conversation history
            goals: List of goals for the conversation (optional)
            max_tools: Maximum number of tools to keep (default: 10)

        Returns:
            A new filtered action registry with only the most relevant tools
        """
        print("Filtering registry tools by relevance")

        # Check if we have no tools or only the terminate tool
        if len(self.actions) == 0:
            print("No tools to filter - registry is empty")
            if self.terminate_tool:
                print("Adding terminate tool to empty registry")
                self.register_terminate_tool()
            return self
        elif len(self.actions) == 1 and "terminate" in self.actions:
            print("Only terminate tool present - no filtering needed")
            return self

        # Define marker for tool response format
        marker = "/RELEVANT_TOOLS"
        marker_start = f"{marker}_START"
        marker_end = f"{marker}_END"

        # Always keep the terminate tool
        terminate_tool = self.actions.get("terminate", None)

        # Extract conversation context from user_input
        conversation_text = ""
        if isinstance(user_input, list):
            # Handle chat format
            conversation_parts = []
            for message in user_input:
                if (
                    isinstance(message, dict)
                    and "role" in message
                    and "content" in message
                ):
                    role = message.get("role", "unknown")
                    if role in ["system", "user"]:
                        content = message["content"]
                        conversation_parts.append(f"{role}: {content}")
            conversation_text = "\n\n".join(conversation_parts)
        else:
            # Handle string format
            conversation_text = str(user_input)

        # Format goals into text
        goals_text = ""
        if goals:
            goal_descriptions = []
            for goal in goals:
                if hasattr(goal, "name") and hasattr(goal, "description"):
                    goal_descriptions.append(
                        f"Goal: {goal.name}\nDescription: {goal.description}"
                    )
            goals_text = "\n".join(goal_descriptions)

        # Format available tools into a structured format
        available_tools = []

        for tool_name, action in self.actions.items():
            if tool_name == "terminate":  # Skip terminate for analysis
                continue

            # Format parameters for better readability
            params_text = "No parameters"
            if action.parameters:
                param_parts = []
                for param_name, param_details in action.parameters.items():
                    # Handle case where param_details is a string instead of a dict
                    if isinstance(param_details, dict):
                        param_type = param_details.get("type", "unknown")
                        param_desc = param_details.get("description", "No description")
                    else:
                        # If param_details is a string, use it as the description and set type to unknown
                        param_type = "unknown"
                        param_desc = str(param_details)

                    param_parts.append(f"- {param_name} ({param_type}): {param_desc}")
                params_text = "\n".join(param_parts)

            tool_info = {
                "name": tool_name,
                "description": action.description,
                "parameters": params_text,
            }
            available_tools.append(tool_info)

        # Create the prompt for the LLM
        system_message = f"""
You are an expert AI tool selector. Your task is to analyze user conversations and goals, then identify which tools would be most relevant and useful for addressing the user's needs.

INSTRUCTIONS:
1. Carefully analyze the user's conversation and goals (prioritize the most recent messages as they are most relevant)
2. User message carries a much heavier weight of importance (60%) than System messages or Goals (20% each).
3. For each available tool, assign a relevance score from 0-10 based on the criteria below
4. Select ONLY the most relevant tools that score 6 or higher (maximum {max_tools} tools total)
5. Format your response EXACTLY as specified in the FORMAT section below

TOOL SELECTION CRITERIA (Score each criterion from 0-10):
- Direct Need Satisfaction: How directly does the tool address an explicit need expressed by the user?
- Goal Alignment: How well does the tool's functionality align with the user's stated goals?
- Problem Solving: Would the tool provide specific capabilities needed to solve the user's problem?
- Domain Relevance: Is the tool specific to the domain or task the user is working on?
- Complementary Value: Would the tool work well with other highly relevant tools to address the user's needs?

SCORING METHOD:
1. For each tool, score it on each of the 5 criteria (0-10)
2. Calculate the overall relevance score as the average of these 5 scores
3. Select tools with an average score >= 6
4. If more than {max_tools} tools score >= 6, select only the top {max_tools} tools

FORMAT YOUR RESPONSE EXACTLY LIKE THIS:
{marker_start}
["tool1", "tool2", "tool3"]
{marker_end}

IMPORTANT: 
- Your response MUST start with {marker_start} and end with {marker_end}
- Between these delimiters must be ONLY a valid JSON array of tool names
- Do NOT include any explanations, scores, or other text outside the delimiters
- Do NOT include any text within the delimiters except the JSON array
"""

        user_message = f"""
USER CONVERSATION (user role messages are most important!):
{conversation_text}

USER GOALS:
{goals_text}

AVAILABLE TOOLS:
{json.dumps(available_tools, indent=2)}

Based on the user's conversation and goals, evaluate each tool using the scoring criteria and select only the most relevant tools (maximum {max_tools}).
Remember to format your response exactly as specified, with only a JSON array of tool names between the {marker_start} and {marker_end} delimiters.
"""

        # Create the prompt
        prompt = Prompt(
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ]
        )

        try:
            # Call the LLM
            response = llm(prompt)
            # print(f"LLM tool selection response raw: {response}")

            # Extract the JSON array using the delimiters
            selected_tools = []

            # Try exact delimiters first
            if marker_start in response and marker_end in response:
                start_index = response.find(marker_start) + len(marker_start)
                end_index = response.find(marker_end)
                json_str = response[start_index:end_index].strip()

                try:
                    selected_tools = json.loads(json_str)
                except json.JSONDecodeError:
                    print(f"Failed to parse JSON between delimiters: '{json_str}'")

            # Validate the response format
            if not isinstance(selected_tools, list):
                print("Invalid response format: not a list")
                return self

            # Filter to only include valid tool names
            valid_tool_names = set(self.actions.keys())
            selected_tools = [
                tool for tool in selected_tools if tool in valid_tool_names
            ]

            # Add terminate tool if it exists
            if terminate_tool and "terminate" not in selected_tools:
                selected_tools.append("terminate")

            # Create new filtered actions dict
            filtered_actions = {}
            for tool_name in selected_tools:
                filtered_actions[tool_name] = self.actions[tool_name]

            print(f"Filtered tools from {len(self.actions)} to {len(filtered_actions)}")
            # print(f"Selected tools: {', '.join(filtered_actions.keys())}")

            # Update the actions dictionary
            self.actions = filtered_actions

        except Exception as e:
            print(f"Error during tool filtering: {str(e)}")
            traceback.print_exc()
            # If there's an error, keep all tools

        return self
