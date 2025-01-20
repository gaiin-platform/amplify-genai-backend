import json
import re

from agent.game.action import ActionRegistry, Action
from agent.game.environment import Environment
from agent.game.goal import Goal
from agent.game.languages import AgentNaturalLanguage
from agent.core import Agent
from agent.tools.common_tools import terminate


def build(environment: Environment):
    """
    Initialize the base agent with initial actions and goals
    """
    goals = [
        Goal(
            name="Core Instructions",
            description=""""
                Your goal is to explain the steps you took and the end result. Do not 
                Do not reply with JSON. Generate a thoughtful, accurate, and detailed response.
                The result that the user asked for must be provided verbatim as the user cannot
                see the results of actions. 
                
                If the task required actions and any of the parts of the result are long, you should incorporate them by reference
                by referring to the "id" of the action result. You can do this by including a special
                markdown block as shown below:
                
                ```result
                $#{id}
                ```
                
                This will be replaced with the actual result when the response is generated.
                Example of incorporating the 3rd result:
                
                ```result
                $#3
                ```
                
                Example of incorporating the 1st result:
                
                ```result
                $#1
                ```
                
                This would be replaced with the result with id $#3.
                """
        )
    ]

    actions = ActionRegistry()
    actions.register(
        Action(
            name="terminate",
            description="Terminate the conversation",
            function=terminate,
            terminal=True,
            output={"message": "str"},
            args={}
        )
    )


    summarizer = Agent(
        goals=goals,
        agent_language=AgentNaturalLanguage(),
        action_registry=actions,
        environment=environment
    )

    return summarizer

