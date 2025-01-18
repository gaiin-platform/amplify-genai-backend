import inspect
from datetime import datetime
from typing import Callable, Any, Dict, Optional

from pydantic import Field, BaseModel, ConfigDict

from agent.core import AgentContext, Environment, Action, ActionResult
from agent.tools.python_action_registry import ActionRegistry
from agent.tools.python_tool import ActionSpec


class EnvironmentEvent(BaseModel):
    """Base class for all environment events"""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    event_type: str


class ActionExecuteEvent(EnvironmentEvent):
    """Event emitted when an action is about to be executed"""
    event_type: str = Field("environment/action/execute", frozen=True)
    action: Action
    action_spec: ActionSpec
    args: Dict[str, Any]


class ActionResultEvent(EnvironmentEvent):
    """Event emitted when an action completes execution"""
    event_type: str = Field("environment/action/result", frozen=True)
    action: Action
    action_spec: ActionSpec
    result: Any


class ActionResult(BaseModel):
    """Standardized format for action results"""
    tool_executed: bool = True
    result: Any
    id: str
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"))


class ActionError(ActionResult):
    """Standardized format for action errors"""
    tool_executed: bool = False
    error: str
    result: None = None


class ActionResultFormattedEvent(EnvironmentEvent):
    """Event emitted when an action result has been formatted"""
    event_type: str = Field("environment/action/result_formatted", frozen=True)
    action: Action
    action_spec: ActionSpec
    result: ActionResult


def has_named_parameter(func, param_name):
    # Get the signature of the function
    sig = inspect.signature(func)
    # Check if the parameter name exists in the signature's parameters
    return param_name in sig.parameters


class PythonFunctionEnvironment(Environment):
    def __init__(self, action_registry: ActionRegistry):
        self.action_registry = action_registry
        self.result_history = []
        self.current_iteration = 0
        self.result_limit = 1000  # For truncation

    async def execute(self, context: AgentContext, action: Action) -> ActionResult:
        """Execute action and track results"""
        try:
            args_copy = action.args.copy()
            agent = context.get("action_agent")

            action_spec = self.action_registry.get_action(action.tool)

            # Create emit shorthand
            emit: Callable[[EnvironmentEvent], None] = lambda e: context.emit(e.event_type, e.model_dump())

            # Check if the action has a named parameter "agent_context"
            if has_named_parameter(action_spec.function, "agent_context"):
                args_copy["agent_context"] = context

            if has_named_parameter(action_spec.function, "action_agent"):
                args_copy["action_agent"] = agent

            # Create and emit execute event
            emit(ActionExecuteEvent(
                action=action,
                action_spec=action_spec,
                args=args_copy
            ))

            if inspect.iscoroutinefunction(action_spec.function):
                result = await action_spec.function(**args_copy)
            else:
                result = action_spec.execute(**args_copy)

            # Create and emit raw result event
            emit(ActionResultEvent(
                action=action,
                action_spec=action_spec,
                result=result
            ))

            formatted_result = ActionResult(
                result=result,
                id=f"$#{self.current_iteration}"
            )
            self.current_iteration += 1

            # Create and emit formatted result event
            emit(ActionResultFormattedEvent(
                action=action,
                action_spec=action_spec,
                result=formatted_result
            ))

            self.result_history.append(formatted_result)
            return formatted_result

        except Exception as e:
            error_result = ActionError(
                error=str(e),
                id=f"$#{self.current_iteration}"
            )
            self.current_iteration += 1
            self.result_history.append(error_result)
            return error_result

    def get_result_by_id(self, result_id: str) -> Optional[ActionResult]:
        """Retrieve a specific result"""
        for result in self.result_history:
            if result.id == result_id:
                return result
        return None