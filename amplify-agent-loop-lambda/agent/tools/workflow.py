import traceback
from typing import List

from agent.components.tool import register_tool
from agent.components.util import resolve_references
from agent.core import ActionContext


@register_tool(tags=["workflow"])
def execute_workflow(
    agent, action_context: ActionContext, workflow: List[dict]
) -> dict:
    """
    Execute a workflow of actions.

    Parameters:
        workflow (Dict): A dictionary containing a sequence of actions to execute like this:

        [
            {
                "stepName": "someVarNameToStoreTheResultTo",
                "tool": "first_tool_name",
                "args": {
                    "arg1": "value1",
                    "arg2": "value2"
                    ...
                }
            },
            {
                "stepName": "someOtherVarNameToStoreTheResultTo",
                "tool": "next_tool_name",
                "args": {
                    "arg1": {"ref": "someVarNameToStoreTheResultTo"},
                    ...
                }
            }
            ...as many tools as needed...
        ]

    Let's say that the first tool returns a dictionary with the key "someKey" and the value 1234.
    The input to the second tool would be:
    {
        "arg1": {"someKey": 1234}
    }

    You can nest references in lists and dictionaries like this:
    {
        "arg1": [
            {"ref": "someVarName"},
            "some string",
            {"key": {"ref": "someOtherVarName"}}
        ]
    }

    You can use a ref inside of a string in the python format method like this:
    {
        "arg1": "The value of someKey is $#someVarNameToStoreTheResultTo"
    }

    Remember: A step cannot access the result of a step that comes before it in the workflow unless you
    reference it in the args. If you prompt the LLM and don't reference something that you want it to
    analyze from a prior step, it won't know about it.

    Any code that you execute with exec_code can access the results of the workflow directly
    by the variable name that you assigned to the result of the action. For CODE, ALWAYS refer
    to the results of the workflow by reference through the variable name and don't insert the value into the code
    string.

    Example:
        {
                "stepName": "countCharacters",
                "tool": "exec_code",
                "args": {
                    "code": "result = len(generateRecipe)"
                }
        }

    You can also access the tools you are provided directly in the code. Let's assume you have tools called "prompt_llm"
    and "get_web_page_text".
    {
        "stepName": "writeJokeAboutWebPage",
        "tool": "exec_code",
        "args": {
            "code": "page_content = get_web_page_text('https://vanderbilt.edu')\nresult = prompt_llm('Please provide a joke about:'+page_content)",
        }
    }

    CRITICAL: Make sure that all strings in your JSON or python code are properly escaped.
    Make sure that multi-line strings within your code use escaped triple quotes.

    CRITICAL:!!! In the workflow JSON make sure that the strings have all newlines and quotes escaped.

    Returns:
        List: The list of results of the actions in the workflow.
    """
    try:
        environment = action_context.get_environment()
        actions = action_context.get_action_registry()
        results = {}
        results_full = {}
        count = 0

        # Simplify the results to just the result value
        simplified_results = {k: v.get("result", v) for k, v in results.items()}

        send_event = action_context.incremental_event()
        send_event(
            "workflow/start", {"workflow": workflow, "context": simplified_results}
        )

        for action in workflow:
            count += 1

            action_def = actions.get_action(action["tool"])
            step_name = action.get("stepName", action["tool"] + str(count))

            # Process the args and replace any refs with the actual value
            args = action["args"]

            send_event(
                "workflow/step/start",
                {
                    "workflow": workflow,
                    "count": count,
                    "total": len(workflow),
                    "step": step_name,
                    "action": action,
                    "action_def": action_def,
                    "context": simplified_results,
                },
            )

            resolved_args = resolve_references(args, {**simplified_results, **results})

            send_event(
                "workflow/step/resolved_args",
                {
                    "workflow": workflow,
                    "step": step_name,
                    "action": action,
                    "action_def": action_def,
                    "context": simplified_results,
                    "args": resolved_args,
                },
            )

            action_context.properties["code_exec_context"] = {
                # This allows code execution to reference the results of previous steps directly
                **results,
                # This allows code execution to reference the actions in the action registry directly
                **{
                    k: v.function
                    for k, v in action_context.get_action_registry().actions.items()
                },
            }

            try:

                result = environment.execute_action(
                    agent, action_context, action_def, resolved_args
                )
                results_full[step_name] = result

                send_event(
                    "workflow/step/end",
                    {
                        "workflow": workflow,
                        "step": step_name,
                        "action": action,
                        "action_def": action_def,
                        "context": simplified_results,
                        "args": resolved_args,
                        "result": result,
                    },
                )

                results[step_name] = result.get("result", result)
            except Exception as e:

                # Convert the traceback to a string and send it as an event
                error_trace = traceback.format_exc()

                send_event(
                    "workflow/step/error",
                    {
                        "workflow": workflow,
                        "step": step_name,
                        "action": action,
                        "action_def": action_def,
                        "context": simplified_results,
                        "args": resolved_args,
                        "traceback": error_trace,
                        "error": str(e),
                    },
                )

                traceback.print_exc()
                print(f"Error executing action {action['tool']}: {str(e)}")
                results_full[step_name] = {"error": str(e)}
                results[step_name] = str(e)

    except Exception as e:
        # print a detailed stack trace
        traceback.print_exc()
        print(f"Error setting up workflow for execution: {str(e)}")
        raise e

    action_context.send_event(
        "workflow/end", {"workflow": workflow, "results": results_full}
    )

    return results
