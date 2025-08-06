import copy
import os
from pprint import pformat
from typing import Dict, List, Any, Tuple
import requests
import yaml
import concurrent.futures
from typing import Dict, List
import copy
from flow.spec import validate_output_spec
from flow.util import (
    get_root_key,
    find_template_vars,
    dynamic_prompt,
    resolve,
    fill_prompt_template,
    resolve_and_set,
)


class WorkflowValidationError(Exception):
    pass


class Step:
    def __init__(self, id: str, data: Dict):
        self.id = id
        self.data = data
        self.update_context = False

    def inputs(self):
        return []

    def get_tracer(self, options):
        tracer = options.get("tracer", lambda id, tag, data: None)
        return tracer

    def run(self, context: Dict, options={}) -> Dict:
        # Placeholder for actual step execution
        return {}

    def exec(self, context: Dict, options={}) -> Dict:
        try:
            self.get_tracer(options)(self.id, "start", {"context": context})
        except Exception as e:
            print(f"Failed to trace step {self.id}: {e}")
        output = self.run(context, options)
        try:
            self.get_tracer(options)(self.id, "end", {"result": output})
        except Exception as e:
            print(f"Failed to trace step {self.id}: {e}")
        return output

    def __str__(self):
        return f"{self.__class__.__name__}(id={self.id}, data={pformat(self.data)})"

    __repr__ = __str__


class Workflow(Step):
    def __init__(self, id: str, steps: List[Step], data: Dict):
        super().__init__(id, {})
        self.steps = steps
        self.output_key = data.get("output_key", None)
        self.context_data = data.get("context", {})

    def inputs(self):
        paths = []
        for step in self.steps:
            paths += step.inputs()
        return [get_root_key(path) for path in paths]

    def run(self, context: Dict, options={}) -> Dict:
        debug = options.get("debug", False)
        if debug and "tracer" not in options:
            options["tracer"] = lambda id, tag, data: print(f"--- Step {id}: {tag}")

        # merge self.context with context, letting context take priority
        context = {**self.context_data, **context}

        stop_controller = options.get("stop_controller", lambda: False)

        if debug:
            print(f"Context after merging additional data: {yaml.dump(context)}")

        total = len(self.steps)
        results = {}
        for idx, step in enumerate(self.steps):

            if stop_controller and stop_controller():
                results[step.id] = {"status": "stopped"}
                context.update(results)
                break

            if debug:
                print(f"Running step [{idx+1}/{total}] {step.id}")
            step_result = step.exec(context, options)
            if debug:
                print(f"Step {step.id} result: {yaml.dump(step_result)}")

            if not step.update_context:
                results[step.id] = step_result
            else:
                results = step_result

            context.update(results)

        if self.output_key:
            results = resolve(results, self.output_key)

        return results

    def __str__(self):
        steps_str = pformat(self.steps, indent=2)
        return f"Workflow(id={self.id}, steps=\n{steps_str})"


class Prompt(Step):
    def __init__(self, id: str, data: Dict):
        super().__init__(id, {})
        self.prompt = data["prompt"]
        self.strip_thought = data.get("strip_thought", True)
        self.system_prompt = data.get(
            "system_prompt",
            "Follow the user's instructions very carefully. "
            "Analyze the task or question and output the requested "
            "information.",
        )
        self.output = data.get("output", {})

        if len(self.output) > 0:
            is_valid_spec, message = validate_output_spec(self.output)
            if not is_valid_spec:
                raise WorkflowValidationError(
                    f"Invalid output specification for prompt step {id}: {message}"
                )

        if "prompt" not in data:
            raise WorkflowValidationError(f"Prompt step {id} must have 'prompt' field")

    def inputs(self):
        paths, _ = find_template_vars(self.prompt)
        spaths, _ = find_template_vars(self.system_prompt)
        # merge and deduplicate
        return [get_root_key(path) for path in list(set(paths + spaths))]

    def run(self, context: Dict, options={}) -> Dict:

        access_token = options.get("access_token", None)
        model = options.get("model", None)
        output_mode = options.get("output_mode", "yaml")

        result, data = dynamic_prompt(
            context,
            self.prompt,
            self.system_prompt,
            self.output,
            access_token,
            model,
            output_mode,
        )

        self.get_tracer(options)(self.id, "prompt_data", data)

        if self.strip_thought and "thought" in result:
            del result["thought"]

        return result

    def __str__(self):
        steps_str = pformat(self.steps, indent=2)
        return f"Prompt(id={self.id}, prompt={self.prompt}, system_prompt={self.system_prompt}, output={self.output})"


