
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import sys

from openai import OpenAI
import yaml
import argparse
import boto3
import os
import re
import concurrent.futures

MARKET_CATEGORIES_DYNAMO_TABLE='amplify-support-dev-market-categories'

parser = argparse.ArgumentParser(description='Load a YAML taxonomy into a database.')
parser.add_argument('filename', type=str, help='The YAML file containing the taxonomy.')
parser.add_argument('profile', type=str, help='The AWS profile to use.')
args = parser.parse_args()

print(f"Using profile {args.profile}")
boto3.setup_default_session(profile_name="vandy-amplify")

def get_secret_value(secret_name):
    # Create a Secrets Manager client
    client = boto3.client('secretsmanager')

    try:
        # Retrieve the secret value
        response = client.get_secret_value(SecretId=secret_name)
        secret_value = response['SecretString']
        return secret_value

    except Exception as e:
        raise ValueError(f"Failed to retrieve secret '{secret_name}': {str(e)}")

def get_openai_client():
    openai_api_key = get_secret_value("OPENAI_API_KEY")
    client = OpenAI(
        api_key=openai_api_key
    )
    return client

def prompt_llm(client, system, user):
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user}
    ]

    result = ""
    response = client.chat.completions.create(
        model="gpt-4-1106-preview",
        messages=messages
    )

    return response.choices[0].message.content


prompt_idea_system_prompt_v1 = """
These are the bounds that we are going to place on how we use ChatGPT in the workplace: We are going to use the following framework in exploring how to use Generative AI to aid people: 1. Better decision making by having the LLM give them multiple possible approaches to solving a problem, multiple potential interpretations of data, identifying assumptions in their decisions and helping them evaluate the validity of those assumptions, often by challenging them. 2. Coming up with innovative ideas by serving as a brainstorming partner that offers lots of different and diverse options for any task. 3. Simultaneously applying multiple structured approaches to representing and solving problems. 4. Allowing people to iterate faster and spend more time exploring possibilities by creating initial drafts that are good starting points. 5. Aiding in summarization, drafting of plans, identification of supporting quotations or evidence, identification of assumptions, in 3-5 pages of text. Provide one approach to using ChatGPT to perform the following and one specific prompt that would be used for this. Make sure and include placeholders like <INSERT TEXT> (insert actual newlines) if the prompt relies on outside information, etc. If the prompt relies on a lot of information (e.g., more than a sentence or two), separate it like this:
----------------
<INSERT TEXT>
----------------
Be thoughtful and detailed in creating a really useful prompt that can be reused and are very detailed.

Output the prompt in a ```template code block.
"""

def extract_prompt_template(prompt):
    pattern = r"```template\s*\n(.*?)```"
    matches = re.findall(pattern, prompt, re.DOTALL)
    if matches:
        return matches[0]
    else:
        return None

def generate_prompt_idea(client, task):
    system = """
These are the bounds that we are going to place on how we use ChatGPT in the workplace:
We are going to use the following framework in exploring how to use Generative AI to aid people:
1. Better decision making by having the LLM give them multiple possible approaches to solving a problem,
multiple potential interpretations of data, identifying assumptions in their decisions and helping them
evaluate the validity of those assumptions, often by challenging them.
2. Helping to draft written content, such as emails, memos, reports, code, outlines, etc.
3. Coming up with innovative ideas by serving as a brainstorming partner that offers lots of different
and diverse options for any task. Spotting ambiguities and helping to resolve them. 
4. Simultaneously applying multiple structured approaches to representing and solving problems.
5. Allowing people to iterate faster and spend more time exploring possibilities by creating initial
drafts that are good starting points.
6. Aiding in summarization, drafting of plans, identification of supporting quotations or evidence,
identification of assumptions, in 3-5 pages of text. Provide one approach to using ChatGPT to perform
the following and one specific prompt that would be used for this.
           
Make sure and include placeholders like <INSERT TEXT> (insert actual newlines) if the prompt relies on 
outside information, etc. If the prompt relies on a lot of information (e.g., more than a sentence or two), 
separate it like this:
----------------
<INSERT TEXT>
----------------
Be thoughtful and detailed in creating a really useful prompt that can be reused and are very detailed. 

Do not suggest anything that requires current knowledge, such as recent news or articles, non-historical 
people, references to specific papers or cases, specific numbers that you the LLM won't have access to, etc.

Output the prompt in a ```template code block.
"""

    user = f"""The task is: {task}

```template

"""
    return prompt_llm(client, system, user)


