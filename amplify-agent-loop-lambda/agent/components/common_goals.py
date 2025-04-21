from agent.core import Goal

CODE_CAN_USE_TOOLS = Goal(
    name="Code Can Call Tools",
    description="""
You can also access the tools you are provided directly in code you write. Let's assume you have tools called "do_a"
and "do_b".
{
    "stepName": "doXYZ",
    "tool": "exec_code",
    "args": {
        "code": "page_content = do_a('https://vanderbilt.edu')\nresult = do_b('Please provide a joke about:'+page_content)",
    }
}

CRITICAL: Make sure that all strings in your JSON or python code are properly escaped.
Make sure that multi-line strings within your code use escaped triple quotes.

CRITICAL:!!! In the workflow JSON make sure that the strings have all newlines and quotes escaped.
"""
)


USE_RESULT_REFERENCES_IN_RESPONSES = Goal(
    name="Result References in Responses",
    description="""
For efficiency in your responses, you can refer to an existing result to avoid having to repeat the same information.
The reference to the result will be replaced with a nicely formatted version of the result when the response is 
shown to the user.

You do this by referring to the "id" of the action result in a special markdown block as shown below:

```result
$#someid
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
""")

PASS_RESULT_REFERENCES_TO_TOOLS = Goal(
    name="Result References",
    description="""
You can pass the results of actions to tools by reference. Actions cannot directly access the results of other actions.

Important!! Results are always passed around with $#someresultid format.

You can pass a previous result inside of a string in the python format method like this:
 ```action
{
    "tool": "some_tool_name",
    "args": {
        "someStr": "The name that was generated is: $#5 and the age is: $#2"
    }
}

These would be replaced with the value of the 'result' keys from the results with id 5 and the result with id 2.
    
You can nest references in lists and dictionaries like this:
 ```action
{
    "tool": "some_other_tool_name...",
    "args": {
        "arg1": [
            {"ref": "$#2"},
            "some string",
            {"key": {"ref": "$#1"}}
        ]
    }
}
```
    
This allows you to pass structured data as well.
"""
)

PREFER_WORKFLOWS = Goal(
    name="Prefer Workflows",
    description="""
Important! Prefer workflows when possible.

If there is reasonable certainty about what to do and the order of steps, you should try to generate and
execute a workflow to accomplish the task. If it fails, then you can fall back to incrementally 
accomplishing the task.
"""
)

LARGE_RESULTS = Goal(
    name="Large results",
    description="""
Some results may be too big to show you in their entirety. If you don't need to see the entire result, 
just make sure it was created. It will be shown to the user even if you can't see it completely. 
"""
)

STOP_WHEN_STUCK = Goal(
    name="Large results",
    description="""
Do your best to solve problems. However, if there is absolutely no path forward, then tell the user that you
are stuck and stop.
"""
)

BAIL_OUT_ON_MANY_ERRORS = Goal(
    name="Large results",
    description="""
If the same action causes an error more than two times, either stop or trying something else.
"""
)

BE_DIRECT = Goal(
    name="Be direct",
    description=""""
                For reasoning tasks, such as summarization, inference, planning, classification, writing, etc., you should
                perform the task directly after acquiring the necessary information.
                """
)

CAREFUL_ARGUMENT_SELECTION = Goal(
    name="Argument selection",
    description="PAY CLOSE ATTENTION to ALL available arguments for each tool. CAREFULLY consider which arguments apply in the current context and would be beneficial to include. ALWAYS strive to be as COMPLETE and THOROUGH as possible when providing values for arguments. Leaving a field blank is only when 100% confident that the arg is not needed."
)
