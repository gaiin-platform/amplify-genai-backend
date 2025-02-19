import json
from copy import deepcopy
from importlib.metadata import metadata
from typing import List, Optional, Dict, Any
from agent.capabilities.workflow_model import Workflow, Step  # Ensure these imports are correct based on your project structure

from agent.components.tool import register_tool
from agent.core import Capability, ActionContext, Memory, ActionRegistry, Action
from agent.prompt import Prompt
from agent.tools.prompt_tools import prompt_llm_with_messages


def update_schema_descriptions(schema: dict, args: dict) -> dict:
    properties = schema.get("properties", {})

    for param_name, param_value in args.items():
        if param_name in properties:
            properties[param_name]['description'] = param_value

    return schema


class ParameterizedActionRegistry(ActionRegistry):
    def __init__(self, wrapped_registry: ActionRegistry, keep_unparameterized: bool = False):
        super().__init__()
        self.wrapped_registry = wrapped_registry
        self.parameterized_args = []
        self.keep_unparameterized = keep_unparameterized

    def parameterize_actions(self, param_args: List[Dict[str, Any]]):
        self.parameterized_args = param_args

    def _update_action_parameters(self, action: Action, args: Dict[str, Any], instructions: Optional[str] = None, metadata=None) -> Action:
        updated_parameters = deepcopy(action.parameters)

        for param_name, param_value in args.items():
            if param_name in updated_parameters.get("properties", {}):
                updated_parameters["properties"][param_name]['description'] = param_value

        return Action(
            name=action.name,
            function=action.function,
            description=instructions if instructions else action.description,
            parameters=updated_parameters,
            output=action.output,
            side_effects=action.side_effects,
            terminal=action.terminal,
            metadata=metadata
        )

    def get_action(self, name: str) -> Optional[Action]:
        original_action = self.wrapped_registry.get_action(name)
        if not original_action:
            return None

        for param_dict in self.parameterized_args:
            if param_dict.get("tool") == name:
                args = param_dict.get("args", {})
                instructions = param_dict.get("instructions")
                metadata = param_dict
                return self._update_action_parameters(original_action, args, instructions, metadata)

        return original_action

    def get_actions(self) -> List[Action]:
        actions = []
        for name, original_action in self.wrapped_registry.actions.items():
            updated_action = None
            for param_dict in self.parameterized_args:
                if param_dict.get("tool") == name:
                    args = param_dict.get("args", {})
                    instructions = param_dict.get("instructions")
                    updated_action = self._update_action_parameters(original_action, args, instructions)
                    break

            if updated_action:
                actions.append(updated_action)
            elif self.keep_unparameterized:
                actions.append(original_action)

        return actions


@register_tool(tags=["workflow"])
def choose_route(action_context, options: List[str], prompt: str):
    """
    Choose from a list of options.

    :param options: List of options
    :param prompt: Prompt to display
    :return: Chosen option
    """
    return prompt_llm_with_messages(action_context=action_context, prompt=[
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(options)}
    ])


class WorkflowCapability(Capability):
    def __init__(self, workflow: Workflow):
        super().__init__(
            name="Workflow Capability",
            description="The Agent will follow a predefined set of actions"
        )

        self.action_registry = None
        self.workflow = workflow
        self.remaining_steps = list(reversed(self.workflow.steps))

    def init(self, agent, action_context: ActionContext) -> dict:
        self.action_registry = ParameterizedActionRegistry(agent.actions)
        agent.actions = self.action_registry
        return {}

    def start_agent_loop(self, agent, action_context: ActionContext) -> bool:
        if self.remaining_steps:
            next_step: Optional[Step] = self.remaining_steps.pop()
            if next_step:
                self.action_registry.parameterize_actions([self._convert_step_to_action(next_step)])
        return True

    def end_agent_loop(self, agent, action_context: ActionContext):
        pass

    def process_action(self, agent, action_context: ActionContext, action_def: Action, action: dict) -> dict:
        if action_def.metadata and action_def.metadata.get("values", None):
            values = action_def.metadata.get("values")
            for key, value in values.items():
                args = action.get("args", {})
                args[key] = value


        return action

    def process_response(self, agent, action_context: ActionContext, response: str) -> str:
        return response

    def process_result(self, agent, action_context: ActionContext, response: str,  action_def: Action, action: dict, result: any) -> any:
        is_error = isinstance(result, dict) and "error" in result

        return result

    def process_new_memories(self, agent, action_context: ActionContext, memory: Memory, response: str, result: Any, memories: List[dict]) -> List[dict]:
        if self.remaining_steps and self.remaining_steps[-1].tool != "think":
            memories = memories + [{
                "type": "user",
                "content": f"Next, you will need to complete this step:\n{self._format_step(self.remaining_steps[-1])}"
            }]
        return memories

    def process_prompt(self, agent, action_context: ActionContext, prompt: Prompt) -> Prompt:
        return prompt

    def should_terminate(self, agent, action_context: ActionContext, response: str) -> bool:
        return not bool(self.remaining_steps)

    def terminate(self, agent, action_context: ActionContext) -> dict:
        return {}

    def _convert_step_to_action(self, step: Step) -> dict:
        return {
            "tool": step.tool,
            "args": step.args,
            "instructions": step.instructions,
            "values": step.values,
        }

    def _format_step(self, step: Step) -> str:
        return f"{step.instructions}\nTool: {step.tool}\nArgs: {step.args}"
