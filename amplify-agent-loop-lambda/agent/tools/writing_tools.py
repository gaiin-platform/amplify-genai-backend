from typing import Dict, List, Any

from agent.components.tool import register_tool
from agent.core import ActionContext
from agent.prompt import Prompt


@register_tool(tags=["writing"])
def write_long_content(
    action_context: ActionContext, outline: List[Dict[str, Any]]
) -> str:
    """
    Turn the outline into long-form written content. The LLM will be prompted
    to write the content for each section of the outline. Each section or subsection of the
    outline will correspond to approximately 1pg of content.

    An outline should have the form:

    [
      {
        "section": "Introduction",
        "notes": "Write an introduction to XYZ."
      },
      {
        "section": "Section X",
        "subsections": [
          {
            "section": "Some Subsection X.1",
            "notes": "Write XYZ part of QRS."
          },
          {
            "section": "Another Subsection X.2",
            "notes": "Write about TUV...."
            "expand": true
          }
        ]
      },
      {
        "section": "Conclusion",
        "notes": "Write a conclusion."
      }
    ]

    The outline can have an arbitrary number of sections and subsections and arbitrary nesting.
    If you have a smaller number of sections, you can include detailed notes on what to write about
    in the "content". If you include the "expand":true flag, the LLM will think about that seciton
    and expand on it with a detailed outline before writing it.

    :param outline:
    :return:
    """

    content = ""

    for section in outline:
        content += write_section(action_context, content, section)

    return content


def write_section(
    action_context: ActionContext, content: str, section: Dict[str, Dict]
) -> str:
    """
    Write a section of the outline.

    :param content:
    :param section:
    :return:
    """
    generate_response = action_context.get("llm")

    section_content = ""

    # check if the section has subsections and recursively build the content from them if it does
    if section.get("subsections"):
        for subsection in section["subsections"]:
            section_content += write_section(action_context, content, subsection)

    else:
        title = section["section"]
        notes = section.get(
            "notes", "write appropriate cohesive content to the preceding section"
        )

        prompt = [
            {
                "role": "system",
                "content": """
            Be extremely careful to make sure that the material
            is completely cohesive with whatever comes before. 
            
            You should include the exact title of the section in consistent formatting with what comes before
            and do not alter it. Your output must start with the formatted section title.
            
            Remember, more sections will come, so don't arbitrarily wrap up with "In conclusion..." type language,
            since that will likely not work with what comes next, unless this is truly a "conclusion" section. 
            """,
            },
            {"role": "user", "content": content},
            {
                "role": "user",
                "content": f"Add a section: \n'{title}'\n Here are some notes to use in writing: "
                f"\n------------\n{notes}\n------------\n"
                f"Now, start with the markdown formatted section title and write the content:",
            },
        ]

        section_content = generate_response(Prompt(messages=prompt))

    return section_content
