import re


def markdown_to_html(text: str) -> str:
    """
    Convert a markdown-formatted string to basic HTML suitable for email bodies.
    Handles bold, italic, bullet lists, numbered lists, and line breaks.
    """
    lines = text.split('\n')
    html_lines = []
    in_ul = False
    in_ol = False

    for line in lines:
        stripped = line.rstrip()

        ul_match = re.match(r'^(\s*)[-*+] (.+)$', stripped)
        ol_match = re.match(r'^(\s*)\d+\. (.+)$', stripped)

        if ul_match:
            if in_ol:
                html_lines.append('</ol>')
                in_ol = False
            if not in_ul:
                html_lines.append('<ul>')
                in_ul = True
            content = _inline_markdown(ul_match.group(2))
            html_lines.append(f'<li>{content}</li>')
        elif ol_match:
            if in_ul:
                html_lines.append('</ul>')
                in_ul = False
            if not in_ol:
                html_lines.append('<ol>')
                in_ol = True
            content = _inline_markdown(ol_match.group(2))
            html_lines.append(f'<li>{content}</li>')
        else:
            if in_ul:
                html_lines.append('</ul>')
                in_ul = False
            if in_ol:
                html_lines.append('</ol>')
                in_ol = False

            heading = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            if heading:
                level = len(heading.group(1))
                content = _inline_markdown(heading.group(2))
                html_lines.append(f'<h{level}>{content}</h{level}>')
            elif stripped == '':
                html_lines.append('<br>')
            else:
                html_lines.append(_inline_markdown(stripped) + '<br>')

    if in_ul:
        html_lines.append('</ul>')
    if in_ol:
        html_lines.append('</ol>')

    return '\n'.join(html_lines)


def _inline_markdown(text: str) -> str:
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'_(.+?)_', r'<em>\1</em>', text)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    return text


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