def assign_roles_to_prompt_inputs(client, prompt):
    system = """
        The prompt template below contains <PLACEHOLDERS>. 
        The user provides some information and the LLM infers other information from what the user provided.
        We want the LLM to infer as much information as possible. We want the user to provide at most 2-4 pieces of information.
        If absolutely necessary, you can have the user provide more information, but try to keep it to 2-4 pieces of information.
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

Output the updated content in a ```template code block.
"""

    user = f"""The prompt template is: 

```template
{prompt}
```
   
The updated prompt template is: 
```template

"""
    return prompt_llm(client, system, user)


def describe_prompt(client, prompt):
    system = """
You will be provided a prompt template for a prompt to be sent to an LLM. The information that the user provides is denoted with <USER: PLACEHOLDER>.
What the LLM will infer and output is denoted with <LLM: PLACEHOLDER>. In 2-3 sentences, describe what the user will
provide and what the LLM will infer and output. The output of the LLM should always be described as a "draft" that
that should be double checked by the user and serve as a starting point for the user to iterate on.
"""

    user = f"""The prompt template is: 
```template
{prompt}
```

The 2-3 sentences describing it are:

"""
    return prompt_llm(client, system, user)


def name_prompt(client, description):
    system = """
Based on the provided description, provide a 3-7 word title for the item.
"""

    user = f"""The description is: 
```template
{description}
```

The 3-7 word title describing it in a ```template code block are:
```template
"""
    name = prompt_llm(client, system, user)

    # check if name includes ```template
    if "```template" in name:
        name = extract_prompt_template(name)

    return name
    # return prompt_llm(client, system, user)

def tag_prompt(client, description):
    system = """
Based on the provided description, provide a 3-5 tags for the item as a comma separated list.
"""

    user = f"""The description is: 
```template
{description}
```

The 3-5 tags in a ```template code block are:
```template
"""

    tags = prompt_llm(client, system, user)
    tags = extract_prompt_template(tags)
    tags = tags.split(",")
    return [tag.strip() for tag in tags]


def templatize_user_tags(text):
    # Define a pattern that matches "<USER: ...>" and captures the content inside
    pattern = re.compile(r'<USER:\s*([^>]*)>', re.IGNORECASE)

    # Replacement function that formats the match as "{{...}}"
    def replacement(match):
        # Retrieve the matched group (the content within "<USER: ...>")
        content = match.group(1).strip()
        # Return the content formatted inside double curly braces
        return f"{{{{{content}}}}}"

    # Perform the substitution and return the result
    return pattern.sub(replacement, text)

def generate_idea(client, task):

    try:
        print(f"Generating idea for: {task}")

        result = generate_prompt_idea(client, task)
        result = extract_prompt_template(result)
        result = assign_roles_to_prompt_inputs(client, result)
        result = extract_prompt_template(result)

        templatized = templatize_user_tags(result)
        description = describe_prompt(client, result)
        name = name_prompt(client, description)
        tags = tag_prompt(client, description)

        debug = False
        if debug:
            print("================ Name ==============")
            print(name)

            print("================ Tags ==============")
            print(tags)

            print("================ Description ==============")
            print(description)

            print("================ Prompt Template ==============")
            print(templatized)


        print(f"Idea generated for: {task}")

        return {
            "name": name,
            "tags": tags,
            "description": description,
            "prompt": templatized
        }
    except Exception as e:
        # Try again
        print(f"An error occurred while generating an idea: {e}")
        print("Trying again...")
        return generate_idea(client, task)


