import os
import threading
import traceback
from datetime import datetime
import argparse
from datetime import datetime
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich import print as rprint
import yaml

from flow.steps import parse_workflow, Format

#
#  Sample Workflows
#
#
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


def main():
    parser = argparse.ArgumentParser(
        description="Run workflow template and save output."
    )
    parser.add_argument(
        "--template-dir",
        default=os.getenv("TEMPLATE_DIR", "templates"),
        help="Directory containing templates (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for output files (default: <template_dir>/output)",
    )
    parser.add_argument("--template-file", required=True, help="Template file name")
    parser.add_argument(
        "--result-key",
        default=os.getenv("RESULT_KEY", None),
        help="Specific result key to output (default: entire result)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--existing-output", help="Path to existing output file to reformat"
    )
    parser.add_argument(
        "--format-file", help="Path to format file for reformatting existing output"
    )

    args = parser.parse_args()

    console = Console()

    # Set up directories and file paths
    template_dir = args.template_dir
    output_dir = args.output_dir or f"{template_dir}/output"
    template_file = args.template_file
    template_doc = os.path.join(template_dir, template_file)

    timestamp = datetime.now().strftime("%m-%d-%Y_%H-%M-%S")

    if args.existing_output and args.format_file:
        # Reformat existing output
        existing_output_name = os.path.basename(args.existing_output)
        target_output_file = os.path.join(
            output_dir, f"{os.path.splitext(existing_output_name)[0]}.md"
        )
    else:
        # Regular workflow execution
        target_output_file = os.path.join(output_dir, f"{template_file}-{timestamp}.md")
        target_output_trace_log = os.path.join(
            output_dir, f"{template_file}-trace-log-{timestamp}.yml"
        )
        target_output_exec_log = os.path.join(
            output_dir, f"{template_file}-exec-log-{timestamp}.yml"
        )

    if args.existing_output and args.format_file:
        # Load existing output
        try:
            eoutput = os.path.join(output_dir, args.existing_output)
            with open(eoutput, "r") as file:
                existing_output = yaml.safe_load(file)
        except Exception as e:
            console.print(
                f"[bold red]Error loading existing output:[/bold red] {str(e)}"
            )
            return

        # Load format file
        try:
            format_resolved = os.path.join(template_dir, args.format_file)
            with open(format_resolved, "r") as file:
                format_data = yaml.safe_load(file)
        except Exception as e:
            console.print(f"[bold red]Error loading format file:[/bold red] {str(e)}")
            return

        # Create Format step
        format_step = Format("format_output", format_data)

        # Run Format step
        try:
            result = format_step.run(existing_output)
        except Exception as e:
            console.print(f"[bold red]Error formatting output:[/bold red] {str(e)}")
            return

        # Save formatted output
        try:
            with open(target_output_file, "w") as file:
                file.write(result)
        except Exception as e:
            console.print(
                f"[bold red]Error saving formatted output:[/bold red] {str(e)}"
            )
            return

        console.print(
            Panel.fit(
                f"[bold green]Reformatting complete![/bold green]\n\n"
                f"Formatted output saved to: [cyan]{target_output_file}[/cyan]",
                title="Output Reformatting Summary",
                border_style="green",
            )
        )

    else:

        # Parse workflow
        try:
            with console.status("[bold green]Parsing workflow...") as status:
                steps = parse_workflow(template_doc)
            console.print("[bold green]✓[/bold green] Workflow parsed successfully")
        except Exception as e:
            console.print(f"[bold red]Error parsing workflow:[/bold red] {str(e)}")
            return

        # Run workflow
        try:
            with Progress() as progress:
                task = progress.add_task("[green]Running workflow...", total=100)

                def progress_callback(percent):
                    progress.update(task, completed=percent)

                trace = []
                trace_lock = threading.Lock()

                # Define a custom representer for multiline strings
                def str_presenter(dumper, data):
                    if isinstance(data, str) and "\n" in data:
                        return dumper.represent_scalar(
                            "tag:yaml.org,2002:str", data, style="|"
                        )
                    return dumper.represent_scalar("tag:yaml.org,2002:str", data)

                # Add the custom representer to the YAML Dumper
                yaml.add_representer(str, str_presenter, Dumper=yaml.Dumper)

                # Disable aliases to prevent the use of references
                yaml.Dumper.ignore_aliases = lambda self, data: True

                def recording_tracer(id, tag, data, log_file="trace_log.yaml"):

                    if args.debug:
                        logdata = data
                        if isinstance(data, dict):
                            logdata = next(
                                (
                                    data[key].keys()
                                    for key in ["result", "context"]
                                    if key in data
                                ),
                                data,
                            )
                        elif isinstance(data, list):
                            logdata = f"list[{len(data)}]"
                        print(f"--- Step {id}: {tag} - {logdata}")
                    with trace_lock:
                        trace.append({"id": id, "tag": tag, "data": data})

                        # Write the complete trace to the log file in YAML format
                        with open(target_output_trace_log, "w") as f:
                            yaml.dump(
                                trace, f, default_flow_style=False, Dumper=yaml.Dumper
                            )

                try:
                    result = steps.exec(
                        {},
                        {
                            "tracer": recording_tracer,
                            "progress_callback": progress_callback,
                        },
                    )
                except Exception as e:
                    # print a detailed stack trace
                    print(f"--- ERROR @ Step {steps.id}")
                    traceback.print_exc()

                    console.print(
                        f"[bold red]Error running workflow:[/bold red] {str(e)}"
                    )
                    return

        except Exception as e:
            # print a detailed stack trace
            traceback.print_exc()

            console.print(f"[bold red]Error running workflow:[/bold red] {str(e)}")
            return

        # Process output
        output = result
        if args.result_key:
            output = result.get(args.result_key, result)
            if output is result:
                console.print(
                    f"[yellow]Warning:[/yellow] Result key '{args.result_key}' not found. Using full result."
                )

        if not isinstance(output, str):
            output = yaml.dump(output, default_flow_style=False, Dumper=yaml.Dumper)

        # Save output
        try:
            os.makedirs(output_dir, exist_ok=True)
            with open(target_output_file, "w") as file:
                file.write(output)
            with open(target_output_exec_log, "w") as file:
                yaml.dump(result, file, default_flow_style=False, Dumper=yaml.Dumper)
        except Exception as e:
            console.print(f"[bold red]Error saving output:[/bold red] {str(e)}")
            return

        # Print completion message
        console.print(
            Panel.fit(
                f"[bold green]Job complete![/bold green]\n\n"
                f"Output saved to: [cyan]{target_output_file}[/cyan]\n"
                f"Execution log saved to: [cyan]{target_output_exec_log}[/cyan]",
                title="Workflow Execution Summary",
                border_style="green",
            )
        )


if __name__ == "__main__":
    main()
