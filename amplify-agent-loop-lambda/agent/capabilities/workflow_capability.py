import json
from copy import deepcopy
from importlib.metadata import metadata
from typing import List, Optional, Dict, Any
from agent.capabilities.workflow_model import (
    Workflow,
    Step,
)  # Ensure these imports are correct based on your project structure

from agent.components.tool import register_tool
from agent.core import Capability, ActionContext, Memory, ActionRegistry, Action
from agent.prompt import Prompt
from agent.tools.prompt_tools import prompt_llm_with_messages


def update_schema_descriptions(schema: dict, args: dict) -> dict:
    properties = schema.get("properties", {})

    for param_name, param_value in args.items():
        if param_name in properties:
            properties[param_name]["description"] = param_value

    return schema


class ParameterizedActionRegistry(ActionRegistry):
    def __init__(
        self, wrapped_registry: ActionRegistry, keep_unparameterized: bool = False
    ):
        super().__init__()
        self.wrapped_registry = wrapped_registry
        self.parameterized_args = []
        self.keep_unparameterized = keep_unparameterized

    def parameterize_actions(self, param_args: List[Dict[str, Any]]):
        self.parameterized_args = param_args

    def _update_action_parameters(
        self,
        action: Action,
        args: Dict[str, Any],
        instructions: Optional[str] = None,
        metadata=None,
    ) -> Action:
        updated_parameters = deepcopy(action.parameters)

        for param_name, param_value in args.items():
            if param_name in updated_parameters.get("properties", {}):
                updated_parameters["properties"][param_name][
                    "description"
                ] = param_value

        return Action(
            name=action.name,
            function=action.function,
            description=instructions if instructions else action.description,
            parameters=updated_parameters,
            output=action.output,
            side_effects=action.side_effects,
            terminal=action.terminal,
            metadata=metadata,
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
                return self._update_action_parameters(
                    original_action, args, instructions, metadata
                )

        return original_action

    def get_actions(self) -> List[Action]:
        actions = []
        for name, original_action in self.wrapped_registry.actions.items():
            updated_action = None
            for param_dict in self.parameterized_args:
                if param_dict.get("tool") == name:
                    args = param_dict.get("args", {})
                    instructions = param_dict.get("instructions")
                    updated_action = self._update_action_parameters(
                        original_action, args, instructions
                    )
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
    return prompt_llm_with_messages(
        action_context=action_context,
        prompt=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(options)},
        ],
    )


