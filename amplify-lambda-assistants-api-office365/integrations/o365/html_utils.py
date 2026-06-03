import re


def html_to_plain_text(html: str) -> str:
    """
    Strip HTML tags and decode common entities to produce clean plain text.

    - Removes <style>/<script> blocks entirely (including their content)
    - Replaces block-level tags with newlines so paragraph structure survives
    - Strips all remaining tags
    - Decodes common HTML entities (&nbsp;, &amp;, etc.)
    - Collapses excessive whitespace / blank lines
    """
    html = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<(br|p|div|tr|li|h[1-6])\b[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    html = html.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<') \
               .replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
    html = re.sub(r'\n[ \t]+', '\n', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()
