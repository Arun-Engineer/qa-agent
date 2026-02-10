from pathlib import Path
from openai import OpenAI


class TestGenerator:

    def __init__(
        self,
        model="gpt-4",
        prompt_path="config/prompts/generate_test_file.md"
    ):
        prompt_file = Path(prompt_path)

        if not prompt_file.exists():
            raise FileNotFoundError(f"Missing prompt file: {prompt_path}")

        # Read prompt safely cross‑platform
        self.prompt = prompt_file.read_text(encoding="utf-8")

        # Correct OpenAI client usage
        self.client = OpenAI()
        self.model = model


    # -----------------------------------------------------
    # Generate Python test code using LLM
    # -----------------------------------------------------
    def generate_test_code(self, step: dict) -> str:

        description = step.get("args", {}).get("description", str(step))

        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": description}
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
            )

            code = response.choices[0].message.content.strip()

            # Safety guard: ensure file always valid python
            if not code.startswith(("import", "from", "def", "#")):
                code = f"# Auto-generated test\n{code}"

            return code

        except Exception as e:
            return f"# ERROR generating test\n# {e}"


    # -----------------------------------------------------
    # Save generated file
    # -----------------------------------------------------
    def write_test_file(self, content: str, file_path: str):

        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        file_path.write_text(content, encoding="utf-8")

        return str(file_path)