class WorkflowCapability(Capability):
    def __init__(self, workflow: Workflow):
        super().__init__(
            name="Workflow Capability",
            description="The Agent will follow a predefined set of actions",
        )

        self.action_registry = None
        self.workflow = workflow
        self.remaining_steps = list(reversed(self.workflow.steps))
        self.current_step = None
        self.retry_count = {}  # Track retries for each step
        self.max_retries = 2  # Maximum number of retries per step
        self.terminate_early = False

    def init(self, agent, action_context: ActionContext) -> dict:
        self.action_registry = ParameterizedActionRegistry(agent.actions)
        agent.actions = self.action_registry
        return {}

    def start_agent_loop(self, agent, action_context: ActionContext) -> bool:
        if self.remaining_steps:
            next_step: Optional[Step] = self.remaining_steps.pop()
            if next_step:
                step_id = self._construct_step_id(next_step)
                step_attempted_before = step_id in self.retry_count
                # skip logic - dont skip failed steps
                should_skip = (
                    False
                    if step_attempted_before
                    else self._should_skip_step(next_step, action_context)
                )
                if should_skip:
                    print(f"-- skipping step in workflow -- {next_step.tool}")
                    # Recursively call start_agent_loop to process the next step
                    return self.start_agent_loop(agent, action_context)

                self.current_step = next_step
                if not step_attempted_before:
                    self.retry_count[step_id] = 0

                self.action_registry.parameterize_actions(
                    [self._convert_step_to_action(next_step)]
                )
            print(
                "-- remaining steps -- ",
                [step.tool for step in self.remaining_steps[::-1]],
            )
        return True

    def end_agent_loop(self, agent, action_context: ActionContext):
        pass

    def process_action(
        self, agent, action_context: ActionContext, action_def: Action, action: dict
    ) -> dict:
        if action_def.metadata and action_def.metadata.get("values", None):
            values = action_def.metadata.get("values")
            for key, value in values.items():
                args = action.get("args", {})
                if isinstance(value, str) and value.lower() in ["true", "false"]:
                    value = value.lower() == "true"

                args[key] = value

        return action

    def process_response(
        self, agent, action_context: ActionContext, response: str
    ) -> str:
        return response

    def process_result(
        self,
        agent,
        action_context: ActionContext,
        response: str,
        action_def: Action,
        action: dict,
        result: any,
    ) -> any:
        if isinstance(action, dict) and action.get("error", None):
            print("Terminating Workflow: ", action.get("error"))
            self.terminate_early = True
            return result
        # Enhanced error detection covering multiple error scenarios
        is_error = (
            isinstance(result, dict)
            and result.get("tool", "") != "terminate"
            and (
                "error" in result
                or (
                    "result" in result
                    and isinstance(result["result"], dict)
                    and (
                        (
                            "success" in result["result"]
                            and not result["result"]["success"]
                        )
                        or "traceback" in result["result"]
                        or (
                            "message" in result["result"]
                            and any(
                                x in result["result"].get("message", "").lower()
                                for x in ["error", "failed", "invalid", "exception"]
                            )
                        )
                    )
                )
            )
        )

        if is_error and self.current_step:
            step_id = self._construct_step_id(self.current_step)
            error_message = "Unknown error"

            if self.retry_count[step_id] < self.max_retries:
                print(
                    f"-- Retrying step {self.current_step.tool} ({self.retry_count[step_id]}/{self.max_retries}) due to error --"
                )
                self.retry_count[step_id] += 1
                self.remaining_steps.append(self.current_step)

                # Log retry information in memory
                memory = action_context.get("memory")
                if memory:

                    if isinstance(result, dict):
                        if "error" in result:
                            error_message = result["error"]
                        elif (
                            "result" in result
                            and isinstance(result["result"], dict)
                            and "message" in result["result"]
                        ):
                            error_message = result["result"]["message"]

                send_event = action_context.incremental_event()
                # Send an event about the retry
                send_event(
                    "workflow/step/retry",
                    {
                        "workflow": self.workflow,
                        "step": self.current_step.tool,
                        "retry_count": self.retry_count[step_id],
                        "max_retries": self.max_retries,
                        "error": error_message,
                    },
                )

        return result

    def process_new_memories(
        self,
        agent,
        action_context: ActionContext,
        memory: Memory,
        response: str,
        result: Any,
        memories: List[dict],
    ) -> List[dict]:
        if (
            self.remaining_steps
            and self.remaining_steps[-1].tool != "think"
            and not self.terminate_early
        ):
            memories = memories + [
                {
                    "type": "user",
                    "content": f"Next, you will need to complete this step:\n{self._format_step(self.remaining_steps[-1])}",
                }
            ]

        if self.current_step.useAdvancedReasoning:
            for m in memories:
                if m.get("type") == "assistant":
                    content = m.get("content")
                    if not isinstance(content, dict):
                        try:
                            content = json.loads(content)
                        except:
                            pass
                    if isinstance(content, dict):
                        content["advanced_reasoning"] = True
                        m["content"] = json.dumps(content)
        return memories

    def process_prompt(
        self, agent, action_context: ActionContext, prompt: Prompt
    ) -> Prompt:
        return prompt

    def should_terminate(
        self, agent, action_context: ActionContext, response: str
    ) -> bool:
        return not bool(self.remaining_steps) or self.terminate_early

    def terminate(self, agent, action_context: ActionContext) -> dict:
        return {}

    def _convert_step_to_action(self, step: Step) -> dict:
        print("-- next step -- ", step.tool)
        return {
            "tool": step.tool,
            "args": step.args,
            "instructions": step.instructions,
            "values": step.values,
        }

    def _format_step(self, step: Step) -> str:
        return f"{step.instructions}\nTool: {step.tool}\nArgs: {step.args}"

    def _should_skip_step(self, step: Step, action_context: ActionContext) -> bool:
        if step.tool in ["terminate", "think"]:
            return False

        memory = action_context.get("memory", None)
        if not memory:
            return False

        memories = memory.get_memories()

        filtered_memories = [
            msg for msg in memories if msg["type"] not in ["system", "prompt"]
        ]

        prompt = f"""You are a workflow step evaluator tasked with determining if step '{step.tool}' can be safely skipped.

INSTRUCTIONS:
1. Carefully analyze the step instructions: '{step.instructions}'
2. Review the conversation history and current context in the provided memories
3. Evaluate whether this step has ALREADY been completed or is COMPLETELY UNNECESSARY based on:
   - Information needed from this step is already available in the conversation history
   - The step's purpose is irrelevant to the current users request
   - The prerequisites for this step are not met and cannot be met
   - Simply the step does not need to be performed
Tips: ALWAYS double check you have all required information needed to perform the step otherwise SKIP the step.

RESPONSE Meaning Clarification:
- YES: Skip this step 
- NO: Do NOT Skip this step

Conversation history and current context:
{json.dumps(filtered_memories)}

Respond with either YES or NO in all caps. Then write a short explanation (1-2 sentences) for your reasoning on the next line."""

        # llm call to determine if the step should be skipped
        sys_prompt = """You're task is to determine if the step {step.tool} should be skipped.  
        Use the following threshold to determine if the step should be skipped:
        CONFIDENCE THRESHOLD:
        - If you have ANY doubt (even 5%) about whether skipping is safe, respond with "NO"
        - Only respond with "YES" if you are 100 confident this step can be safely skipped without affecting the workflow outcome

        YOUR RESPONSE MUST BE EXACTLY ONLY  YES   or   NO  in all caps. Followed by a new line and short explanation explaing your decision"""
        f"Should we skip the step {step.tool}? {step.instructions}"
        response = prompt_llm_with_messages(
            action_context=action_context,
            prompt=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt},
            ],
        )

        if response and "YES" in response and "NO" not in response:
            skip_reason = (
                response.split("\n", 1)[1] if "\n" in response else "No reason provided"
            )

            send_event = action_context.incremental_event()
            # Send event about skipped step
            send_event(
                "workflow/step/skip",
                {"workflow": self.workflow, "step": step.tool, "reason": skip_reason},
            )

            new_memory = {
                "type": "assistant",
                "content": {
                    "tool": step.tool,
                    "skipped": skip_reason,
                },
            }

            memory.add_memory(new_memory)

            send_event("agent/memory/new_memories", {"memories": [new_memory]})
            return True

        return False

    def _construct_step_id(self, step: Step) -> str:
        description_hash = hash(step.description) if step.description else "_"
        args_hash = len(step.args.items()) if step.args else 0
        instructions_hash = hash(step.instructions) if step.instructions else "_"
        action_segment = step.actionSegment if step.actionSegment else "0"
        step_name = step.stepName if step.stepName else "0"

        return f"{step.tool}-{step_name}-{action_segment}_{args_hash}_{instructions_hash}_{description_hash}"
