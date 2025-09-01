import uuid
from datetime import datetime
from pydantic import BaseModel, Field, conint, constr, confloat
from typing import List, Dict, Optional, Any


def to_snake_case(s: str) -> str:
    return "".join(["_" + c.lower() if c.isupper() else c for c in s]).lstrip("_")


class CustomBaseModel(BaseModel):
    class Config:
        alias_generator = to_snake_case
        populate_by_name = True


class Step(CustomBaseModel):
    stepName: Optional[str] = None
    instructions: Optional[str] = None
    tool: str
    description: Optional[str] = None
    actionSegment: Optional[str] = None
    useAdvancedReasoning: Optional[bool] = False
    args: Optional[Dict[str, Any]] = None
    values: Optional[Dict[str, Any]] = None
    on_failure: Optional[Dict[str, Any]] = None
    retries: Optional[int] = Field(None, ge=0)
    timeout: Optional[int] = Field(None, ge=0)  # Timeout in seconds


class Limits(CustomBaseModel):
    max_time: Optional[int] = Field(None, ge=0)  # Maximum time limit in seconds
    max_cost: Optional[float] = Field(None, ge=0)  # Maximum cost
    max_steps: Optional[int] = Field(None, ge=0)  # Maximum number of steps
    tool_budgets: Optional[Dict[str, int]] = None  # Budgets for each tool


class Notification(CustomBaseModel):
    type: str  # Example types: email, sms, webhook
    address: str  # Can be an email address, phone number, or URL
    message: str
    parameters: Optional[Dict[str, Any]] = (
        None  # Additional parameters as key/value pairs
    )


class Notifications(CustomBaseModel):
    on_completion: Optional[Notification] = None
    on_failure: Optional[Notification] = None


class Logging(CustomBaseModel):
    level: str  # Define valid log levels
    output: str


class Workflow(CustomBaseModel):
    id: str
    name: str
    version: str
    author: str
    created_at: str  # ISO 8601 datetime format
    updated_at: str  # ISO 8601 datetime format
    tags: List[str]
    description: str
    steps: List[Step]
    parameters: Optional[Dict[str, Any]]
    limits: Limits
    retries: Optional[int] = Field(None, ge=0)
    notifications: Optional[Notifications] = None
    logging: Logging

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Workflow":
        steps_data = data.pop("steps", [])
        steps = [Step(**step) for step in steps_data]
        return cls(steps=steps, **data)

    @classmethod
    def from_steps(cls, steps: List[Dict[str, Any]], prompt: str) -> "Workflow":
        current_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        step_objects = [Step(**step) for step in steps]  # Convert dicts to Step objects
        return cls(
            id=str(uuid.uuid4()),
            name="Default Workflow",
            version="1.0",
            author="Default Author",
            created_at=current_time,
            updated_at=current_time,
            tags=["default"],
            description="A workflow created from a list of steps.",
            steps=step_objects,
            parameters={"prompt": prompt},
            limits=Limits(),
            retries=3,
            notifications=Notifications(),
            logging=Logging(level="info", output="workflow_logs.txt"),
        )

    class Config:
        json_schema_extra = {
            "example": {
                "id": "workflow_001",
                "name": "Email Scheduling Workflow",
                "version": "1.0",
                "author": "Your Name",
                "created_at": "2023-10-10T07:00:00Z",
                "updated_at": "2023-10-10T07:00:00Z",
                "tags": ["scheduling", "email", "automation"],
                "description": "This workflow automates the process of scheduling an event by communicating with a user via email.",
                "steps": [
                    {
                        "instructions": "Draft an email to the user, jules.white@vanderbilt.edu, telling them that you are scheduling the event.",
                        "tool": "draft_email",
                        "args": {
                            "message": "Provide information on the scheduling request for their awareness."
                        },
                        "on_failure": {
                            "action": "log",
                            "message": "Failed to draft initial email.",
                        },
                        "retries": 2,
                        "timeout": 300,  # 5 minutes in seconds
                    },
                    {
                        "tool": "think",
                        "instructions": "What dates/times do we need to schedule a meeting based on the original email?",
                        "args": {
                            "what_to_think_about": "List concretely the dates and times that are available for scheduling based on the email so we can find times that work for the user."
                        },
                        "on_failure": {
                            "action": "terminate",
                            "message": "Failed to determine suitable dates/times.",
                        },
                        "timeout": 120,  # 2 minutes in seconds
                    },
                ],
                "parameters": {"email": "user@example.com"},
                "limits": {
                    "max_time": 3600,  # 1 hour in seconds
                    "max_cost": 100.00,
                    "max_steps": 10,
                    "tool_budgets": {
                        "draft_email": 3,
                        "think": 5,
                        "get_availability": 2,
                        "terminate": 1,
                    },
                },
                "retries": 3,
                "notifications": {
                    "on_completion": {
                        "type": "email",
                        "address": "admin@domain.com",
                        "message": "The workflow has been completed successfully.",
                        "parameters": {"subject": "Workflow Completed"},
                    },
                    "on_failure": {
                        "type": "email",
                        "address": "admin@domain.com",
                        "message": "The workflow has failed.",
                        "parameters": {"subject": "Workflow Failed"},
                    },
                },
                "logging": {"level": "info", "output": "workflow_logs.txt"},
            }
        }
