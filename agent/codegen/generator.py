# agent/codegen/generator.py
from pathlib import Path
from openai import OpenAI

class TestGenerator:
    def __init__(self, model="gpt-4", prompt_template_path="config/prompts/generate_test_file.md"):
        self.llm = OpenAI(model=model)
        self.prompt_template = Path(prompt_template_path).read_text()

    def generate_test_code(self, step):
        test_description = step.get("args", {}).get("description") or str(step)
        messages = [
            {"role": "system", "content": self.prompt_template},
            {"role": "user", "content": test_description}
        ]
        try:
            result = self.llm.chat.completions.create(
                messages=messages,
                response_format="text"
            )
            return result.choices[0].message.content.strip()
        except Exception as e:
            return f"# ERROR: Failed to generate test\n# {e}"

    def write_test_file(self, content, file_path):
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return str(file_path)


