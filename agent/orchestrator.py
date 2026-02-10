class QATestOrchestrator:
    def __init__(self, planner, tools, verifier):
        self.planner = planner
        self.tools = tools
        self.verifier = verifier

    def run(self, spec: str):
        plan = self.planner.generate_plan(spec)
        for step in plan['steps']:
            tool = self.tools.get(step['tool'])
            output = tool.run(**step['args'])
            verified = self.verifier.validate(output, step)
            if not verified:
                return self.verifier.triage(output, step)
        return "✅ All checks passed."
