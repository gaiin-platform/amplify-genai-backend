import json
import os

from typing import Callable

from agent.llm_service import LiteLLMService
from agent.util import collect_from_capabilities, reduce_capabilities, reduce_capabilities_with_args
from typing import Dict, Any, Optional, List, Protocol, runtime_checkable
from datetime import datetime
from pydantic import BaseModel, Field, PrivateAttr
from uuid import uuid4


class Memory(BaseModel):
    """Immutable memory entry"""
    content: Any
    memory_type: str
    metadata: Dict[str, Any]
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())

    class Config:
        frozen = True

class AgentState(BaseModel):
    """Immutable agent state"""
    memories: List[Memory] = Field(default_factory=list)
    conversation_id: str = Field(default_factory=lambda: str(uuid4()))
    metadata: Dict[str, Any] = Field(default_factory=dict)
    should_continue: bool = True

    class Config:
        frozen = True

    def add_memory(self, memory: Memory) -> 'AgentState':
        """Creates new state with additional memory"""
        return AgentState(
            memories=[*self.memories, memory],
            conversation_id=self.conversation_id,
            metadata=self.metadata,
            should_continue=self.should_continue
        )

    def with_metadata(self, new_metadata: Dict[str, Any]) -> 'AgentState':
        """Creates new state with updated metadata"""
        return AgentState(
            memories=self.memories,
            conversation_id=self.conversation_id,
            metadata={**self.metadata, **new_metadata},
            should_continue=self.should_continue
        )

    def set_continue(self, should_continue: bool) -> 'AgentState':
        """Creates new state with updated continuation status"""
        return AgentState(
            memories=self.memories,
            conversation_id=self.conversation_id,
            metadata=self.metadata,
            should_continue=should_continue
        )

class Message(BaseModel):
    """Message structure using Pydantic instead of TypedDict"""
    role: str
    content: str
    tag: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class AgentPrompt(BaseModel):
    """Immutable prompt structure with helper methods for modification"""
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        frozen = True

    def add(self,
            messages: Optional[List[Dict[str, Any]]] = None,
            metadata: Optional[Dict[str, Any]] = None) -> 'AgentPrompt':
        """Create new prompt with additional messages and/or metadata"""
        return AgentPrompt(
            messages=[*self.messages, *(messages or [])],
            metadata={**self.metadata, **(metadata or {})}
        )

    def add_message(self,
                    role: str,
                    content: str,
                    tag: Optional[str] = None,
                    metadata: Optional[Dict[str, Any]] = None) -> 'AgentPrompt':
        """Create new prompt with an additional single message"""
        message = Message(
            role=role,
            content=content,
            tag=tag,
            metadata=metadata or {}
        )
        return self.add(messages=[message.model_dump()])

    def with_metadata(self, new_metadata: Dict[str, Any]) -> 'AgentPrompt':
        """Create new prompt with updated metadata"""
        return AgentPrompt(
            messages=self.messages,
            metadata={**self.metadata, **new_metadata}
        )

class Action(BaseModel):
    """Immutable action structure"""
    tag: Optional[str] = None
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def none(cls) -> "Action":
        return cls(tool="no_action", args={})

    class Config:
        frozen = True

NO_ACTION = Action(tool="no_action", args={})

class AgentResponse(BaseModel):
    """Immutable response structure"""
    raw_response: str
    parsed_response: Optional[Action] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        frozen = True

class ActionResult(BaseModel):
    """Immutable action result"""
    result: Any
    action_id: str
    action: Action
    time: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        frozen = True

class ContinuationDecision(BaseModel):
    """Immutable decision about whether to continue execution"""
    should_continue: bool
    reason: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        frozen = True

# Base Event class
class AgentEvent(BaseModel):
    """Base class for all agent events"""
    event_type: str
    class Config:
        frozen = True

class IterationStarted(AgentEvent):
    event_type: str = "agent/iteration/started"
    state: AgentState

class IterationEnded(AgentEvent):
    event_type: str = "agent/iteration/ended"
    state: AgentState

class PromptCreated(AgentEvent):
    event_type: str = "agent/prompt/created"
    prompt: AgentPrompt
    state: AgentState

