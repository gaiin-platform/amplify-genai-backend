import json
import time
import traceback
import uuid
from dataclasses import dataclass
from functools import reduce
from typing import List, Callable, Dict, Any
from agent.prompt import Prompt
from service.requestState import request_killed


class UnknownActionError(Exception):
    pass


class Memory:
    def __init__(self):
        self.items = []  # Basic conversation histor

    def add_memory(self, memory: dict):
        """Add memory to working memory"""
        self.items.append(memory)

    def get_memories(self, limit: int = None) -> List[Dict]:
        """Get formatted conversation history for prompt"""
        return self.items[:limit]

    def copy_without_system_memories(self):
        """Return a copy of the memory without system memories"""
        filtered_items = [m for m in self.items if m["type"] != "system"]
        memory = Memory()
        memory.items = filtered_items
        return memory


@dataclass(frozen=True)
class Goal:
    name: str
    description: str


class Action:
    def __init__(
        self,
        name: str,
        function: callable,
        description: str,
        parameters: Dict,
        output: Dict,
        side_effects: Dict = {},
        terminal: bool = False,
        metadata: Dict = None,
    ):
        self.name = name
        self.function = function
        self.description = description
        self.terminal = terminal
        self.parameters = parameters
        self.output = output
        self.side_effects = side_effects
        self.metadata = metadata or {}

    def execute(self, **args) -> Any:
        """Execute the action's function"""
        return self.function(**args)

    def todict(self):
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "terminal": self.terminal,
        }


class ActionRegistry:
    def __init__(self):
        self.actions = {}

    def register(self, action: Action):
        self.actions[action.name] = action

    def get_action(self, name: str) -> Action | None:
        return self.actions.get(name, None)

    def get_actions(self) -> List[Action]:
        """Get all action descriptions for prompt"""
        return [action for action in self.actions.values()]


class ActionContext:
    def __init__(self, properties: Dict = None):
        self.context_id = str(uuid.uuid4())
        self.properties = properties or {}

    def enable_code_exec_tool_calls(self, action_registry):
        self.properties["code_exec_context"] = {
            **{k: v.function for k, v in action_registry.actions.items()}
        }

    def get(self, key: str, default=None):
        return self.properties.get(key, default)

    def set(self, key: str, value: Any):
        self.properties[key] = value

    def get_environment(self):
        return self.properties.get("environment", None)

    def get_action_registry(self):
        return self.properties.get("action_registry", None)

    def get_agent_registry(self):
        return self.properties.get("agent_registry", None)

    def get_memory(self):
        return self.properties.get("memory", None)

    def send_event(self, event_id: str, event: Dict):
        hdlr = self.properties.get("event_handler", None)
        if not isinstance(event, dict):
            event = {"content": event}
        if hdlr and callable(hdlr) and event:
            event["context_id"] = self.context_id
            hdlr(event_id, event)

    def incremental_event(self, event=None) -> callable:
        base_props = event or {}
        # Create a correlation ID for the event with a uuid
        correlation_id = str(uuid.uuid4())

        def handler(event_id: str, event: Dict) -> str:
            new_event = event or {}
            self.send_event(
                event_id, {**base_props, **new_event, "correlation_id": correlation_id}
            )

        return handler


class Environment:
    def __init__(self):
        pass

    def execute_action(
        self, agent, action_context: ActionContext, action: Action, args: dict
    ) -> dict:
        return {}


class AgentLanguage:
    def __init__(self):
        pass

    def construct_prompt(
        self,
        actions: List[Action],
        environment: Environment,
        goals: List[Goal],
        memory: Memory,
    ) -> Prompt:
        """
        Construct the prompt to send to the language model.

        :param actions:
        :param environment:
        :param goals:
        :param memory:
        :return:
        """
        raise NotImplementedError("Subclasses must implement this method")

    def adapt_prompt_after_parsing_error(
        self,
        prompt: Prompt,
        response: str,
        traceback: str,
        error: Any,
        retries_left: int,
    ) -> Prompt:
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


