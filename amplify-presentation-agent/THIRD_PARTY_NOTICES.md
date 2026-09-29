# Third-party notices

The presentation agent adapts ideas, prompts and schemas from the projects below.
No source files were copied verbatim; adapted portions are marked in module docstrings.

## aws-samples/sample-strands-agent-with-agentcore

- Source: https://github.com/aws-samples/sample-strands-agent-with-agentcore (commit `4c1933d9`)
- Used in: `agent/presentation_agent/plan.py` (slide plan schema), `prompts.py`
  (design, workflow and QA guidance from `skills/powerpoint-presentations/*.md`),
  `render.py` (LibreOffice/poppler preview approach), `agent/Dockerfile`.
- Not used: `src/builtin_tools/lib/pptx_engine.py` and `pptxgenjs.md`, whose headers
  state they were ported from a separately licensed pptx skill.

```
MIT License

Copyright (c) 2025 Agent Chatbot Template

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## icip-cas/PPTAgent

- Source: https://github.com/icip-cas/PPTAgent (tag `v1.1.38`, commit `2419d30b`)
- Used in: `agent/presentation_agent/induct.py` (template induction approach from
  `pptagent/induct.py`), `prompts.py` (layout description prompt from
  `pptagent/prompts/ask_category.txt`), `plan.py` (functional slide categories from
  `pptagent/prompts/category_split.txt`).

```
MIT License

Copyright (c) 2025 ICIP-CAS

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
