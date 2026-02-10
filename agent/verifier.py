# agent/verifier.py

class Verifier:
    def validate(self, output, step):
        """
        Performs basic validation of tool output against expected criteria.
        Returns True if the result is considered successful, else False.
        """
        if step['tool'] in ['pytest_runner', 'playwright_runner']:
            return output.get("code", 1) == 0
        if step['tool'] == 'api_caller':
            return output.get("ok", False) and 200 <= output.get("status", 500) < 300
        return True

    def triage(self, output, step):
        """
        Tries to classify failure reason.
        """
        tool = step.get("tool")
        result = {
            "tool": tool,
            "step": step,
            "diagnosis": "unknown",
            "details": output
        }

        if tool in ["pytest_runner", "playwright_runner"]:
            stderr = output.get("stderr", "")
            if "timeout" in stderr:
                result["diagnosis"] = "flaky_timeout"
            elif "assert" in stderr or "AssertionError" in stderr:
                result["diagnosis"] = "test_assertion_failed"
            elif output.get("code", 0) != 0:
                result["diagnosis"] = "general_failure"

        elif tool == "api_caller":
            status = output.get("status")
            if status in [500, 502, 503]:
                result["diagnosis"] = "server_error"
            elif status == 401:
                result["diagnosis"] = "unauthorized"
            elif status == 404:
                result["diagnosis"] = "endpoint_missing"

        return result