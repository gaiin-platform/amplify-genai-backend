import json
from typing import List, Dict
from agent.core import AgentPrompt, AgentResponse, Memory, ActionResult, ContinuationDecision, Action, \
    Capability
from agent.util import extract_markdown_block


class ToolUseCapability:
    """Capability for formatting prompts and handling tool execution."""

    def __init__(self, action_registry, stop_on_no_action=True):
        """
        Initialize with an action registry that provides available tools.
        :param action_registry: Registry of available tools.
        """
        self.stop_on_no_action = stop_on_no_action
        self.action_registry = action_registry

    def enhance_prompt(self, prompt: AgentPrompt) -> AgentPrompt:
        """
        Add tool usage information to the prompt.
        :param prompt: Original agent prompt.
        :return: Enhanced prompt with tool usage instructions.
        """
        # Add action descriptions to the prompt
        action_descriptions = [
            {
                "name": action.name,
                "description": action.description,
                "args": action.args
            }
            for action in self.action_registry.get_actions()
        ]

        # Include instructions for tool usage
        tool_instructions = f"""
            Available Tools:
            {json.dumps(action_descriptions, indent=4)}
            
            Use the following format for tool usage:
            
            <Stop and think step by step. Insert a rich description of your step by step thoughts here.>
            
            ```action
            {{
                "tool": "tool_name",
                "args": {{...}}
            }}
            ```
            Terminate the process using the “terminate” tool if no further actions are needed.
            """

        # Append instructions to the existing prompt
        return prompt.add_message(
            role="system",
            content=tool_instructions,
            tag="tool_use"
        )

    def process_response(self, response: AgentResponse) -> AgentResponse:
        """
        Parse the LLM's response into actionable JSON if an ```action block is present
        and parsed_response is not already set.
        :param response: Raw response from the LLM.
        :return: New AgentResponse with parsed response if action block exists and parsed_response is None.
        """
        # Return the original response if parsed_response is already set
        if response.parsed_response is not None:
            return response

        action = extract_markdown_block(response.raw_response, "action")
        if action:
            try:
                # If an action block is found, parse the JSON content
                action_json = json.loads(action)
                action_json['tag'] = "tool_use"
                parsed_response = Action(**action_json)
            except json.JSONDecodeError as e:
                # If there is an exception parsing, create a response to let the agent know
                parsed_response = Action(tool="error",
                                         args={"message": f"Failed to parse the ```action block because it contained invalid JSON: {str(e)}"}
                                         )
        else:
            # If no action block is found, leave parsed_response as None
            parsed_response = None

        # Return a new AgentResponse instance with updated parsed_response
        return AgentResponse(
            raw_response=response.raw_response,
            parsed_response=parsed_response,
            metadata=response.metadata
        )

    def should_continue(self, state, result: ActionResult, new_memories: List[Memory]) -> ContinuationDecision:
        """
        Decide whether the agent should continue.
        :param state: Current state of the agent.
        :param result: Result of the last tool execution.
        :param new_memories: Updated memories.
        :return: Continuation decision.
        """
        if  (not result.action or result.action.tool == "no_action") and self.stop_on_no_action:
            return ContinuationDecision(
                should_continue=not self.stop_on_no_action,
                reason="No action to execute"
            )
        elif result.action and result.action.tool == "terminate":
            return ContinuationDecision(
                should_continue=False,
                reason="Terminate tool invoked",
                metadata={}
            )

        return ContinuationDecision(
            should_continue=True,
            reason="Continue as normal"
        )