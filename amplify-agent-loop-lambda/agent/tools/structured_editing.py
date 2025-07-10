from typing import Tuple, Callable

from agent.components.tool import register_tool
from agent.components.util import add_line_numbers, extract_markdown_block
from agent.tools.prompt_tools import qa_check

from typing import List, Dict, Union


def apply_multiline_edit_operations(
    text: str, operations: List[Dict[str, Union[str, int, Tuple[int, int]]]]
) -> str:
    lines = text.split("\n")

    # Fix sorting by always extracting the first element if line is a tuple
    def sort_key(op):
        line = op.get("line", op.get("after_line", 0))
        return (
            line if isinstance(line, int) else line[0]
        )  # Use the first line for sorting

    for op in sorted(operations, key=sort_key, reverse=True):
        try:
            if op["operation"] == "replace":
                start, end = (
                    (op["line"], op["line"])
                    if isinstance(op["line"], int)
                    else op["line"]
                )
                lines[start - 1 : end] = op["content"].split("\n")
            elif op["operation"] == "delete":
                start, end = (
                    (op["line"], op["line"])
                    if isinstance(op["line"], int)
                    else op["line"]
                )
                del lines[start - 1 : end]
            elif op["operation"] == "add":
                lines.insert(
                    op["after_line"], op["content"].strip()
                )  # Insert after the given line
        except Exception as e:
            pass

    return "\n".join(lines)


def parse_multiline_edit_operations(
    edit_output: str,
) -> List[Dict[str, Union[str, int, tuple]]]:
    try:
        operations = []
        lines = edit_output.strip().split("\n")

        current_operation = None
        current_content = []

        for line in lines:
            try:
                if any(line.startswith(op) for op in ["REPLACE,", "ADD,", "DELETE,"]):
                    if current_operation:  # Save the previous operation
                        if current_content:
                            current_operation["content"] = "\n".join(current_content)
                        operations.append(current_operation)
                        current_content = []

                    parts = line.split(", ", maxsplit=1)
                    operation, line_info = parts[0], parts[1] if len(parts) > 1 else ""

                    if "-" in line_info:  # Multi-line range
                        start, end = map(int, line_info.split("-"))
                        current_operation = {
                            "operation": "replace",
                            "line": (start, end),
                        }
                    else:
                        line_number = int(line_info)
                        if operation == "REPLACE":
                            current_operation = {
                                "operation": "replace",
                                "line": line_number,
                            }
                        elif operation == "ADD":
                            current_operation = {
                                "operation": "add",
                                "after_line": line_number,
                            }
                        elif operation == "DELETE":
                            current_operation = {
                                "operation": "delete",
                                "line": line_number,
                            }
                else:
                    current_content.append(line)
            except Exception as e:
                pass

        if current_operation:  # Save the last operation
            if current_content:
                current_operation["content"] = "\n".join(current_content)
            operations.append(current_operation)

        return operations
    except Exception as e:
        return []


@register_tool(tags=["structured_editing"])
def edit_content(action_context, instructions: str, content: str) -> str:
    line_edits, reasoning = propose_multiline_edits(
        action_context, instructions, content
    )
    edits = parse_multiline_edit_operations(line_edits)
    modified_text = apply_multiline_edit_operations(content, edits)
    return modified_text


def edit_content_to_achieve_goal(
    action_context, instructions: str, content: str, oracle_instructions=None
) -> str:
    qa_checks = prompt_llm_with_messages(
        action_context=action_context,
        prompt=[
            {
                "role": "system",
                "content": oracle_instructions
                or "Your goal is to look at the INPUT and the user's INSTRUCTIONS. And provide a detailed step-by-step set of instructions to check if the goals of the INSTRUCTIONS have been met.",
            },
            {"role": "user", "content": "INPUT:\n" + content},
            {"role": "user", "content": "INSTRUCTIONS:\n" + instructions},
        ],
    )

    def oracle(original_text, modified_text, reasoning):
        return qa_check(
            action_context=action_context,
            qa_criteria=qa_checks,
            thing_to_check=modified_text,
        )

    return edit_content_with_oracle(action_context, instructions, content, oracle)