class FileSave(Step):
    def __init__(self, id: str, data: Dict):
        super().__init__(id, {})

        # Validate required fields
        if "path" not in data:
            raise WorkflowValidationError(f"FileSave step {id} must have 'path' field")
        if "content" not in data:
            raise WorkflowValidationError(
                f"FileSave step {id} must have 'content' field"
            )

        self.path = data["path"]
        self.content = data["content"]
        self.mode = data.get("mode", "w")  # Default to text write mode
        self.encoding = data.get("encoding", "utf-8")
        self.mkdir = data.get("mkdir", True)  # Create parent directories by default

        # Validate mode
        valid_modes = ["w", "wb", "a", "ab"]
        if self.mode not in valid_modes:
            raise WorkflowValidationError(
                f"Invalid mode '{self.mode}' for FileSave step {id}. Must be one of: {valid_modes}"
            )

    def inputs(self):
        # Find template variables in both path and content
        path_vars, _ = find_template_vars(self.path)
        content_vars, _ = find_template_vars(self.content)
        return [get_root_key(path) for path in list(set(path_vars + content_vars))]

    def run(self, context: Dict, options={}) -> Dict:
        # Resolve path and content using context
        resolved_path = fill_prompt_template(context, self.path)
        resolved_content = fill_prompt_template(context, self.content)

        # Create parent directories if needed
        if self.mkdir:
            os.makedirs(os.path.dirname(resolved_path), exist_ok=True)

        # Write the file
        try:
            if "b" in self.mode:  # Binary mode
                with open(resolved_path, self.mode) as f:
                    f.write(resolved_content)
            else:  # Text mode
                with open(resolved_path, self.mode, encoding=self.encoding) as f:
                    f.write(resolved_content)

            # Record the operation in trace
            self.get_tracer(options)(
                self.id,
                "file_save",
                {
                    "path": resolved_path,
                    "mode": self.mode,
                    "encoding": self.encoding,
                    "size": len(resolved_content),
                },
            )

            return {"path": resolved_path, "size": len(resolved_content)}

        except Exception as e:
            raise WorkflowValidationError(
                f"Failed to save file in step {self.id}: {str(e)}"
            )

    def __str__(self):
        return f"FileSave(id={self.id}, path={self.path}, mode={self.mode}, encoding={self.encoding})"


