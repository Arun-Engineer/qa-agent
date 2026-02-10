# agent/verifier/structured_asserts.py
def verify_output(output: dict, rules: list):
    """
    Enforce structured assertions on output from tools.
    Each rule is a dict like {"field": "status", "equals": 200} or {"contains": "timeout"}
    """
    verdicts = []
    for rule in rules:
        if "field" in rule and "equals" in rule:
            val = output.get(rule["field"])
            verdicts.append(val == rule["equals"])
        elif "field" in rule and "contains" in rule:
            val = str(output.get(rule["field"], ""))
            verdicts.append(rule["contains"] in val)
    return all(verdicts)