class ResponseReceived(AgentEvent):
    event_type: str = "agent/response/received"
    response: str
    prompt: AgentPrompt
    state: AgentState

class ResponseProcessed(AgentEvent):
    event_type: str = "agent/response/processed"
    processed_response: AgentResponse
    original_response: str
    state: AgentState

class ActionsExecuted(AgentEvent):
    event_type: str = "agent/actions/executed"
    result: ActionResult
    response: AgentResponse
    state: AgentState

class MemoriesGenerated(AgentEvent):
    """Event emitted after memory processing showing old and new state"""
    event_type: str = "agent/memories/generated"
    old_memories: List[Memory]
    new_memories: List[Memory]
    result: ActionResult
    state: AgentState


@runtime_checkable
class Capability(Protocol):
    """Protocol defining what a capability can do"""

    def enhance_prompt(self, prompt: AgentPrompt) -> AgentPrompt:
        """Pure function to enhance prompt"""
        return prompt

    def process_response(self, response: AgentResponse) -> AgentResponse:
        """Pure function to process response"""
        return response

    def process_result(self, result: ActionResult) -> ActionResult:
        """Pure function to process result"""
        return result

    def process_memories(self, memory_state: List[Memory], response: AgentResponse, result: ActionResult) -> List[Memory]:
        """Pure function to process memory state returning the updated list of memories for the agent.

        This function can add memories, remove memories, or modify existing memories, but should return
        a new list of memories.

        Args:
            memory_state: List of current memories
            response: Agent response
            result: Action result
        Returns:
            New set of memories
        """
        return memory_state

    def should_continue(self,
                        state: AgentState,
                        result: ActionResult,
                        new_memories: List[Memory]) -> ContinuationDecision:
        """Pure function to determine if execution should continue"""
        return ContinuationDecision(should_continue=True)

class AgentRegistry:
    def __init__(self):
        self.agents = {}

    def register(self, agent_id, agent_description, agent):
        self.agents[agent_id] = {
            "agent_id": agent_id,
            "description": agent_description,
            "agent": agent
        }

    def get(self, agent_id):
        agent_entry = self.agents.get(agent_id, {})
        return agent_entry.get('agent', None)

    def get_all(self):
        return [agent['agent'] for agent in self.agents]

    def get_ids(self):
        return list(self.agents.keys())

    def get_descriptions(self):
        return [{'agent_id': k, 'description': agent['description']} for k, agent in self.agents.items()]

    def __len__(self):
        return len(self.agents)

    def __iter__(self):
        return iter(self.agents.values())

    def __getitem__(self, name):
        return self.get(name)

    def __contains__(self, name):
        return name in self.agents

    def __repr__(self):
        return f"<AgentRegistry: {json.dumps(self.get_descriptions(), indent=4)}>"