def top_n_support_ideas(client, ideas, n):
    try:
        ideas_str = "\n".join(ideas)

        result = prompt_llm(client,
                            f"""
Think about these ideas. Which ones are going to provide the most value to university faculty, staff, and students?  

Imagine being a person encountering a document with these ideas. Which ones are going to save you the most time and 
help you the most? Make sure that your list is not duplicative. 
                            """,
                            f"""
Think about these ideas. Which ones are going to provide the most value to university faculty, staff, and students? 
Which ones will really save people time? Which ones are the ones that are really important and could dramatically
improve things?

The {n} most important should be in a ```template code block. 

Ideas to Evaluate:
-------------
{ideas_str}
-------------

<WHAT WILL DETERMINE WHAT IS MOST IMPORTANT>

```template
<INSERT {n} MOST IMPORTANT>
```
                   """
                            )
        ideas_str = extract_prompt_template(result)
        return ideas_str.strip().split("\n")
    except Exception as e:
        # Try again
        print(f"An error occurred while generating 10 ideas: {e}")
        print("Trying again...")
        return top_n_ideas(client, ideas, n)

def top_n_ideas(client, ideas, n):
    try:
        ideas_str = "\n".join(ideas)

        result = prompt_llm(client,
                            f"""
Think about these ideas. Which ones are going to provide the most value to university faculty, staff, and students
in terms of helping to create a great document. What are the most important points, structures, formats, things to
include, etc. 

Imagine being a person encountering a document with these ideas. Which ones are going to save you the most time and 
help you most to use the document? Make sure that your list is not duplicative. 
                            """,
                            f"""
Think about these ideas. Which ones are going to provide the most value to university faculty, staff, and students? 
Which ones will really save people time? Which ones are the ones that are really important and could dramatically
improve the quality of the document?

The {n} most important should be in a ```template code block. 

Ideas to Evaluate:
-------------
{ideas_str}
-------------

<WHAT WILL DETERMINE WHAT IS MOST IMPORTANT>

```template
<INSERT {n} MOST IMPORTANT>
```
                   """
                            )
        ideas_str = extract_prompt_template(result)
        return ideas_str.strip().split("\n")
    except Exception as e:
        # Try again
        print(f"An error occurred while generating 10 ideas: {e}")
        print("Trying again...")
        return top_n_ideas(client, ideas, n)


def generate_10_support_ideas(client, task, notes):
    try:
        result = prompt_llm(client,
                            f"""
                            These are the bounds that we are going to place on how we use ChatGPT in the workplace:
                            We are going to use the following framework in exploring how to use Generative AI to aid people:
                            1. Better decision making by having the LLM give them multiple possible approaches to solving a problem,
                            multiple potential interpretations of data, identifying assumptions in their decisions and helping them
                            evaluate the validity of those assumptions, often by challenging them.
                            2. Helping to draft written content, such as emails, memos, reports, code, outlines, etc.
                            3. Coming up with innovative ideas by serving as a brainstorming partner that offers lots of different
                            and diverse options for any task.
                            4. Simultaneously applying multiple structured approaches to representing and solving problems.
                            5. Allowing people to iterate faster and spend more time exploring possibilities by creating initial
                            drafts that are good starting points.
                            6. Aiding in summarization, drafting of plans, identification of supporting quotations or evidence,
                            identification of assumptions, in 3-5 pages of text. Provide one approach to using ChatGPT to perform
                            the following and one specific prompt that would be used for this.
    
                            {notes} 
    
                            I am going to give you types of documents that would be used in a university. 
                            Whatever I give you, generate 10 1-sentence descriptions tasks, supporting documents,
                            ideas, automations, analyses, etc. Things like transforming lecture notes into
                            a quiz or study topic checklist. Giving feedback on an essay based on a grading rubric.
                            Giving an interactive quiz or game based on contents in the document. Comparing and
                            contrasting two documents. 
                            
                            ChatGPT can only output text. Your idea must be focused on outputting text, like the result
                            of ChatGPT performing an analysis, writing a document, generating a small amount of code,
                            giving feedback, rewriting, editing, etc. ChatGPT can generate vega-lite visualizations.
                            ChatGPT can generate mermaid diagrams. Focus on ideas that are based on ChatGPT generating
                            text either to communicate information, write all / some of a document, generate a small
                            amount of code, etc.
                             
                            Each sentence should be a complete idea a supporting task, analysis, or document for the original document..
                            
                            LIST EACH SENTENCE ON A SEPARATE LINE in a ```template code block.
                            """,
                            f"""
                   Generate 10 ideas for: {task}
                   
                   The 10 ideas on separate lines in a ```template code block are:
                   ```template
                   
                   """
                            )
        ideas_str = extract_prompt_template(result)
        return ideas_str.strip().split("\n")
    except Exception as e:
        # Try again
        print(f"An error occurred while generating 10 ideas: {e}")
        print("Trying again...")
        return generate_10_ideas(client, task, notes)

