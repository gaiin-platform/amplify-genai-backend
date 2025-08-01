import inspect
import time
from typing import Any

from agent.core import ActionContext, Action, Environment


def has_named_parameter(func, param_name):
    # Get the signature of the function
    sig = inspect.signature(func)
    # Check if the parameter name exists in the signature's parameters
    return param_name in sig.parameters


class PythonEnvironment(Environment):
    def __init__(self):
        super().__init__()
        self.result_history = []
        self.current_iteration = 0
        self.result_limit = 1000  # For truncation

    def execute_action(
        self, agent, action_context: ActionContext, action: Action, args: dict
    ) -> dict:
        """Execute action and track results"""
        try:

            args_copy = args.copy()
            # Check if the action has a named parameter "action_context"
            if has_named_parameter(action.function, "action_context"):
                # If the action has an "action_context" parameter, pass the environment as an argument
                args_copy["action_context"] = action_context

            if has_named_parameter(action.function, "action_agent"):
                args_copy["action_agent"] = agent

            # Iterate through the keys in the action_context.properties and add them to
            # if the action.function has a matching named parameter and the parameter is not already in the args_copy
            for key, value in action_context.properties.items():
                if (
                    has_named_parameter(action.function, "_" + key)
                    and key not in args_copy
                ):
                    args_copy["_" + key] = value

            result = action.execute(**args_copy)
            metadata = None

            if isinstance(result, dict):
                metadata = result.get("__meta__", None)

            formatted_result = self.format_result(action, result, metadata)

            self.result_history.append(formatted_result)
            return formatted_result
        except Exception as e:
            return {"tool": action.name, "tool_executed": False, "error": str(e)}

    def format_result(self, action, result: Any, metadata: Any) -> dict:
        """Format and add metadata to result"""
        result_dict = {
            "tool": action.name,
            "tool_executed": True,
            "result": result,
            "id": f"$#{self.current_iteration}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }

        if metadata:
            result_dict["metadata"] = metadata

        self.current_iteration += 1
        return result_dict

    def get_result_by_id(self, result_id: str) -> dict | None:
        """Retrieve a specific result"""
        for result in self.result_history:
            if result["id"] == result_id:
                return result
        return None
