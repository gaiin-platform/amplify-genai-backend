import os
import re
from functools import wraps
from typing import Callable, Any, Dict, Type

import dspy
import yaml
from dspy.datasets.gsm8k import GSM8K, gsm8k_metric
from dspy.teleprompt import BootstrapFewShot
from dspy.evaluate import Evaluate
from pydantic import BaseModel, Field

import llm.chat
from llm.chat import prompt
from llm.amplifyllm import Amplify
from typing import Callable, Any, get_type_hints
from functools import wraps
from pydantic import BaseModel
from inspect import signature

# Set up the LM.
turbo = dspy.OpenAI(model='gpt-3.5-turbo-instruct', max_tokens=1000)
# get the chaturl from the environment variable
chat_url = os.getenv('AMPLIFY_CHAT_URL')
access_token = os.getenv('AMPLIFY_TOKEN')
amplify = Amplify(model='gpt-4-1106-Preview', chat_url=chat_url, access_token=access_token, max_tokens=1000)
dspy.settings.configure(lm=amplify)
#dspy.settings.configure(lm=turbo)


class PromptInput(BaseModel):
    task: str = Field(description="The task to generate a prompt template for.")

class PromptTemplateOutput(BaseModel):
    prompt_template: str = Field(description="The template for a useful prompt.")


@prompt(system_prompt="Follow the instructions very carefully.")
def prompt_generator(task: PromptInput) -> PromptTemplateOutput:
    """
    These are the bounds that we are going to place on how we use LLM in the workplace:
    We are going to use the following framework in exploring how to use Generative AI to aid people:
    1. Better decision making by having the LLM give them multiple possible approaches to solving a problem,
    multiple potential interpretations of data, identifying assumptions in their decisions and helping them
    evaluate the validity of those assumptions, often by challenging them.
    2. Coming up with innovative ideas by serving as a brainstorming partner that offers lots of
    different and diverse options for any task.
    3. Simultaneously applying multiple structured approaches to representing and solving problems.
    4. Allowing people to iterate faster and spend more time exploring possibilities by creating initial
    drafts that are good starting points.
    5. Aiding in summarization, drafting of plans, identification of
    supporting quotations or evidence, identification of assumptions, in 3-5 pages of text. Provide one
    approach to using ChatGPT to perform the following and one specific prompt that would be used for this.
    6. Extracting structured information from unstructured text by having the LLM extract and reformat
    the information into a new structured format.

    First, think about the inputs that the user would need to provide to the prompt to make sure it has
    the relevant outside information as context.

    Make sure and include placeholders like {{INSERT TEXT}} (insert actual newlines) if the prompt relies on
    outside information, etc. If the prompt relies on a lot of information (e.g., more than a sentence or two),
    separate it like this:
    ----------------
    {{INSERT TEXT}}
    ----------------
    Be thoughtful and detailed in creating a really useful prompt that can be reused and are very detailed.
    If there is a specific domain that the prompt is for, make sure to include a detailed "Act as ..." with
    a detailed description of the role that the LLM is supposed to take on.

    You may ask for AT MOST 3-4 pieces of information. Everything else must be inferred by the LLM.

    If you are creating a specific format in markdown for the LLM to fill in, you can leave placeholders for
    the LLM to fill in with the format <Insert XYZ>. For example, you might have a template like:
    ## <Insert First Quiz Question>
    | Question | Answer |
    |----------|--------|
    | <Insert Question 1> | <Insert Answer 1> |
    | <Insert Question 2> | <Insert Answer 2> |
    ----------------
    You could also have a template like:
    <Facts>
    {{Numbered List of Facts}}
    </Facts>
    ## Summary
    <Insert Summary of Facts with markdown Footnotes Supporting Each Sentence>
    ## Footnotes
    <Insert Footnotes with each fact from the Facts>

    Create an extremely useful prompt for an LLM based on the specified task.
    """
    pass

@dspy.predictor
def prompt_role_assignment(prompt_template: str) -> str:
  """
    The prompt_template contains <PLACEHOLDERS>.
    The user provides some information and the LLM infers other information from what the user provided.
    We want the LLM to infer as much information as possible. We want the user to provide at most 2-4 pieces
    of information.
    If absolutely necessary, you can have the user provide more information, but try to keep it to 2-4 pieces
    of information.
    Think carefully, which of the placeholders are information that the user needs to fill in that
    there is no possible way the LLM could infer from the values that are provided earlier by the user?
    Which of the placeholders are information that the LLM could infer from the values that are
    provided earlier by the user? For the information that the user ABSOLUTELY MUST fill in, update
    the placeholder to <USER: PLACEHOLDER>. If the LLM can infer it, update the placeholder
    to <LLM: PLACEHOLDER>. To make the template useful, only require the user to fill in
    information that is essential and let the LLM infer as much as possible.
    HAVE THE LLM INFER AS MUCH AS POSSIBLE.

    YOU CAN USE A MAXIMUM OF THREE <USER: PLACEHOLDER>. If you need more than THREE <USER: PLACEHOLDER>s, you
    should just include a section where the user can provide whatever information they want and you infer the rest.


    For example, if the user provides a topic, the LLM can infer sub topics, etc. If the user provides
    a title, the LLM can infer descriptions, etc.

    Create an updated prompt_template based on these rules.
  """


class YesNo(dspy.Module):
    def __init__(self):
        super().__init__()
        self.prog = dspy.ChainOfThought("question -> yes_or_no_lowercase__or_na")

    def forward(self, question):
        return self.prog(question=question)


class ProgramAnswer(dspy.Module):
    def __init__(self):
        super().__init__()
        self.prog = dspy.ProgramOfThought("question -> answer", max_iters=3, import_white_list=["json"])

    def forward(self, question):
        return self.prog(question=question)

class CoT(dspy.Module):
    def __init__(self):
        super().__init__()
        self.prog = dspy.ChainOfThought("question -> answer")

    def forward(self, question):
        return self.prog(question=question)


# Set up the optimizer: we want to "bootstrap" (i.e., self-generate) 4-shot examples of our CoT program.
# config = dict(max_bootstrapped_demos=4, max_labeled_demos=4)
#
# teleprompter = BootstrapFewShot(metric=gsm8k_metric, **config)
# optimized_cot = teleprompter.compile(CoT(), trainset=gsm8k_trainset)
#
# evaluate = Evaluate(devset=gsm8k_devset, metric=gsm8k_metric, num_threads=4, display_progress=True, display_table=0)
#
# evaluate(optimized_cot)
#
# optimized_cot.save("optimized_cot.json")
#
# turbo.inspect_history(n=1)
#
# f = YesNo()
# print(f("How bis is the su"))

idea = PromptInput(task="Create a prompt to write user stories for a given software feature.")
template = prompt_generator(task=idea, model="gpt-4o", access_token=access_token)
print(template.prompt_template)