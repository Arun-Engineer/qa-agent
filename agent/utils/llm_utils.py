# === llm_utils.py ===
# Auto-convert logs into testable specs using LLM

def generate_spec_from_logs(log_text: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    prompt = f"""
    Given this error log, write a structured QA test goal:

    {log_text}

    Output format:
    Reproduce and validate the scenario that causes: <summary>
    """
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a QA test planner."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()