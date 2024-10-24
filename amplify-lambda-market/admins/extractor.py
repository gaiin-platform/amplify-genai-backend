
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


test = """```template    
Lesson Plan Creation

Objective:
To facilitate a comprehensive understanding of <USER: Lesson Topic>, students will be able to <USER: Learning Outcome>. This aligns with <USER: Standard or Competency>.

Context:
Currently, the class is working on <USER: Current Topic/Area of Study>. The students have previously learned <LLM: Prior Knowledge>, which directly relates to today's lesson.

Lesson Structure:
- Introduction: An engaging opener that activates prior knowledge relating to <LLM: Lesson Topic>.
- Development: The main teaching component, including a presentation of new concepts and guided practice.
- Practice: Activities or exercises where students apply the new concepts, such as <LLM: Describe Activity>.
- Assessment: How understanding will be gauged, either formatively or summatively, through <LLM: Assessment Method>.
- Feedback: When and how students will receive feedback on their performance.

Accommodations for Diverse Learners:
<LLM: Describe any modifications or supports for different learning styles, abilities, etc.>

Materials and Resources:
The following materials will be needed for the lesson: <LLM: List of Necessary Materials and Resources>.

Follow-up:
In the next lesson, we will <LLM: Next Steps in Curriculum>, building upon the concepts learned in today's session.

ChatGPT, using the information provided above, please develop a detailed lesson plan for me, including estimated time frames for each part of the structure and specific instructions for the activities and assessment.

```
"""

import re

def extract_prompt_template(prompt):
    pattern = r"```template\s*\n(.*?)```"
    matches = re.findall(pattern, prompt, re.DOTALL)
    if matches:
        return matches[0]
    else:
        return None


print(extract_prompt_template(test))