def edit_content_with_oracle(
    action_context,
    instructions: str,
    content: str,
    oracle: Callable[[str, str, str], Tuple[bool, str]],
    max_tries=3,
) -> str:
    modified_text = content

    print("=========================")
    print("Editing Content to Achieve Goal")
    print(modified_text)
    print("=========================")

    for _ in range(max_tries):
        line_edits, reasoning = propose_multiline_edits(
            action_context, instructions, modified_text
        )

        print("=========================")
        print(f"Reasoning: {reasoning}")
        print(f"Line Edits: {line_edits}")
        print("=========================")

        edits = parse_multiline_edit_operations(line_edits)
        modified_text = apply_multiline_edit_operations(modified_text, edits)

        print("=========================")
        print("Updated Content:")
        print(modified_text)
        print("=========================")

        passed, feedback = oracle(content, modified_text, reasoning)
        if passed:
            return modified_text
        else:
            print("=========================")
            print(f"Feedback: {feedback}")
            print("=========================")

        instructions = feedback

    return modified_text


def propose_multiline_edits(
    action_context, instructions: str, content: str
) -> Tuple[str, str]:
    line_edits_system_prompt = """
### **Task: Generate Precise Line-Based Edits in a Structured Format**

You will receive a **large text document** and a set of **proposed edits**. Your task is to translate these edits into a **structured, minimal format** that concisely represents the required modifications.

**Key Constraint:**  
The format allows significantly **more input than output**, so your response must **only include edit instructions**, **not** the full document.

---

### **Format Specification**
Use the following structured edit format:

1. **REPLACE, line-range**  
   - Replaces the specified line(s) with new content.
   - Example (single-line replacement):
     ```plaintext
     REPLACE, 12
     The cat is enormous.
     ```
   - Example (multi-line replacement):
     ```plaintext
     REPLACE, 20-22
     This method has significant performance issues.
     The new implementation improves efficiency.
     Additional optimizations have been applied.
     ```
   - **Interpretation:** The given line(s) are completely replaced with the provided content.

2. **REPLACE, line-range** *(without content)*  
   - Deletes the specified line(s).
   - Example:
     ```plaintext
     REPLACE, 45
     ```
   - **Interpretation:** Line **45** is **deleted**.

3. **ADD, after-line**  
   - Inserts new content **after** the specified line.
   - Example (single-line addition):
     ```plaintext
     ADD, 100
     The new algorithm improves efficiency by 20%.
     ```
   - Example (multi-line addition):
     ```plaintext
     ADD, 30
     This section explains the new functionality.
     It introduces key improvements over the previous approach.
     Performance benchmarks show significant gains.
     ```
   - **Interpretation:** The new content is inserted **after** the specified line.

---

### **Examples**

#### **Input Document (Excerpt)**
```plaintext
10. The cat is big.
11. It sits on the mat.
12. This method is inefficient.
13. Performance should be improved.
14. The dog is small.
15. Some algorithms perform better than others.
...
20. Outdated implementation details.
21. These steps are no longer necessary.
22. The previous algorithm was slower.
...
30. Section header for improvements.
```

#### **Edits Example**
```output
REPLACE, 10
The cat is enormous.

REPLACE, 12
This method has a performance bottleneck.

REPLACE, 14

REPLACE, 20-22
This method has significant performance issues.
The new implementation improves efficiency.
Additional optimizations have been applied.

ADD, 16
The new algorithm improves efficiency by 20%.

ADD, 30
This section explains the new functionality.
It introduces key improvements over the previous approach.
Performance benchmarks show significant gains.
```

---

### **Guidelines**
- **Do NOT** include the original document in your output.  
- **Do NOT** output unchanged lines.  
- **Do NOT** add extra explanations or comments.  
- Ensure edits are **precise, minimal, and formatted exactly as specified**.  
- Your response must be enclosed within a ```output markdown block.  

Your output must be placed in a ```output markdown block.
    """

    content = add_line_numbers(content)

    changes_needed = prompt_llm_with_messages(
        action_context=action_context,
        prompt=[
            {
                "role": "system",
                "content": "Your goal is to carefully look at the INPUT and to stop and think step by step about how you can improve it per the user's INSTRUCTIONS. Provide a detailed step-by-step set of instructions and reference line numbers.",
            },
            {"role": "user", "content": "INPUT:\n" + content},
            {"role": "user", "content": "INSTRUCTIONS:\n" + instructions},
        ],
    )

    # Escape triple backticks for markdown
    escaped_content = content.replace("```", "\\`\\`\\`")

    line_edits = prompt_llm_with_messages(
        action_context=action_context,
        prompt=[
            {"role": "system", "content": line_edits_system_prompt},
            {"role": "user", "content": "```input\n" + escaped_content + "\n```"},
            {"role": "user", "content": changes_needed},
        ],
    )

    line_edits = extract_markdown_block(line_edits, "output")

    return line_edits, changes_needed


