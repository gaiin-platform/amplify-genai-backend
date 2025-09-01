from typing import Dict, List

import requests

from agent.components.tool import register_tool


@register_tool(tags=["http_requests"])
def send_http_request(
    url: str, headers: Dict, method: str = "GET", body: str = None
) -> Dict:
    """
    Make an HTTP request to the provided URL with the given headers, method, and body.

    Parameters:
        url (str): The URL to request.
        headers (Dict): The headers to include in the request.
        method (str, optional): The HTTP method to use. Defaults to "GET".
        body (str, optional): The body to include in the request. Defaults to None.

    Returns:
        Dict: A dictionary containing the status code, headers, and body of the response.
    """
    response = requests.request(method, url, headers=headers, data=body)
    return {
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": response.text,
    }


@register_tool(tags=["web_browser"])
def get_web_page_text(url: str) -> str:
    """
    Get the text content of a web page at the provided URL.

    Parameters:
        url (str): The URL of the web page.

    Returns:
        str: The text content of the web page.
    """
    response = requests.get(url)

    # Use beautifulsoup to extract text content from HTML
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response.text, "html.parser")
    text_content = soup.get_text()

    return text_content
