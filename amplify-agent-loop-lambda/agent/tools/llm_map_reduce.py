from typing import Tuple, List, Union

from agent.components.util import extract_markdown_block
from agent.tools.prompt_tools import prompt_llm_with_messages


def llm_split(action_context, instructions: str, content: str) -> List[str]:
    split_prompt = """
### **Task: Generate Precise Split Points in a Large Document**

You will receive a **very long text document** and a set of **instructions describing how to split it**. Your task is to provide precise points where the document should be split.

---

### **Output Format**
- Provide split points in the format: `<LINE>:<CHAR>`
- Example: If the split should be after character 50 on line 10, output `10:50`.
- End the output with `END`.

---

### **Guidelines**
- If no split is needed, output `NOSPLIT`.
- Ensure that all provided split points are precise and accurate.
- Only provide needed split points, do not over-segment the document.
- Your response must be enclosed within a ```output markdown block.
"""

    def extract_split_points(response: str) -> Union[List[Tuple[int, int]], str]:
        splits = []
        for line in response.split("\n"):
            line = line.strip()
            if line == "END":
                return splits
            elif line == "NOSPLIT":
                return "NOSPLIT"
            parts = line.split(":")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                splits.append((int(parts[0]), int(parts[1])))
        return splits

    all_splits = []
    max_token_limit = 100000

    while content:
        response = prompt_llm_with_messages(
            action_context=action_context,
            prompt=[
                {"role": "system", "content": split_prompt},
                {"role": "user", "content": "### INSTRUCTIONS\n" + instructions + "\n"},
                {
                    "role": "user",
                    "content": "```input\n" + content[:max_token_limit] + "\n```",
                },
            ],
        )

        split_points = extract_split_points(extract_markdown_block(response, "output"))

        if split_points == "NOSPLIT":
            all_splits.append(content)
            break

        end_detected = any(line.strip() == "END" for line in response.split("\n"))
        if not end_detected:
            split_points = split_points[:-1]

        previous_split_index = 0
        for line, char in split_points:
            split_index = (
                sum(len(line) + 1 for line in content.splitlines()[: line - 1]) + char
            )
            all_splits.append(content[previous_split_index:split_index])
            previous_split_index = split_index

        content = content[previous_split_index:]

    return all_splits
