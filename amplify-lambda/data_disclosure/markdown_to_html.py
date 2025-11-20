"""
Lightweight markdown to HTML converter for data disclosure documents.
No external dependencies - pure Python implementation.
"""

import re


def markdown_to_html(markdown_text):
    """
    Convert markdown text to HTML with data disclosure styling.
    
    Args:
        markdown_text (str): Markdown content to convert
        
    Returns:
        str: Complete HTML document with embedded CSS
    """
    if not markdown_text:
        return ""
    
    # Split into lines for processing
    lines = markdown_text.split('\n')
    html_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Headers (# ## ### etc.)
        if line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            if level <= 6:
                header_text = line.lstrip('#').strip()
                header_text = _process_inline_formatting(header_text)
                html_lines.append(f'<h{level} class="MsoNormal" style="text-align:justify"><b>{header_text}</b></h{level}>')
                i += 1
                continue
        
        # Empty lines
        if not line.strip():
            html_lines.append('')
            i += 1
            continue
        
        # Regular paragraphs
        paragraph_lines = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith('#'):
            paragraph_lines.append(lines[i])
            i += 1
        
        if paragraph_lines:
            paragraph_text = ' '.join(paragraph_lines)
            paragraph_text = _process_inline_formatting(paragraph_text)
            html_lines.append(f'<p class="MsoNormal" style="text-align:justify">{paragraph_text}</p>')
    
    # Join all HTML content
    html_content = '\n'.join(html_lines)
    
    # Wrap in complete HTML document with data disclosure styling
    complete_html = f"""
<html>

<head>
<meta http-equiv=Content-Type content="text/html; charset=utf-8">
<meta name=Generator content="Microsoft Word 15 (filtered)">
<style>
@font-face {{
    font-family:"Cambria Math";
    panose-1:2 4 5 3 5 4 6 3 2 4;
}}
@font-face {{
    font-family:Aptos;
    panose-1:2 11 0 4 2 2 2 2 2 4;
}}
p.MsoNormal, li.MsoNormal, div.MsoNormal {{
    margin-top:0in;
    margin-right:0in;
    margin-bottom:8.0pt;
    margin-left:0in;
    line-height:115%;
    font-size:12.0pt;
    font-family:"Times New Roman",serif;
}}
h1.MsoNormal, h2.MsoNormal, h3.MsoNormal, h4.MsoNormal, h5.MsoNormal, h6.MsoNormal {{
    margin-top:12.0pt;
    margin-right:0in;
    margin-bottom:8.0pt;
    margin-left:0in;
    line-height:115%;
    font-size:14.0pt;
    font-family:"Times New Roman",serif;
}}
a:link, span.MsoHyperlink {{
    color:#467886;
    text-decoration:underline;
}}
.MsoChpDefault {{
    font-family:"Aptos",sans-serif;
}}
@page WordSection1 {{
    size:8.5in 11.0in;
    margin:1.0in 1.0in 1.0in 1.0in;
}}
div.WordSection1 {{
    page:WordSection1;
}}
</style>

</head>

<body lang=EN-US link="#467886" vlink="#96607D" style='word-wrap:break-word'>

<div class=WordSection1>
{html_content}
</div>

</body>

</html>
"""
    return complete_html


def _process_inline_formatting(text):
    """
    Process inline markdown formatting like bold, italic, and links.
    
    Args:
        text (str): Text to process
        
    Returns:
        str: Text with HTML formatting
    """
    # Bold text (**text** or __text__)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text)
    
    # Italic text (*text* or _text_)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
    
    # Links [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    
    # Code inline `code`
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    
    return text


def simple_markdown_to_paragraphs(markdown_text):
    """
    Simple converter that just splits markdown into paragraphs without full HTML wrapper.
    Useful for extracting just the content part.
    
    Args:
        markdown_text (str): Markdown content to convert
        
    Returns:
        str: HTML paragraphs with MsoNormal styling
    """
    if not markdown_text:
        return ""
    
    # Split by double newlines to get paragraphs
    paragraphs = markdown_text.split('\n\n')
    html_paragraphs = []
    
    for paragraph in paragraphs:
        if paragraph.strip():
            # Remove any markdown formatting and convert to simple text
            clean_text = paragraph.strip()
            clean_text = _process_inline_formatting(clean_text)
            
            # Handle headers
            if clean_text.startswith('#'):
                level = len(clean_text) - len(clean_text.lstrip('#'))
                if level <= 6:
                    header_text = clean_text.lstrip('#').strip()
                    html_paragraphs.append(f'<h{level} class="MsoNormal" style="text-align:justify"><b>{header_text}</b></h{level}>')
                else:
                    html_paragraphs.append(f'<p class="MsoNormal" style="text-align:justify">{clean_text}</p>')
            else:
                html_paragraphs.append(f'<p class="MsoNormal" style="text-align:justify">{clean_text}</p>')
    
    return '\n'.join(html_paragraphs)