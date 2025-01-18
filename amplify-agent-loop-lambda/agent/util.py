from typing import TypeVar, Iterable, Callable, Any, Optional, List
from functools import reduce

T = TypeVar('T')  # Type of value being reduced
C = TypeVar('C')  # Type of capability

def reduce_capabilities(
        capabilities: Iterable[C],
        method_name: str,
        initial_value: T,
        reverse: bool = False
) -> T:
    """
    Reduce a value through all capabilities that have the specified method.

    Args:
        capabilities: Iterable of capabilities
        method_name: Name of method to call on each capability
        initial_value: Starting value for reduction
        reverse: If True, process capabilities in reverse order

    Returns:
        Final value after processing through all applicable capabilities
    """
    def reducer(value: T, capability: C) -> T:
        if hasattr(capability, method_name):
            method = getattr(capability, method_name)
            return method(value)
        return value

    caps = reversed(list(capabilities)) if reverse else capabilities
    return reduce(reducer, caps, initial_value)


def reduce_capabilities_with_args(
        capabilities: Iterable[C],
        method_name: str,
        initial_value: T,
        *extra_args: Any,
        reverse: bool = False
) -> T:
    """
    Reduce a value through all capabilities that have the specified method,
    while supporting additional arguments to each capability method.

    Args:
        capabilities: Iterable of capabilities.
        method_name: Name of the method to call on each capability.
        initial_value: Starting value for reduction.
        *extra_args: Additional arguments to pass to each method.
        reverse: If True, process capabilities in reverse order.

    Returns:
        Final value after processing through all applicable capabilities.
    """
    def reducer(value: T, capability: C) -> T:
        if hasattr(capability, method_name):
            method = getattr(capability, method_name)
            return method(value, *extra_args)  # Pass the extra arguments
        return value

    caps = reversed(list(capabilities)) if reverse else capabilities
    return reduce(reducer, caps, initial_value)


def collect_from_capabilities(
        capabilities: Iterable[C],
        method_name: str,
        *args,
        **kwargs
) -> List[T]:
    """
    Collect and combine results from all capabilities that have the specified method.

    Args:
        capabilities: Iterable of capabilities
        method_name: Name of method to call on each capability
        *args: Positional arguments to pass to the method
        **kwargs: Keyword arguments to pass to the method

    Returns:
        List of all results concatenated together
    """
    results = []
    for cap in capabilities:
        if hasattr(cap, method_name):
            method_result = getattr(cap, method_name)(*args, **kwargs)
            # If the result is not iterable, wrap it in a list
            if isinstance(method_result, (list, tuple)):
                results.extend(method_result)
            else:
                results.append(method_result)
    return results


def extract_markdown_block(raw_text: str, block_type: str) -> Optional[str]:
    """
    Extract the content of a specific markdown block from the raw text.
    Searches for the ending marker from the back of the string.

    :param raw_text: The input text containing markdown blocks.
    :param block_type: The type of block to extract (e.g., 'action', 'json').
    :return: The content of the block if it exists, or None if it doesn't.
    """
    start_marker = f"```{block_type}"
    end_marker = "```"

    # Check if the block markers exist in the text
    if start_marker in raw_text and end_marker in raw_text:
        try:
            start_index = raw_text.find(start_marker) + len(start_marker)
            end_index = raw_text.rfind(end_marker)  # Search for the end marker from the back
            if start_index < end_index:  # Ensure valid positions
                return raw_text[start_index:end_index].strip()
        except Exception:
            # Optionally log or handle parsing errors here
            return None
    return None