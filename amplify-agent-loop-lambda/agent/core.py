import json
import re
import time
import traceback
from functools import reduce
from statistics import correlation
from typing import List, Dict, Any, Type, Callable

from agent import tool
from agent.game.action import ActionRegistry, ActionContext
from agent.game.agent_registry import AgentRegistry
from agent.game.environment import Environment
from agent.game.goal import Goal
from agent.game.languages import AgentJsonActionLanguage, AgentNaturalLanguage, AgentLanguage
from agent.game.memory import Memory
from agent.prompt import generate_response
from agent.tool import tools
import agent.tools.common_tools
import agent.tools.writing_tools
import agent.tools.code_exec
import agent.tools.workflow
import agent.tools.file_handling


class Capability:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def process_action(self, agent, action_context: ActionContext, action: dict) -> dict:
        return action

    def process_response(self, agent, action_context: ActionContext, response: str) -> str:
        return response

    def process_prompt(self, agent, action_context: ActionContext, prompt: List[dict]) -> List[dict]:
        return prompt

    def should_terminate(self, agent, action_context: ActionContext, response: str) -> bool:
        return False


class Agent:
    def __init__(self,
                 goals: List[Goal],
                 agent_language: AgentLanguage,
                 action_registry: ActionRegistry,
                 environment: Environment,
                 generate_response: Callable[[List[dict]], str],
                 capabilities: List[Capability] = []):
        """
        Initialize an agent with its core GAME components
        """
        self.goals = goals
        self.agent_language = agent_language
        self.actions = action_registry
        self.environment = environment
        self.generate_response = generate_response
        self.capabilities = capabilities or []

    def construct_prompt(self, action_context: ActionContext, goals:List[Goal], memory: Memory) -> List[dict]:
        """Build prompt with memory context"""
        #context = self.memory.get_relevant_context(self.current_goal)

        prompt = self.agent_language.construct_prompt(
            actions=self.actions.get_actions(),
            environment=self.environment,
            goals=self.goals,
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

        :param response:
        :param result:
        :return:
        """

        send_event = action_context.incremental_event()

        send_event("agent/memory/update", {"response": response, "result": result})

        result_summary_length_limit = 1000
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

        for m in new_memories:
            memory.add_memory(m)

        send_event("agent/memory/new_memories", {"memories": new_memories})

        return result


    def prompt_llm_for_action(self, action_context: ActionContext, full_prompt: List[dict]) -> [str,None]:
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
            **user_action_context_props
        })

        # Record the initial task
        self.set_current_task(action_context, memory, user_input)

        while True:
            # 1. Sense & Think Phase
            prompt = self.construct_prompt(action_context, self.goals, memory)

            # 2. Expression Phase
            response = self.prompt_llm_for_action(action_context, prompt)

            # 3. Action Phase
            result = self.handle_agent_response(
                action_context=action_context,
                response=response
            )

            # 4. Update memory with knowledge of what the agent did and how the environment responded
            self.update_memory(action_context, memory, response, result)

            # 5. Continuation Decision
            if self.should_terminate(action_context, response):
                break

        return memory

