from litellm import completion


class LiteLLMService:

    def __init__(self, model: str = "openai/gpt-4o"):
        self.model = model

    async def generate_response(self, messages: []) -> str:
        """Call LLM to get response"""

        try:
            response = completion(
                model=self.model,
                messages=messages,
                max_tokens=1024
            )

            return response.choices[0].message.content
        except Exception as e:
            return None