class Map(Step):
    def __init__(self, id: str, data: Dict):
        super().__init__(id, data)

        self.delegate = Prompt(f"map_prompt{id}", {**data, "prompt": data["map"]})
        self.split = data.get("split", None)
        self.number_splits = data.get("number_splits", True)
        self.number_prefix = data.get("number_prefix", "")
        self.number_suffix = data.get("number_suffix", ".")
        self.limit = data.get("limit", -1)
        self.item_key = data.get("item_key", "item")
        self.result_key = data.get("result_key", "result")
        self.strip_thought = data.get("strip_thought", True)
        self.merge_item = data.get("merge_item", True)
        self.enhance = data.get("enhance", False)
        self.update_context = self.enhance
        self.enhance_item = data.get("enhance_item", False)
        self.include_item = data.get("include_item", False)
        self.previous_item_key = data.get("include_previous_item_as", "Previous Item:")
        self.include_previous_item = data.get("include_previous", False)
        self.previous_items_key = data.get("include_previous_items_as", "Previous:")
        self.include_previous_items = data.get("include_previous_items", False)
        self.max_workers = data.get("max_workers", 5)

        if "map" not in data or "input" not in data:
            raise WorkflowValidationError(
                f"Map step {id} must have 'map' and 'input' fields"
            )

    def inputs(self):
        paths = self.delegate.inputs()
        paths = [get_root_key(path) for path in paths if not path.startswith("item")]
        paths.append(self.data["input"])
        return [get_root_key(path) for path in paths]

    def process_item(self, item, idx, context, options={}):
        item_context = copy.deepcopy(context)
        item_context["item"] = item
        item_context[self.item_key] = item
        item_context["index"] = idx

        item_result = self.delegate.exec(item_context, options)

        if (
            self.strip_thought
            and isinstance(item_result, dict)
            and "thought" in item_result
        ):
            del item_result["thought"]

        if isinstance(item, dict) and self.enhance_item:
            item[self.result_key] = item_result
            item_result = item
        if isinstance(item_result, dict) and isinstance(item, dict) and self.merge_item:
            item_result = {**item, **item_result}
        if (
            isinstance(item_result, dict)
            and not isinstance(item, dict)
            and self.merge_item
        ):
            item_result[self.item_key] = item
        if isinstance(item_result, dict) and self.include_item:
            item_result[self.item_key] = item

        if isinstance(item_result, list) and self.merge_item:
            item_result.append(item)
        if isinstance(item_result, list) and self.include_item:
            item_result = {self.item_key: item, self.result_key: item_result}

        return item_result

    def run(self, context: Dict, options={}) -> Dict:
        tracer = self.get_tracer(options)

        input_path = self.data["input"]
        tracer(self.id, "input", input_path)

        input_list = self.get_input_list(context, input_path)

        tracer(self.id, "input_list", input_list)

        results = None
        if not self.include_previous_items and not self.include_previous_item:
            results = self.run_parallel(context, input_list, options)
        else:
            results = self.run_sequential(context, input_list, options)

        if self.enhance:
            results_context = copy.deepcopy(context)
            resolve_and_set(results_context, input_path, results)
            results = results_context

        return results

    def run_sequential(self, context, input_list, options):
        tracer = self.get_tracer(options)

        results = []
        total = len(input_list)
        for idx, item in enumerate(input_list):
            tracer(self.id, f"[{idx+1}/{total}] mapitem_{idx}", item)

            if self.include_previous_items:
                context[self.previous_items_key] = results
            if self.include_previous_item and len(results) > 0:
                context[self.previous_item_key] = results[-1]

            item_result = self.process_item(item, idx, context, options)
            tracer(self.id, f"[{idx+1}/{total}] map_item_{idx}_result", item_result)
            results.append(item_result)

        return results

    def run_parallel(self, context, input_list, options):

        tracer = self.get_tracer(options)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as executor:
            future_to_item = {
                executor.submit(self.process_item, item, idx, context, options): (
                    item,
                    idx,
                )
                for idx, item in enumerate(input_list)
            }

            results = []
            total = len(input_list)
            for future in concurrent.futures.as_completed(future_to_item):
                item, idx = future_to_item[future]
                try:
                    item_result = future.result()
                    tracer(
                        self.id,
                        f"[{idx+1}/{total}] map_item_{idx}",
                        {"item": item, "result": item_result},
                    )
                    results.append(item_result)
                except Exception as exc:
                    print(
                        f"[{idx+1}/{total}] {self.id}: item {idx} generated an exception: {exc}"
                    )
        return results

    def get_input_list(self, context, input_path):
        input_list = resolve(context, input_path)
        if isinstance(input_list, str):
            if self.split:
                input_list = input_list.split(self.split)
                if self.number_splits:
                    input_list = [
                        f"{self.number_prefix}{i + 1}{self.number_suffix} {item}"
                        for i, item in enumerate(input_list)
                    ]
            else:
                input_list = [input_list]
        if not isinstance(input_list, list):
            raise ValueError(f"Input for Map step {self.id} must be a list")

        if self.limit > 0 and self.limit < len(input_list):
            # sublist of the list
            input_list = input_list[: self.limit]

        return input_list