class Capability:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def init(self, agent, action_context: ActionContext) -> dict:
        pass

    def start_agent_loop(self, agent, action_context: ActionContext) -> bool:
        return True

    def end_agent_loop(self, agent, action_context: ActionContext):
        pass

    def process_action(
        self, agent, action_context: ActionContext, action_def: Action, action: dict
    ) -> dict:
        return action

    def process_response(
        self, agent, action_context: ActionContext, response: str
    ) -> str:
        return response

    # process_result(self, action_context, response, action_def, action, r)
    def process_result(
        self,
        agent,
        action_context: ActionContext,
        response: str,
        action_def: Action,
        action: dict,
        result: any,
    ) -> any:
        return result

    def process_new_memories(
        self,
        agent,
        action_context: ActionContext,
        memory: Memory,
        response,
        result,
        memories: List[dict],
    ) -> List[dict]:
        return memories

    def process_prompt(
        self, agent, action_context: ActionContext, prompt: Prompt
    ) -> Prompt:
        return prompt

    def should_terminate(
        self, agent, action_context: ActionContext, response: str
    ) -> bool:
        return False

    def terminate(self, agent, action_context: ActionContext) -> dict:
        pass


class Agent:
    def __init__(
        self,
        goals: List[Goal],
        agent_language: AgentLanguage,
        action_registry: ActionRegistry,
        generate_response: Callable[[Prompt], str],
        environment: Environment,
        capabilities: List[Capability] = [],
        max_iterations: int = 10,
        max_duration_seconds: int = 180,
    ):
        """
        Initialize an agent with its core GAME components
        """
        self.goals = goals
        self.generate_response = generate_response
        self.agent_language = agent_language
        self.actions = action_registry
        self.environment = environment
        self.capabilities = capabilities or []
        self.max_iterations = max_iterations
        self.max_duration_seconds = max_duration_seconds

    def construct_prompt(
        self, action_context: ActionContext, goals: List[Goal], memory: Memory
    ) -> Prompt:
        """Build prompt with memory context"""
        # context = self.memory.get_relevant_context(self.current_goal)

        prompt = self.agent_language.construct_prompt(
            actions=self.actions.get_actions(),
            environment=self.environment,
            goals=goals,
            memory=memory,
        )

        prompt = reduce(
            lambda p, c: c.process_prompt(self, action_context, p),
            self.capabilities,
            prompt,
        )
        return prompt

    def get_action(self, response):
        action = self.agent_language.parse_response(response)
        action_name = action["tool"]
        action_def = self.actions.get_action(action_name)

        if not action_def:
            raise UnknownActionError(
                f"The specified tool '{action_name}' does not exist. Try something else."
            )

        return action_def, action

    def handle_agent_response(
        self, action_context: ActionContext, response: str
    ) -> dict:
        """Handle action with memory updates"""
        action_def, action = self.get_action(response)

        action_context.send_event(
            "agent/execute_action", {"action": action, "action_def": action_def}
        )

        action = reduce(
            lambda a, c: c.process_action(self, action_context, action_def, a),
            self.capabilities,
            action,
        )

        result = self.environment.execute_action(
            self, action_context, action_def, action["args"]
        )

        result = reduce(
            lambda r, c: c.process_result(
                self, action_context, response, action_def, action, r
            ),
            self.capabilities,
            result,
        )

        if isinstance(action, dict) and action.get("error", None):
            error = f"{result.get('error', '')} {action['error']}"
            result["error"] = error
        return result

    def should_terminate(self, action_context: ActionContext, response: str) -> bool:
        request_id = action_context.get("request_id")
        if request_id and request_killed(
            action_context.get("current_user"), request_id
        ):
            print(f"Request {request_id} killed, terminating agent loop")
            return True
        elif not request_id:
            print(f"Request {request_id} not provided, continuing...")

        action_def, action = self.get_action(response)
        capability_decision = reduce(
            lambda a, c: c.should_terminate(self, action_context, response),
            self.capabilities,
            False,
        )
        return action_def.terminal or capability_decision

    def set_current_task(
        self, action_context: ActionContext, memory: Memory, task: str
    ):
        action_context.send_event("agent/set_task", {"task": task})
        memory.add_memory({"type": "user", "content": task})

    def update_memory(
        self, action_context: ActionContext, memory: Memory, response, result
    ):
        """
        Update memory with new information about what the agent decided to do and how the
        environment responded to the action. This will likely be the output of the action, may include
        side effects, and may include other information about state changes in the environment.

        :param action_context:
        :param memory:
        :param response:
        :param result:
        :return:
        """

        send_event = action_context.incremental_event()

        send_event("agent/memory/update", {"response": response, "result": result})

        result_summary_length_limit = 1000000
        result_summary = json.dumps(result)
        # Check if the result summary is longer than the limit
        if len(result_summary) > result_summary_length_limit and not result.get(
            "complete_result", False
        ):
            result = {
                **result,
                f"result": result_summary[:result_summary_length_limit],
                "complete_result": False,
                "notes": f"The full result was too long to include in the response. "
                f"This is an excerpt of {result_summary_length_limit} chars. The full result is available by reference.",
            }
            result_summary = json.dumps(result)

        new_memories = [
            {"type": "assistant", "content": response},
            {"type": "environment", "content": result_summary},
        ]

        new_memories = reduce(
            lambda nm, c: c.process_new_memories(
                self, action_context, memory, response, result, nm
            ),
            self.capabilities,
            new_memories,
        )

        for m in new_memories:
            memory.add_memory(m)

        send_event("agent/memory/new_memories", {"memories": new_memories})

        return result

    def prompt_llm_for_action(
        self, action_context: ActionContext, full_prompt: Prompt
    ) -> str | None:
        # Try up to 3 times
        send_event = action_context.incremental_event()
        response = None

        max_tries = 3
        for i in range(max_tries):
            try:

                send_event("agent/prompt/action/get", {"prompt": full_prompt})

                response = self.generate_response(full_prompt)

                send_event("agent/prompt/action/raw_result", {"response": response})

                response = reduce(
                    lambda r, c: c.process_response(self, action_context, r),
                    self.capabilities,
                    response,
                )

                # Parse into action
                action_def, action = self.get_action(response)
                send_event(
                    "agent/prompt/action/result",
                    {"action": action, "action_def": action_def},
                )

                if action_def and action:
                    return response

                print("No action_def or action found in response, returning None")
                print("action_def: ", action_def)
                print("action: ", action)

            except Exception as e:
                traceback_str = traceback.format_exc()
                send_event(
                    "agent/prompt/action/error",
                    {
                        "error": str(e),
                        "traceback": traceback_str,
                        "will_retry": i < max_tries - 1,
                    },
                )
                full_prompt = self.agent_language.adapt_prompt_after_parsing_error(
                    full_prompt, response, traceback_str, e, (max_tries - i - 1)
                )

        return None

    def run(self, user_input: str, memory=None, action_context_props=None) -> Memory:
        """
        Execute the GAME loop for this agent
        """

        memory = memory or Memory()

        user_action_context_props = action_context_props or {}

        action_context = ActionContext(
            {
                "environment": self.environment,
                "action_registry": self.actions,
                "memory": memory,
                "llm": self.generate_response,
                **user_action_context_props,
            }
        )

        # Record the initial task
        self.set_current_task(action_context, memory, user_input)

        iterations = 0
        start_time = time.time()

        # Call init on all capabilities
        for capability in self.capabilities:
            capability.init(self, action_context)

        # ========================
        # The Agent Loop
        # ========================
        while True:
            iterations = iterations + 1

            if iterations > self.max_iterations:
                self.update_memory(
                    action_context, memory, "Agent stopped. Max iterations reached.", {}
                )
                break

            can_start_loop = reduce(
                lambda a, c: c.start_agent_loop(self, action_context),
                self.capabilities,
                len(self.capabilities) == 0,
            )
            if not can_start_loop:
                break

            if time.time() - start_time > self.max_duration_seconds:
                self.update_memory(
                    action_context, memory, "Agent stopped. Max duration reached.", {}
                )
                break

            # 1. Construct the prompt for the LLM to generate a response
            prompt = self.construct_prompt(action_context, self.goals, memory)
            memory.add_memory(
                {
                    "type": "prompt",
                    "content": {
                        "messages": prompt.messages,
                        "tools": prompt.tools,
                        "metadata": prompt.metadata,
                    },
                }
            )

            # 2. Prompt the agent for its next action
            response = self.prompt_llm_for_action(action_context, prompt)

            # 3. Handle the agent's response and execute the action (if any)
            result = self.handle_agent_response(
                action_context=action_context, response=response
            )

            # 4. Update memory with knowledge of what the agent did and how the environment responded
            self.update_memory(action_context, memory, response, result)

            # 5. Decide if the loop should continue of if the agent should terminate
            terminate_loop = self.should_terminate(action_context, response)

            for capability in self.capabilities:
                capability.end_agent_loop(self, action_context)

            if terminate_loop:
                break

        for capability in self.capabilities:
            capability.terminate(self, action_context)

        return memory