def generate_10_ideas(client, task, notes):
    try:
        result = prompt_llm(client,
                            f"""
                            These are the bounds that we are going to place on how we use ChatGPT in the workplace:
                            We are going to use the following framework in exploring how to use Generative AI to aid people:
                            1. Better decision making by having the LLM give them multiple possible approaches to solving a problem,
                            multiple potential interpretations of data, identifying assumptions in their decisions and helping them
                            evaluate the validity of those assumptions, often by challenging them.
                            2. Helping to draft written content, such as emails, memos, reports, code, outlines, etc.
                            3. Coming up with innovative ideas by serving as a brainstorming partner that offers lots of different
                            and diverse options for any task.
                            4. Simultaneously applying multiple structured approaches to representing and solving problems.
                            5. Allowing people to iterate faster and spend more time exploring possibilities by creating initial
                            drafts that are good starting points.
                            6. Aiding in summarization, drafting of plans, identification of supporting quotations or evidence,
                            identification of assumptions, in 3-5 pages of text. Provide one approach to using ChatGPT to perform
                            the following and one specific prompt that would be used for this.
    
                            {notes} 
    
                            I am going to give you types of documents that would be used in a university. 
                            Whatever I give you, generate 10 1-sentence descriptions for how to make an incredible
                            vesion of this document that is really well structured, easy to understand, and helps the
                            reader. The ideas should create a really great document that is beautiful, insightful, 
                            easy to understand, and helps the reader.
                             
                            Each sentence should be a complete idea describing a way to make the document better.
                            
                            LIST EACH SENTENCE ON A SEPARATE LINE in a ```template code block.
                            """,
                            f"""
                   Generate 10 ideas for: {task}
                   
                   The 10 ideas on separate lines in a ```template code block are:
                   ```template
                   
                   """
                            )
        ideas_str = extract_prompt_template(result)
        return ideas_str.strip().split("\n")
    except Exception as e:
        # Try again
        print(f"An error occurred while generating 10 ideas: {e}")
        print("Trying again...")
        return generate_10_ideas(client, task, notes)