class Files(Step):
    def __init__(self, id: str, data: Dict):
        super().__init__(id, data)
        if "input" not in data or "files" not in data:
            raise WorkflowValidationError(
                f"Files step {id} must have 'input' and 'files' fields"
            )

        self.input_path = data["input"]
        self.files = data["files"]
        self.content_key = data.get("content_key", None)

    def run(self, context: Dict, options={}) -> List[Dict]:
        input_data = resolve(context, self.input_path)

        if not isinstance(input_data, list):
            input_data = [input_data]

        created_files = self.save_files(context, input_data, options)

        return {"files": created_files}

    def save_files(self, context, items: List, options) -> List[Dict]:
        tracer = self.get_tracer(options)
        created_files = []

        for item in items:
            # Resolve the file path using the item context
            file_path = fill_prompt_template({**context, "item": item}, self.files)

            # Create the directory if it doesn't exist
            directory = os.path.dirname(file_path)
            os.makedirs(directory, exist_ok=True)

            # Log file creation
            tracer(self.id, "creating_file", {"file_name": file_path})

            def get_content(item):
                if self.content_key:
                    item = item[self.content_key]

                if not isinstance(item, str):
                    return yaml.dump(item)

                return item

            content = get_content(item)

            # Write the content to the file
            with open(file_path, "w") as file:
                file.write(
                    content
                )  # Assuming 'content' holds the text to save in the file

            # Log after file is written
            tracer(self.id, "file_written", {"file_name": file_path})

            # Append to created files list
            created_files.append({"item": item, "file": file_path})

        return created_files


class Format(Step):
    def __init__(self, id: str, data: Dict):
        super().__init__(id, data)
        if "input" not in data or "format" not in data:
            raise WorkflowValidationError(
                f"Format step {id} must have 'input' and 'template' fields"
            )

        self.input_path = data["input"]
        self.template = data["format"]
        self.join_str = data.get("join", "\n")  # Default join string is newline

    def run(self, context: Dict, options={}) -> Dict:
        input_data = resolve(context, self.input_path)

        if isinstance(input_data, list):
            formatted_items = self.format_list(input_data)
        else:
            formatted_items = self.format_list([input_data])

        result = self.join_str.join(formatted_items)
        return result

    def format_list(self, items: List) -> List[str]:
        formatted_items = []
        for item in items:
            item_context = {"item": item}
            formatted_item = fill_prompt_template(item_context, self.template)
            formatted_items.append(formatted_item)
        return formatted_items


class Reduce(Step):
    def __init__(self, id: str, data: Dict):
        super().__init__(id, data)
        if "input" not in data:
            raise WorkflowValidationError(
                f"Reduce step {id} must have 'input' and 'output' fields"
            )


class Route(Step):
    def __init__(self, id: str, data: Dict):
        super().__init__(id, data)
        if "input" not in data or "route" not in data:
            raise WorkflowValidationError(
                f"Route step {id} must have 'input' and 'route' fields"
            )


class If(Step):
    def __init__(self, id: str, data: Dict):
        super().__init__(id, data)
        if "condition" not in data or "then" not in data:
            raise WorkflowValidationError(
                f"If step {id} must have 'condition' and 'then' fields"
            )


class Action(Step):
    def __init__(self, id: str, data: Dict):
        super().__init__(id, data)
        if "action" not in data:
            raise WorkflowValidationError(f"Action step {id} must have 'action' field")


