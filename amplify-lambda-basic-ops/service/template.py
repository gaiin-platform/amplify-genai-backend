import os
from datetime import datetime

import yaml

from flow.steps import parse_workflow

workflow = """
id: sample_workflow
steps:
  - id: initial_prompt
    prompt: "What is the main topic you'd like to explore today?"
    output:
      topic: str - the main topic of interest
      subtopics: list - potential subtopics to explore

  - id: subtopic_exploration
    steps:
      - prompt: "Tell me more about the subtopic: {{item}}"
        output:
          summary: str - a brief summary of the subtopic
        map:
          input: initial_prompt.subtopics
    
  - id: summary_compilation
    prompt: "Summarize the following information about {{initial_prompt.topic}}:\n{{subtopic_exploration}}"
    output:
      final_summary: str - a comprehensive summary of the topic and subtopics

  - id: sentiment_analysis
    prompt: "Analyze the sentiment of the following summary:\n{{summary_compilation.final_summary}}"
    output:
      sentiment: str - positive, negative, or neutral
      confidence: float - confidence score of the sentiment analysis

  - id: next_action
    prompt: "Based on the {{sentiment_analysis.sentiment}} sentiment (confidence: {{sentiment_analysis.confidence}}), what should be our next action?"
    output:
      action: str - recommended next action
      reason: str - explanation for the recommended action

  - id: action_router
    input: next_action.action
    route:
        "Explore Further": initial_prompt
        "Conclude Research": conclusion
        "Seek Expert Opinion": expert_consultation

  - id: conclusion
    prompt: "Provide a conclusion for our exploration of {{initial_prompt.topic}}, considering the sentiment is {{sentiment_analysis.sentiment}}."
    output:
      conclusion: str - final thoughts on the topic

  - id: expert_consultation
    prompt: "Formulate a question for an expert about {{initial_prompt.topic}}, focusing on areas where the sentiment was {{sentiment_analysis.sentiment}}."
    output:
      expert_question: str - question to ask an expert
"""


workflow2 = """
id: sample_workflow
steps:
  - id: initial_prompt
    prompt: Think of roles within a university. 
    output:
      roles: 
        - "str - role description in one sentence as a list item"
        - "yet another role description in one sentence as a list item"
        - "..."
      
  - id: create_tasks
    input: initial_prompt.roles
    map: "List three pain points for this role: {{item}}"    
    output: 
        pain_points: 
          - "str - pain point description in one sentence"
          - "another pain point description in one sentence"
          - "...yet another pain point description in one sentence"

  - id: subtopic_exploration
    steps:
      - prompt: "Tell me more about the subtopic: {{create_tasks.pain_points[0]}}"
        output:
          summary: str - a brief summary of the subtopic
    
  - id: summary_compilation
    prompt: "Summarize the following information about {{initial_prompt.topic}}:\n{{create_tasks}}"
    output:
      final_summary: str - a comprehensive summary of the topic and subtopics

  - id: sentiment_analysis
    prompt: "Analyze the sentiment of the following summary:\n{{summary_compilation.final_summary}}"
    output:
      sentiment: str - positive, negative, or neutral
      confidence: float - confidence score of the sentiment analysis
"""

workflow_document = """

id: blog_post
steps:
    - id: outline
      prompt: "Write an outline with two sections for a blog post on the topic of Generative AI."
      output:
        sections: 
          - str - some section as a string
          - another section as a string
          - ...
    - id: write_sections
      input: outline.sections
      include_previous_items: true
      include_previous_items_as: "previous_sections"
      
      map: | 
        Existing Writing:
        --------
        {{previous_sections}}
        --------
        
        You use very simple, direct language when you write. You use lots of concrete examples, metaphors,
        and analogies. You never use language like fantastic, awesome, incredible, or amazing. You never use
        language like terrible, horrible, awful, or disgusting. You never use language like good, bad, better,
        or worse. You never use language like great, excellent, or outstanding. You avoid using complicated
        or fancy words. 
        
        Write the next section on: {{item}}
        
        Output your result in the format below:
        
        Section Title: <Insert Title>
        
        <Insert a segue way paragraph that picks up on examples and ideas in the last few paragraphs of the prior section>
        
        <Insert 5-8 paragraphs of content that continue from this segue way>
        
      output:
        section_title: str - the title of the section you were asked to write
        section: str - the section you were asked to write that is at least five paragraphs as a | string in yaml
        
    - id: format_sections
      input: write_sections
      join: "\\n"
      format: |
        # {{item.section_title}}
            
        {{item.section}}
        
"""


needs_analysis = """

id: blog_post
steps:
    - id: outline
      prompt: "Write an outline with two sections for a blog post on the topic of Generative AI."
      output:
        sections: 
          - str - some section as a string
          - another section as a string
          - ...
    - id: write_sections
      input: outline.sections
      include_previous_items: true
      include_previous_items_as: "previous_sections"
      
      map: | 
        Existing Writing:
        --------
        {{previous_sections}}
        --------
        
        You use very simple, direct language when you write. You use lots of concrete examples, metaphors,
        and analogies. You never use language like fantastic, awesome, incredible, or amazing. You never use
        language like terrible, horrible, awful, or disgusting. You never use language like good, bad, better,
        or worse. You never use language like great, excellent, or outstanding. You avoid using complicated
        or fancy words. 
        
        Write the next section on: {{item}}
        
        Output your result in the format below:
        
        Section Title: <Insert Title>
        
        <Insert a segue way paragraph that picks up on examples and ideas in the last few paragraphs of the prior section>
        
        <Insert 5-8 paragraphs of content that continue from this segue way>
        
      output:
        section_title: str - the title of the section you were asked to write
        section: str - the section you were asked to write that is at least five paragraphs as a | string in yaml
        
    - id: format_sections
      input: write_sections
      join: "\\n"
      format: |
        # {{item.section_title}}
            
        {{item.section}}
        
"""


template_dir = os.getenv('TEMPLATE_DIR', 'templates')
output_dir = os.getenv('OUTPUT_DIR', f"{template_dir}/output")
template_file = "needs_analysis.yml"
template_doc = f"{template_dir}/{template_file}"
result_key = os.getenv('RESULT_KEY', None)
timestamp = datetime.now().strftime("%m-%d-%Y_%H-%M-%S")
target_output_file = f"{output_dir}/{template_file}-{timestamp}.md"
target_output_exec_log = f"{output_dir}/{template_file}-exec-log-{timestamp}.yml"
steps = parse_workflow(template_doc)

result = steps.run({}, {'debug': True})
output = result

if result_key:
    output = result.get(result_key, result)

if not isinstance(output, str):
    output = yaml.dump(output)

os.makedirs(output_dir, exist_ok=True)

with open(target_output_file, 'w') as file:
    file.write(output)

with open(target_output_exec_log, 'w') as file:
    file.write(yaml.dump(result))

print("Job complete. Output saved to: ", target_output_file)