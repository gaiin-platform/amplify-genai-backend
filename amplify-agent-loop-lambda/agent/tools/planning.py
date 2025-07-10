import json

from agent.components.tool import register_tool
from agent.core import Memory, ActionRegistry
from agent.tools.prompt_tools import prompt2, prompt_llm_with_messages


sample_plans = """
Example Plans:
-------------
Input:
Hi! I am looking for a fun and uplifting way to start your day. Can you please tell me the current time in your timezone? I’d also love to receive a random inspirational quote to motivate you as you begin your work. It would be great if you could combine this information into a personalized message that I can reflect on throughout the day. Thanks!

Plan:

1. **Compute the current time in a specified timezone**: You will use the `exec_code` action to run the Python code necessary to calculate the current time in the desired timezone.

2. **Fetch a random inspirational quote from an online source**: You will retrieve a random quote using the `get_web_page_text` action from a dedicated quotes website.

3. **Create a personalized message combining the current time and the quote**: You will use the `prompt_llm_with_messages` action to instruct the LLM to generate a personalized message that includes the current time and the inspirational quote.

4. **Terminate and provide you with the personalized message**: Finally, you will use the `terminate` action to complete the workflow and present the personalized message to you.

Here is the detailed plan:

1. **Step 1**: Compute the current time in a specified timezone
   - Use the `exec_code` action to run code that calculates and returns the current time in the specified timezone.

2. **Step 2**: Fetch a random inspirational quote
   - Use the `get_web_page_text` action to obtain a random inspirational quote from an online source.

3. **Step 3**: Create a personalized message
   - Use the `prompt_llm_with_messages` action. The prompt will blend the current time from Step 1 with the quote obtained in Step 2, asking the LLM to create an engaging personalized message.

4. **Step 4**: Terminate
   - Use the `terminate` action to display the personalized message to you.

The completed actions are:
None.

The next action to take is:

1. **Step 1**: Compute the current time in a specified timezone
   - Use the `exec_code` action to run the Python code necessary to calculate the current time in the desired timezone.


Input:
I want to create a healthy meal plan for the week based on the ingredients you have at home. Can you help me generate a recipe that utilizes these ingredients? Also, I’d like to know the dietary information for the recipe to make sure it’s nutritious. Lastly, I want to ensure that this information meets my health standards. Thanks!

Plan:

1. **Generate a new recipe based on available ingredients**: You will use the `prompt_expert` action to consult the LLM, acting as a culinary expert, to create a new recipe using specified available ingredients.

2. **Fetch dietary information relevant to the created recipe**: You will retrieve detailed dietary information using the `get_web_page_text` action from a reputable nutrition website.

3. **Validate the dietary information against health standards**: You will use the `qa_check` action to ensure that the dietary information complies with specified health criteria.

4. **Terminate and provide you with the recipe and dietary validation results**: Finally, you will use the `terminate` action to complete the workflow and present you with the generated recipe and the results of the dietary validation.

Here is the detailed plan:

1. **Step 1**: Generate a new recipe based on available ingredients
   - Use the `prompt_expert` action to generate a recipe using the specified available ingredients while consulting culinary expertise.

2. **Step 2**: Fetch dietary information
   - Use the `get_web_page_text` action to obtain dietary information relevant to the created recipe.

3. **Step 3**: Validate dietary information
   - Use the `qa_check` action to ensure that the provided dietary information adheres to defined health standards.

4. **Step 4**: Terminate
   - Use the `terminate` action to display the generated recipe and the validation results to you.

The completed actions are:
None.

The next action to take is:

1. **Step 1**: Generate a new recipe based on available ingredients
   - Use the `prompt_expert` action to consult the LLM for a detailed recipe based on the specified ingredients.


Input:
I am trying to improve your productivity throughout the week and I need help creating a structured daily schedule. Can you help you with a plan that includes daily tasks and perhaps some motivational tips? I want everything to be laid out clearly so you can follow it easily.

Plan:

1. **Create a schedule for daily tasks**: You will use the `prompt_llm_for_json` action to generate a structured JSON schedule that outlines daily tasks, including time slots and descriptions for each task.

2. **Fetch motivation tips from a wellness site**: You will retrieve motivation tips by using the `get_web_page_text` action from a dedicated wellness website.

3. **Combine the schedule and motivation tips into a comprehensive plan**: You will employ the `prompt_llm_with_info` action to instruct the LLM to integrate the daily task schedule with the motivation tips, creating a comprehensive daily plan to enhance productivity.

4. **Terminate and provide you with the comprehensive daily plan**: Finally, you will use the `terminate` action to complete the workflow and present the comprehensive daily plan to you.

Here is the detailed plan:

1. **Step 1**: Create a schedule for daily tasks
   - Use the `prompt_llm_for_json` action to generate a JSON structure containing a daily task schedule.

2. **Step 2**: Fetch motivation tips
   - Use the `get_web_page_text` action to collect motivation tips from a wellness site.

3. **Step 3**: Combine the schedule and motivation tips
   - Use the `prompt_llm_with_info` action. The prompt will include the daily task schedule from Step 1 and the motivation tips from Step 2, asking the LLM to create a cohesive daily plan.

4. **Step 4**: Terminate
   - Use the `terminate` action to display the comprehensive daily plan to you.

The completed actions are:
None.

The next action to take is:

1. **Step 1**: Create a schedule for daily tasks
   - Use the `prompt_llm_for_json` action to generate a structured JSON schedule that outlines daily tasks.
--------------
"""


@register_tool(tags=["planning"])
def create_plan(action_context, _memory: Memory, action_registry: ActionRegistry):

    def get_memory_message(memory):
        type = memory["type"] if "type" in ["user", "assistant", "system"] else "user"
        return {"role": type, "content": memory["content"]}

    actions_message = {
        "role": "user",
        "content": f"""   
Available Actions:    
------------------
{json.dumps([a.todict() for a in action_registry.get_actions()])}
""",
    }

    messages = [get_memory_message(m) for m in _memory.items]

    messages += [actions_message]

    messages += [
        {
            "role": "user",
            "content": f"""
I am providing some sample plans for reference as to the content and structure of plans.

{sample_plans}
    
Stop and think step by step. What set of actions should you tell me to perform to solve this problem? What is
a simple and efficient way to tell me how to solve this problem? What are the key steps to follow?

Write out your concrete step by step plan for me referencing the available actions by name here:
""",
        }
    ]

    return prompt_llm_with_messages(action_context=action_context, prompt=messages)


@register_tool(tags=["planning"])
def determine_progress(
    action_context, _memory: Memory, action_registry: ActionRegistry
):

    def get_memory_message(memory):
        type = memory["type"] if "type" in ["user", "assistant", "system"] else "user"
        return {"role": type, "content": memory["content"]}

    messages = [get_memory_message(m) for m in _memory.items]

    messages += [
        {
            "role": "user",
            "content": """
Output Format:
------------
The following actions have been completed:

<Insert completed actions here>

The following actions are remaining:

<Insert remaining actions here>

The next action to take is:

<Insert next action here>
------------

Stop and think step by step. What was the overall plan? What actions have been completed? What actions are remaining?
What should be the next action to take?

Using the Output Format as your guide, and output the completed actions, remaining actions, and next action.
""",
        }
    ]

    return prompt_llm_with_messages(action_context=action_context, prompt=messages)