def create_step(step_data: Dict, step_id: str) -> Step:
    if "steps" in step_data:
        nested_steps = [
            create_step(s, f"{step_id}_{i}") for i, s in enumerate(step_data["steps"])
        ]
        return Workflow(step_id, nested_steps, step_data)
    elif "map" in step_data:
        return Map(step_id, step_data)
    elif "reduce" in step_data:
        return Reduce(step_id, step_data)
    elif "route" in step_data:
        return Route(step_id, step_data)
    elif "if" in step_data:
        return If(step_id, step_data)
    elif "action" in step_data:
        return Action(step_id, step_data)
    elif "prompt" in step_data:
        return Prompt(step_id, step_data)
    elif "format" in step_data:
        return Format(step_id, step_data)
    elif "files" in step_data:
        return Files(step_id, step_data)
    else:
        raise WorkflowValidationError(f"Unknown step type for step {step_id}")


def validate_workflow(workflow: Workflow, path: str = ""):
    if not isinstance(workflow, Workflow):
        raise WorkflowValidationError(
            f"{path}: Expected Workflow, got {type(workflow)}"
        )

    for i, step in enumerate(workflow.steps):
        step_path = f"{path}.steps[{i}]" if path else f"steps[{i}]"

        if not isinstance(step, (Step, Workflow, Map, Reduce, Route, If, Action)):
            raise WorkflowValidationError(
                f"{step_path}: Invalid step type {type(step)}"
            )

        if not step.id:
            raise WorkflowValidationError(f"{step_path}: Missing step id")

        if isinstance(step, Workflow):
            validate_workflow(step, step_path)


def import_from_uri(uri: str, base_dir: str = None) -> Any:
    if uri.startswith("http://") or uri.startswith("https://"):
        response = requests.get(uri)
        return yaml.safe_load(response.text)
    else:
        if base_dir and not os.path.isabs(uri):
            uri = os.path.join(base_dir, uri)
        if os.path.exists(uri):
            with open(uri, "r") as file:
                return yaml.safe_load(file)
        else:
            raise ValueError(f"Cannot import from URI: {uri}")


def process_context(data: Dict, base_dir: str = None):
    if "context" in data:
        for key, value in data["context"].items():
            if (
                isinstance(value, str)
                and value.startswith("import(")
                and value.endswith(")")
            ):
                uri = value[7:-1].strip()
                data["context"][key] = import_from_uri(uri, base_dir)


def process_workflow_data(data: Dict, base_dir: str = None):
    process_context(data, base_dir)
    if "steps" in data:
        for step in data["steps"]:
            if "steps" in step:  # This is a nested workflow
                process_workflow_data(step, base_dir)


def load_yaml(yaml_input: str) -> Tuple[Dict, str]:
    base_dir = None
    if os.path.isfile(yaml_input):
        with open(yaml_input, "r") as file:
            data = yaml.safe_load(file)
        base_dir = os.path.dirname(os.path.abspath(yaml_input))
    else:
        # Assume the input is a YAML string
        data = yaml.safe_load(yaml_input)
    return data, base_dir


def parse_workflow(yaml_input: str) -> "Workflow":
    try:
        # check if yaml_input is a dict
        if isinstance(yaml_input, dict):
            data = yaml_input
            base_dir = None
        else:
            data, base_dir = load_yaml(yaml_input)
    except yaml.YAMLError as e:
        raise WorkflowValidationError(f"Invalid YAML: {str(e)}")

    if not isinstance(data, dict):
        raise WorkflowValidationError("YAML root must be a dictionary")

    if "steps" not in data:
        raise WorkflowValidationError("Workflow must have 'steps' defined")

    # Process the entire workflow data recursively
    process_workflow_data(data, base_dir)

    workflow_id = data.get("id", "root_workflow")
    steps = []

    for i, step_data in enumerate(data["steps"]):
        if not isinstance(step_data, dict):
            raise WorkflowValidationError(f"Step {i} must be a dictionary")
        step_id = step_data.get("id", f"step{i}")
        step = create_step(step_data, step_id)
        steps.append(step)

    workflow = Workflow(workflow_id, steps, data)
    validate_workflow(workflow)
    return workflow