# Sample usage:
#
#
# from agent.prompt import generate_response
#
# action_context = {
#     'llm': generate_response
# }
#
# text = """
# import json
# from typing import Dict, Any, List
# from agent.prompt import Prompt
# from agent.tool import register_tool
#
# @register_tool()
# def suggest_tree_edits(action_context, tree: Dict[str, Any]) -> List[Dict[str, Any]]:
#     \"\"\"
# Given a hierarchical tree structure, propose edits such as replacing content,
# inserting new nodes, or deleting nodes.
# \"\"\"
#
# prompt = {
#     "role": "system",
#     "content": \"\"\"You are an expert document editor. Your task is to suggest structured edits
# to improve the clarity, consistency, and organization of the provided document tree.
#
# Background information about the document structure, context, or prior changes may be
# included to help guide the suggested edits.
#
# **Tree Structure:**
# Each node has an `id`, `content`, and optional `children`.
#
# **Edit Operations:**
# - REPLACE: Change the `content` of a node while keeping its ID and children.
# - INSERT: Add a new child node at a given position within a parent's `children`.
# - DELETE: Remove a node entirely.
#
# **Indexing Rules:**
# - Indexing for INSERT is 0-based within `children`.
# - If index > length of children, append to the end.
#
# **Examples:**
# Input Tree:
# {"id": "1", "content": "Introduction", "children": [{"id": "2", "content": "Background"}]}
#
# Possible Edits:
# [
#     {"op": "replace", "id": "1", "newContent": "Introductory Section"},
#     {"op": "insert", "parentId": "1", "index": 1, "child": {"id": "3", "content": "New Topic"}},
#     {"op": "delete", "id": "2"}
# ]
# \"\"\"
# }
#
# return [prompt]
# """
#
# instructions = """
# I would like to be able to take in a parameter with the background information and have it
# included in the prompt as well.
# """

# updated_text = edit_content_to_achieve_goal(action_context, instructions, text)
# print(updated_text)


from agent.tools.prompt_tools import prompt_llm_with_messages


# from agent.prompt import generate_response
#
# action_context = {
#     'llm': generate_response
# }
#
# outline = build_outline(action_context,
#                         "Explain how to write code to create an AWS lambda function that can execute arbitrary Python code provided as a string. The Python code should be able to important and use a wide variety of libraries that may not fit in a standard AWS Lambda layer."
#                         "The outline must have at least 8 sections, each with at least 10 items per section. "
#                         "The sections must be cohesive and hang together. "
#                         "Key themes must be maintained throughout the outline. "
#                         "The outline must be detailed and comprehensive. "
#                         "The outline must be well-organized and structured. "
#                         "The outline must use cohesive and consistent examples, language, terminology, and formatting. "
#                         "Make sure that as you edit that you maintain correct section numbering. "
#                         "The outline must have consistent / correct section numbering and all sections must be numbered.")