def generate_10_document_creation_ideas(client, task, notes):
    try:
        result = prompt_llm(client,
                            f"""
                            These are the bounds that we are going to place on how we use ChatGPT in the workplace:
                            We are going to use the following framework in exploring how to use Generative AI to aid people:
                            1. Better decision making by having the LLM give them multiple possible approaches to solving a problem,
                            multiple potential interpretations of data, identifying assumptions in their decisions and helping them
                            evaluate the validity of those assumptions, often by challenging them.
                            2. Helping to draft written content, such as emails, memos, reports, code, outlines, etc.
                            3. Coming up with innovative ideas by serving as a brainstorming partner that offers lots of different
                            and diverse options for any task.
                            4. Simultaneously applying multiple structured approaches to representing and solving problems.
                            5. Allowing people to iterate faster and spend more time exploring possibilities by creating initial
                            drafts that are good starting points.
                            6. Aiding in summarization, drafting of plans, identification of supporting quotations or evidence,
                            identification of assumptions, in 3-5 pages of text. Provide one approach to using ChatGPT to perform
                            the following and one specific prompt that would be used for this.
    
                            {notes} 
    
                            Whatever I give you, generate 10 1-sentence variations on wording describing step by step how to create a great
                            vesion of this document that is really well structured, easy to understand, and helps the
                            reader. Each sentence should describe the complete document creation task, such as
                            "Create an expense report that has separate categories for meals, lodging, entertainment,
                            and transportation." Each sentence must be a variation on creating a complete document.
                             
                            Each sentence should be a complete idea describing how to create the document.
                            
                            LIST EACH SENTENCE ON A SEPARATE LINE in a ```template code block.
                            """,
                            f"""
                   Generate 10 task descriptions for producing the document: {task}
                   
                   The 10 task descriptions on separate lines in a ```template code block are:
                   ```template
                   
                   """
                            )
        ideas_str = extract_prompt_template(result)
        return ideas_str.strip().split("\n")
    except Exception as e:
        # Try again
        print(f"An error occurred while generating 10 ideas: {e}")
        print("Trying again...")
        return generate_10_document_creation_ideas(client, task, notes)


def to_valid_yaml_key(s):
    # Strip leading and trailing whitespace
    s = s.strip()

    # Replace spaces with underscores or hyphens
    s = re.sub(r'\s+', '_', s)

    # Remove any characters that are invalid for YAML keys
    s = re.sub(r'[^\w\s-]', '', s)

    # If the resulting string is potentially ambiguous or empty,
    # it can be enclosed in quotes to make it a valid YAML key
    if not s or re.match(r'^[0-9-]', s) or ':' in s or re.search(r'\s', s):
        s = f'"{s}"'

    return s


import concurrent.futures

def do_in_parallel_with_timeout(fn, args_list, timeout, retries=5):
    """
    Function to execute `fn` with each list of arguments from `args_list` within the specified `timeout`.
    Each function execution has individual retries in case it does not complete within `timeout`.

    Args:
    fn -- Function to be executed
    args_list -- List of argument lists to be passed to fn
    timeout -- Time in seconds before the execution is cancelled
    retries -- Optional; Number of retries in case of timeout (default: 5)

    Returns:
    results -- List of results of fn(*args) for each args in args_list
    """
    # Inner function: executes a single task with retries
    def exec_task_with_retries(args):
        for i in range(retries):
            try:
                future = executor.submit(fn, *args)
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                print(f"Execution timeout for task '{fn.__name__}' with args {args}, retrying... ({i+1}/{retries})")
            except Exception as e:
                print(f"An error occurred with task '{fn.__name__}' with args {args}: {e}")
        return None

    # Use the inner function on each item in args_list
    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = list(executor.map(exec_task_with_retries, args_list))

    return results


client = get_openai_client()
use_cached = True
create_new_ideas = True
rank_ideas = True
save_updated = True
rerank = True

