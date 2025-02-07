import json
import time
import traceback
from functools import reduce
from typing import List, Callable

from agent.game.action import ActionRegistry, ActionContext
from agent.game.environment import Environment
from agent.game.goal import Goal
from agent.game.languages import AgentLanguage
from agent.game.memory import Memory
from agent.prompt import generate_response, Prompt


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

    def process_action(self, agent, action_context: ActionContext, action: dict) -> dict:
        return action

    def process_response(self, agent, action_context: ActionContext, response: str) -> str:
        return response

    def process_new_memories(self, agent, action_context: ActionContext, memory: Memory, response, result, memories: List[dict]) -> List[dict]:
        return memories

    def process_prompt(self, agent, action_context: ActionContext, prompt: Prompt) -> Prompt:
        return prompt

    def should_terminate(self, agent, action_context: ActionContext, response: str) -> bool:
        return False

    def terminate(self, agent, action_context: ActionContext) -> dict:
        pass


class Agent:
    def __init__(self,
                 goals: List[Goal],
                 agent_language: AgentLanguage,
                 action_registry: ActionRegistry,
                 generate_response: Callable[[Prompt], str],
                 environment: Environment,
                 capabilities: List[Capability] = [],
                 max_iterations: int = 10,
                 max_duration_seconds: int = 180):
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


    def construct_prompt(self, action_context: ActionContext, goals:List[Goal], memory: Memory) -> Prompt:
        """Build prompt with memory context"""
        #context = self.memory.get_relevant_context(self.current_goal)

        prompt = self.agent_language.construct_prompt(
            actions=self.actions.get_actions(),
            environment=self.environment,
            goals=goals,
            memory=memory
        )

        prompt = reduce(lambda p, c: c.process_prompt(self, action_context, p), self.capabilities, prompt)
        return prompt

    def get_action(self, response):
        action = self.agent_language.parse_response(response)
        action_def = self.actions.actions[action["tool"]]
        return action_def, action

    def handle_agent_response(self, action_context: ActionContext, response: str) -> dict:
        """Handle action with memory updates"""
        action_def, action = self.get_action(response)

        action_context.send_event("agent/execute_action", {"action": action, "action_def": action_def})

        action = reduce(lambda a, c: c.process_action(self, action_context, a), self.capabilities, action)

        result = self.environment.execute_action(self, action_context, action_def, action["args"])
        return result

    def should_terminate(self, action_context: ActionContext, response: str) -> bool:
        action_def, action = self.get_action(response)
        capability_decision = reduce(lambda a, c: c.should_terminate(self, action_context, response), self.capabilities, False)
        return action_def.terminal or capability_decision

    def set_current_task(self, action_context: ActionContext, memory: Memory, task: str):
        action_context.send_event("agent/set_task", {"task": task})
        memory.add_memory({"type":"user", "content":task})

    def update_memory(self, action_context: ActionContext, memory: Memory, response, result):
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

        result_summary_length_limit = 100000
        result_summary = json.dumps(result)
        # Check if the result summary is longer than the limit
        if len(result_summary) > result_summary_length_limit and not result.get("complete_result", False):
            result = {
                **result,
                f"result": result_summary[:result_summary_length_limit],
                "complete_result": False,
                "notes": "The result was too long to include in the response. "
                         "This is an excerpt. The full result is available by reference."
            }
            result_summary = json.dumps(result)

        new_memories = [
            {"type": "assistant", "content": response},
            {"type": "environment", "content": result_summary}
        ]

        new_memories = reduce(lambda nm, c: c.process_new_memories(self, action_context, memory, response, result, nm),
                              self.capabilities,
                              new_memories)

        for m in new_memories:
            memory.add_memory(m)

        send_event("agent/memory/new_memories", {"memories": new_memories})

        return result


    def prompt_llm_for_action(self, action_context: ActionContext, full_prompt: Prompt) -> [str,None]:
        # Try up to 3 times
        send_event = action_context.incremental_event()
        response = None

        max_tries = 3
        for i in range(max_tries):
            try:

                send_event("agent/prompt/action/get", {"prompt": full_prompt})

                response = self.generate_response(full_prompt)

                send_event("agent/prompt/action/raw_result", {"response": response})

                response = reduce(lambda r, c: c.process_response(self, action_context, r), self.capabilities, response)

                # Parse into action
                action_def, action = self.get_action(response)
                send_event("agent/prompt/action/result", {"action": action, "action_def": action_def})

                if action_def and action:
                    return response
            except Exception as e:
                traceback_str = traceback.format_exc()
                send_event("agent/prompt/action/error",
                                          {"error": str(e), "traceback": traceback_str, "will_retry": i < max_tries - 1})
                full_prompt = self.agent_language.adapt_prompt_after_parsing_error(
                    full_prompt,
                    response,
                    traceback_str,
                    e,
                    (max_tries - i - 1)
                )

        return None


    def run(self, user_input: str, memory=None, action_context_props = None) -> Memory:
        """
        Execute the GAME loop for this agent
        """

        memory = memory or Memory()

        user_action_context_props = action_context_props or {}

        action_context = ActionContext({
            'environment': self.environment,
            'action_registry': self.actions,
            'memory': memory,
            'llm': self.generate_response,
            **user_action_context_props
        })

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

            can_start_loop = reduce(lambda a, c: c.start_agent_loop(self, action_context), self.capabilities, False)
            if not can_start_loop:
                break

            if iterations > self.max_iterations:
                self.update_memory(action_context, memory, "Agent stopped. Max iterations reached.", {})
                break

            if time.time() - start_time > self.max_duration_seconds:
                self.update_memory(action_context, memory, "Agent stopped. Max duration reached.", {})
                break

            for capability in self.capabilities:
                capability.start_agent_loop(self, action_context)

            # 1. Construct the prompt for the LLM to generate a response
            prompt = self.construct_prompt(action_context, self.goals, memory)

            # 2. Prompt the agent for its next action
            response = self.prompt_llm_for_action(action_context, prompt)

            # 3. Handle the agent's response and execute the action (if any)
            result = self.handle_agent_response(
                action_context=action_context,
                response=response
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

