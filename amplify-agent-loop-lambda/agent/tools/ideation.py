from typing import List

from agent.components.tool import register_tool
from agent.core import ActionContext
from agent.prompt import Prompt
from agent.tools.prompt_tools import prompt2


@register_tool(tags=["ideation"])
def critique_and_improve_content(action_context: ActionContext, content: str):
    generate_response = action_context.get("llm")
    generator_instructions = f"Critique this content and think of concrete steps to dramatically improve it:\n\n{content}"
    prompt = Prompt(messages=[{"role": "user", "content": generator_instructions}])
    critique = generate_response(prompt)
    # apply the critique to the content
    updated_content = generate_response(
        Prompt(
            messages=[
                {
                    "role": "user",
                    "content": f"Apply the following critique to the content to improve it:\n\n{critique}\n------------\nContent:\n{content}",
                }
            ]
        )
    )
    return updated_content


@register_tool(tags=["ideation"])
def ideate(
    action_context: ActionContext,
    generator_instructions: str,
    iterations=3,
    improve_content=False,
):

    generate_response = action_context.get("llm")

    ideas = []
    for i in range(iterations):
        print(f"Generating idea {i+1} of {iterations}")
        response = generate_response(
            Prompt(messages=[{"role": "user", "content": generator_instructions}])
        )

        if improve_content:
            response = critique_and_improve_content(action_context, response)

        ideas.append(response)

    variation_prompts = []
    for i in range(iterations):
        print(f"Generating prompt variation {i+1} of {iterations}")
        response = generate_response(
            Prompt(
                messages=[
                    {
                        "role": "user",
                        "content": f"Create a highly detailed variation on this prompt that will generate foundational, but unique variations of the content. "
                        f"Consider key theories, frameworks, and ideas from related domains to include. Prompt to Adapt:\n-------------\n{generator_instructions}\n----------------\n",
                    }
                ]
            )
        )

        variation_prompts.append(response)

    # Generate new ideas with the prompt variations
    new_ideas = []
    for i in range(iterations):
        print(f"Generating new idea with prompt variation {i+1} of {iterations}")
        response = generate_response(
            Prompt(messages=[{"role": "user", "content": variation_prompts[i]}])
        )

        if improve_content:
            response = critique_and_improve_content(action_context, response)

        new_ideas.append(response)

    combined_content = "\n".join(ideas + new_ideas)

    print(f"Generating best of breed content from {len(ideas + new_ideas)} ideas")
    best_of_breed = generate_response(
        Prompt(
            messages=[
                {
                    "role": "user",
                    "content": f"Combine the best parts of this content and output it as a single, cohesive response. \n\n{combined_content}",
                }
            ]
        )
    )

    return best_of_breed


@register_tool(tags=["ideation"])
@prompt2(
    """
Create a taxonomy for the provide information as an ASCII tree:
{information}
"""
)
def create_taxonomy(result_references: List[str]):
    pass
