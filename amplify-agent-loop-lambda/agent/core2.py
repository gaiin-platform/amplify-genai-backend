from typing import Dict, Any, Optional, List, Protocol, runtime_checkable, Callable
from datetime import datetime
from pydantic import BaseModel, Field, PrivateAttr
from uuid import uuid4

# Core Data Models
class Memory(BaseModel):
    content: Any
    memory_type: str
    metadata: Dict[str, Any]
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())

    class Config:
        frozen = True

class AgentState(BaseModel):
    memories: List[Memory] = Field(default_factory=list)
    conversation_id: str = Field(default_factory=lambda: str(uuid4()))
    metadata: Dict[str, Any] = Field(default_factory=dict)
    should_continue: bool = True

    class Config:
        frozen = True

    def add_memory(self, memory: Memory) -> 'AgentState':
        return self.model_copy(update={'memories': [*self.memories, memory]})

    def with_metadata(self, new_metadata: Dict[str, Any]) -> 'AgentState':
        return self.model_copy(update={'metadata': {**self.metadata, **new_metadata}})

    def set_continue(self, should_continue: bool) -> 'AgentState':
        return self.model_copy(update={'should_continue': should_continue})

class Action(BaseModel):
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    tag: Optional[str] = None

    @classmethod
    def none(cls) -> "Action":
        return cls(tool="no_action")

    class Config:
        frozen = True

NO_ACTION = Action.none()

class AgentResponse(BaseModel):
    raw_response: str
    parsed_response: Optional[Action] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        frozen = True

class AgentContext(BaseModel):
    """Simplified context for agent execution"""
    id: str = Field(default_factory=lambda: str(uuid4()))
    _data: Dict[str, Any] = PrivateAttr(default_factory=dict)
    _listeners: List[Callable[[str, Any], None]] = PrivateAttr(default_factory=list)

    def __init__(self, **data):
        super().__init__()
        self._data = data
        self._listeners = []

    def emit(self, event_type: str, event: Any) -> None:
        for listener in self._listeners:
            try:
                listener(event_type, event)
            except Exception as e:
                print(f"Event listener error: {str(e)}")

    def add_listener(self, listener: Callable[[str, Any], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def model_dump(self) -> Dict[str, Any]:
        return {"id": self.id, "data": self._data}

@runtime_checkable
class Capability(Protocol):
    def enhance_prompt(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return messages

    def process_response(self, response: AgentResponse) -> AgentResponse:
        return response

    def process_memories(self, memories: List[Memory], response: AgentResponse) -> List[Memory]:
        return memories

    def should_continue(self, state: AgentState) -> bool:
        return True

@runtime_checkable
class Environment(Protocol):
    async def execute(self, context: AgentContext, action: Action) -> Any:
        ...

class SimpleEnvironment:
    async def execute(self, context: AgentContext, action: Action) -> Any:
        return f"Executed: {action}"

class Agent:
    def __init__(self, llm_service, goals=None, capabilities=None, environment=None):

        self.llm = llm_service
        self.goals = goals or []
        self.capabilities = capabilities or []
        self.environment = environment or SimpleEnvironment()

    async def run(self, context: AgentContext, initial_input: str, initial_state: Optional[AgentState] = None) -> AgentState:
        state = initial_state or AgentState()
        state = self._add_memory(state, initial_input, "user")

        context.set('agent', self)
        context.set('llm_service', self.llm)

        try:
            while state.should_continue:
                state = await self._execute_iteration(context, state)
        except Exception as e:
            state = self._add_memory(state, str(e), "error")

        return state

    async def _execute_iteration(self, context: AgentContext, state: AgentState) -> AgentState:
        try:
            # Emit iteration start
            context.emit("agent/iteration/started", {"state": state.model_dump()})

            # Build and process prompt
            messages = self._build_messages(state)
            for cap in self.capabilities:
                messages = cap.enhance_prompt(messages) or messages # Must have messages in the prompt

            context.emit("agent/prompt/created", {"messages": messages})

            # Get LLM response
            raw_response = await self.llm.generate_response(messages)
            context.emit("agent/response/received", {"response": raw_response})

            response = AgentResponse(raw_response=raw_response)

            # Process response
            for cap in self.capabilities:
                response = cap.process_response(response) or response # Can't be None

            context.emit("agent/response/processed", {
                "processed_response": response.model_dump(),
                "original_response": raw_response
            })

            # Execute action if any
            if response.parsed_response:
                result = await self.environment.execute(context, response.parsed_response)
            else:
                result = response.raw_response

            context.emit("agent/actions/executed", {
                "result": result,
                "response": response.model_dump()
            })

            # Process memories
            new_memories = state.memories
            for cap in self.capabilities:
                new_memories = cap.process_memories(new_memories, response) is not None or new_memories

            context.emit("agent/memories/generated", {
                "new_memories": [m.model_dump() for m in new_memories]
            })

            # Update state
            new_state = AgentState(
                memories=new_memories,
                conversation_id=state.conversation_id,
                metadata=state.metadata
            )

            # Check continuation
            should_continue = all(cap.should_continue(new_state) for cap in self.capabilities)
            final_state = new_state.set_continue(should_continue)

            # Emit iteration end
            context.emit("agent/iteration/ended", {"state": final_state.model_dump()})

            return final_state

        except Exception as e:
            return self._add_memory(state, str(e), "error")

    def _build_messages(self, state: AgentState) -> List[Dict[str, Any]]:
        goals_text = "\n".join(str(goal) for goal in self.goals)
        messages = [{"role": "system", "content": goals_text}]
        messages.extend({"role": m.memory_type, "content": str(m.content)} for m in state.memories)
        return messages

    def _add_memory(self, state: AgentState, content: str, memory_type: str) -> AgentState:
        return state.add_memory(Memory(
            content=content,
            memory_type=memory_type,
            metadata={"type": memory_type}
        ))