class AgentContext(BaseModel):
    """
    Context for agent execution that is separate from the state of the
    agent itself.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    _data: Dict[str, Any] = PrivateAttr(default_factory=dict)
    _listeners: List[Callable[[str, Any], None]] = PrivateAttr(default_factory=list)

    def __init__(self, id: str | None = None, listeners=None, **data: Any):
        # If id is provided, use it, otherwise uuid4 will be used from the Field default
        super().__init__(id=id) if id else super().__init__()
        self._data = {}
        self._listeners = listeners or []
        # Add any initial data
        for key, value in data.items():
            self.set(key, value)

    def set_agent_registry(self, agent_registry: AgentRegistry) -> None:
        """Set the agent registry"""
        self.set('agent_registry', agent_registry)

    def get_agent_registry(self) -> AgentRegistry:
        """Get the agent registry"""
        return self.get('agent_registry', {})

    def set_llm_service(self, llm_service: Any) -> None:
        """Set the LLM service"""
        self.set('llm_service', llm_service)

    def get_llm_service(self) -> Any:
        """Get the LLM service"""
        return self.get('llm_service', None)

    def add_listener(self, listener: Callable[[str, Any], None]) -> None:
        """Add an event listener"""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[str, Any], None]) -> None:
        """Remove an event listener"""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, event_type: str, event: Any) -> None:
        """Notify all listeners of an event"""
        for listener in self._listeners:
            try:
                listener(event_type, event)
            except Exception as e:
                print(f"Error in event listener: {str(e)}")

    def set(self, key: str, value: Any) -> None:
        """Set a value in the context"""
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the context"""
        return self._data.get(key, default)

    def has(self, key: str) -> bool:
        """Check if a key exists in the context"""
        return key in self._data

    def remove(self, key: str) -> None:
        """Remove a key from the context"""
        if key in self._data:
            del self._data[key]

    def clear(self) -> None:
        """Clear all data from the context"""
        self._data.clear()

    def get_all(self) -> Dict[str, Any]:
        """Get all data as a dictionary"""
        return self._data.copy()

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Custom serialization that includes the data"""
        return {
            "id": self.id,
            "data": self._data
        }


@runtime_checkable
class Environment(Protocol):
    """Protocol defining what an environment can do"""

    async def execute(self, agent_context: AgentContext, action: Any) -> Any:
        """Execute action and return result"""
        ...

class PrintEnvironment:
    """Simple environment that prints actions and returns a string"""
    async def execute(self, agent_context: AgentContext, action: Action) -> Any:
        print(f"Executing action: {action}")
        return f"Executed action: {action}"



class Agent:
    """Agent that processes requests through capabilities using immutable state transitions"""

    def __init__(self,
                 llm_service: Optional[Any] = None,
                 goals: Optional[List[Any]] = None,
                 capabilities: Optional[List[Any]] = None,
                 environment: Optional[Environment] = None):

        default_model = os.environ.get('DEFAULT_MODEL', None)
        if not llm_service and not default_model:
            default_model = "gpt-4o-mini"
            print("Defaulting to GPT-4o-mini model, set the DEFAULT_MODEL environment variable to change this or "
                  "provide an llm_service parameter when constructing the Agent.")

        self.llm = llm_service or LiteLLMService(model=default_model)
        self.goals = goals or []
        self.capabilities = capabilities or []
        self.environment = environment or PrintEnvironment()

    async def run(self,
                  agent_context: AgentContext,
                  initial_input: str,
                  initial_state: Optional[AgentState] = None) -> AgentState:
        """Execute the agent with immutable state transitions"""
        current_state = initial_state or AgentState()
        current_state = self._add_input_memory(current_state, initial_input)

        agent_context.set('agent', self)
        agent_context.set_llm_service(self.llm)

        try:
            while current_state.should_continue:
                current_state = await self._execute_iteration(agent_context, current_state)
        except Exception as e:
            current_state = self._add_error_memory(current_state, e)

        return current_state

    async def _execute_iteration(self, agent_context: AgentContext, state: AgentState) -> AgentState:
        """Execute one iteration, returning new state"""

        try:

            self._emit_event(agent_context, IterationStarted(state=state))

            # Build and enhance prompt
            prompt = self._build_prompt(state)
            enhanced_prompt = reduce_capabilities(self.capabilities, 'enhance_prompt', prompt)
            self._emit_event(agent_context, PromptCreated(prompt=enhanced_prompt, state=state))

            # Get LLM response
            raw_response = await self.llm.generate_response([
                {"content": m["content"], "role": m["role"]}
                for m in enhanced_prompt.messages
            ])
            self._emit_event(agent_context, ResponseReceived(
                response=raw_response,
                prompt=enhanced_prompt,
                state=state
            ))

            # Process response through capabilities
            response = reduce_capabilities(
                self.capabilities,
                'process_response',
                AgentResponse(raw_response=raw_response),
                reverse=True
            )
            self._emit_event(agent_context, ResponseProcessed(
                processed_response=response,
                original_response=raw_response,
                state=state
            ))

            if response.parsed_response:
                # Execute action(s)
                action_result = await self._execute_actions(agent_context, response)
            else:
                action_result = ActionResult(
                    result={"message":response.raw_response},
                    action_id=str(uuid4()),
                    time=str(datetime.now().timestamp()),
                    action=NO_ACTION
                )


            self._emit_event(agent_context, ActionsExecuted(
                result=action_result,
                response=response,
                state=state
            ))

            # Process result through capabilities
            processed_result = reduce_capabilities(
                self.capabilities,
                'process_result',
                action_result
            )

            # Let capabilities generate the updated list of memories for the next loop
            processed_memories = reduce_capabilities_with_args(
                self.capabilities,  # List of capabilities
                'process_memories',
                state.memories,
                response,
                processed_result
            )
            self._emit_event(agent_context, MemoriesGenerated(
                old_memories=state.memories,
                new_memories=processed_memories,
                result=processed_result,
                state=state
            ))

            # Create the new state for the next iteration
            new_state = AgentState(
                memories=processed_memories,
                conversation_id=state.conversation_id,
                metadata=state.metadata,
                should_continue=state.should_continue
            )

            # Check continuation
            continuation = self._check_continuation(new_state, processed_result, processed_memories)
            final_state = new_state.set_continue(continuation.should_continue)

        except Exception as e:
            # print a stack trace
            import traceback
            traceback.print_exc()
            final_state = self._add_error_memory(state, e)

        self._emit_event(agent_context, IterationEnded(state=final_state))

        return final_state

    def _build_prompt(self, state: AgentState) -> AgentPrompt:
        """Build initial prompt from goals and state"""
        # Combine all goal descriptions
        # Create a list of strings from the goals. If the goal is a string, just add it. If it is not a string
        # see if it has a 'description()'. If it does, call it and add the result. If it does not, try to dump
        # it with json.dumps

        goals_text = "\n".join(str(goal) for goal in self.goals)

        # Create messages starting with goals
        messages = [
            {"role": "system", "content": goals_text, "tag": "goals"}
        ]

        # Add memory messages
        memory_messages = [
            {"role": m.memory_type, "content": str(m.content)}
            for m in state.memories
        ]

        return AgentPrompt(messages=messages + memory_messages)

    def _check_continuation(self,
                            state: AgentState,
                            result: ActionResult,
                            new_memories: List[Memory]) -> ContinuationDecision:
        """Check if execution should continue"""
        decisions = collect_from_capabilities(
            self.capabilities,
            'should_continue',
            state=state,
            result=result,
            new_memories=new_memories
        )

        # Stop if any capability says to stop
        stopping_decisions = [d for d in decisions if not d.should_continue]
        if stopping_decisions:
            return stopping_decisions[0]

        return ContinuationDecision(
            should_continue=True,
            reason="All capabilities agree to continue or expressed no opinion"
        )

    async def _execute_actions(self, agent_context: AgentContext, response: AgentResponse) -> ActionResult:
        """Execute action from response"""
        if not response.parsed_response:
            return ActionResult(
                result="No action to execute",
                action_id=str(uuid4()),
                time=str(datetime.now().timestamp()),
                action=Action(tool="no_action", args={}),
                metadata={"type": "no_action"}
            )

        try:
            agent_context.set("action_agent", self)
            result = await self.environment.execute(agent_context, response.parsed_response)
            return ActionResult(
                result=result,
                action=response.parsed_response,
                action_id=str(uuid4()),
                time=str(datetime.now().timestamp()),
                metadata={
                    "type": "action_execution",
                    "action": response.parsed_response
                }
            )
        except Exception as e:
            return ActionResult(
                action_id=str(uuid4()),
                action=response.parsed_response,
                time=str(datetime.now().timestamp()),
                result=f"Action execution failed: {str(e)}",
                metadata={"type": "action_error"}
            )

    def _emit_event(self, agent_context: AgentContext, event: AgentEvent):
        """Emit event to all listeners"""
        agent_context.emit(event.event_type, event.model_dump())

    def _add_input_memory(self, state: AgentState, input_str: str) -> AgentState:
        """Add input memory to state"""
        return state.add_memory(Memory(
            content=input_str,
            memory_type="user",
            metadata={"type": "input"}
        ))

    def _add_error_memory(self, state: AgentState, error: Exception) -> AgentState:
        """Add error memory to state"""
        return state.add_memory(Memory(
            content=str(error),
            memory_type="error",
            metadata={
                "error_type": type(error).__name__,
                "type": "error"
            }
        ))