def construct_path_and_load(parent_path, category_name, category_content):
    print(f"Constructing path and loading for: {parent_path} / {category_name}")
    filename = './categories/'+category_name+'.yaml'

    # If the file exists, read the content and return it
    if os.path.exists(filename) and use_cached:
        with open(filename, 'r') as f:
            cached_content = yaml.safe_load(f)
            category_content['ideas'] = cached_content.get('ideas', [])
            category_content['top_ideas'] = cached_content.get('top_ideas', [])


    if isinstance(category_content, dict):
        description = category_content.get('description', '')
        tags = category_content.get('tags', [])

        # Process any subcategories.
        for subcategory_name, subcategory_content in category_content.items():
            if subcategory_name in ['description', 'tags', 'ideas', 'top_ideas']:
                continue
            subcategory_path = f"{parent_path}/{convert_to_snake_case(subcategory_name)}"
            category_content[subcategory_name] = construct_path_and_load(subcategory_path, subcategory_name, subcategory_content)

        sub_categories = [(k, v) for k, v in category_content.items() if k not in ['description', 'tags', 'ideas', 'top_ideas']]

        if len(sub_categories) == 0:
            readable_name = ' '.join(word.capitalize() for word in category_name.split('_'))
            task = parent_path + " / " + readable_name + " - " + description


            if not use_cached or (create_new_ideas and len(category_content.get('ideas', [])) == 0):
                print(f"Creating ideas for: {task}")

                document_tasks = do_in_parallel_with_timeout(
                    generate_10_document_creation_ideas,
                    [(client, task, "Be extremely creative, think about the document from all angles, and think about how the document could be optimally structured."),
                     (client, task, "Think about all of the pieces of information that should be in the document and make sure and have one idea that is related to the core information it should have."),
                     (client, task, "Think of analyses, feedback, critiques, alternative approaches, problem solving, critical thinking, that could make the document incredibly insightful.")
                     ],
                    30
                )

                document_tasks = [element for sublist in document_tasks for element in sublist]

                #print(f"Created document tasks: {document_tasks}")

                document_qualities = do_in_parallel_with_timeout(
                    generate_10_ideas,
                    [(client, task, "Be extremely creative, think about the document from all angles, and think about how the document could be optimally structured."),
                        # (client, task, "Think about all of the pieces of information that should be in the document and make sure and have one idea that is related to the core information it should have."),
                        # (client, task, "Think of analyses, feedback, critiques, alternative approaches, problem solving, critical thinking, that could make the document incredibly insightful.")
                     ],
                    30
                )

                document_qualities = [element for sublist in document_qualities for element in sublist]
                document_task = f"Generate a \"{task}\" with the following characteristics: " + " ".join(document_qualities);

                #print(f"Created document task: {document_task}")

                task_ideas = do_in_parallel_with_timeout(
                    generate_10_support_ideas,
                    [
                        (client, task, "Think one, two, and three steps ahead of this document. What else will be needed to support, analyze, enhance, improve, do something with it, etc.?"),
                        # (client, task, "Think of additional supplementary documents, code, emails, memos, reports, etc. that could be generated to go along with the document and make it better."),
                    ], 30)

                task_ideas = [element for sublist in task_ideas for element in sublist]

                items = document_tasks + [document_task] + task_ideas

                print("Updating ideas and top_ideas...")
                category_content['ideas'] = items
                category_content['id'] = category_name
                category_content['path'] = parent_path

            if save_updated:
                with open(filename, 'w') as f:
                    yaml.dump(category_content, f)
                    print(f"Saved ideas for: {task}")
            else:
                yaml.dump(category_content, sys.stdout)

    return category_content

# Helper function to convert strings into snake_case for consistent path names.
def convert_to_snake_case(text):
    return text.lower().replace(' ', '_').replace('-', '_')

# Set up command-line argument parsing.


# Load the YAML file specified as a command-line argument.
with open(args.filename, 'r') as stream:
    try:
        taxonomy = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        print(exc)
        exit()

for main_category_name, main_category_content in taxonomy["Marketplace_Taxonomy"].items():
    main_category_path = '/' + convert_to_snake_case(main_category_name)
    taxonomy["Marketplace_Taxonomy"][main_category_name] = construct_path_and_load(main_category_path, main_category_name, main_category_content)

# Dump new YAML file
with open('new_file.yaml', 'w') as f:
    yaml.dump(taxonomy, f)