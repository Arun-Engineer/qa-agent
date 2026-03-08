"""
agent/core/errors.py — Structured error hierarchy

Every error in the agent system inherits from AgentError.
This enables:
  - Typed catch blocks (catch PlanningError vs ToolError)
  - Error chaining (cause= preserves original exception)
  - Structured logging (type + message + cause)
"""


class AgentError(Exception):
    """Base error for all agent operations."""

    def __init__(self, message: str, cause: Exception = None):
        self.message = message
        self.cause = cause
        super().__init__(message)

    def to_dict(self):
        d = {"type": type(self).__name__, "message": self.message}
        if self.cause:
            d["cause"] = {
                "type": type(self.cause).__name__,
                "message": str(self.cause),
            }
        return d


class PlanningError(AgentError):
    """LLM failed to generate a valid plan."""
    pass


class ExecutionError(AgentError):
    """A step execution failed."""
    pass


class ToolError(AgentError):
    """A specific tool (pytest, playwright, api_caller) failed."""

    def __init__(self, message: str, tool: str = "", cause: Exception = None):
        self.tool = tool
        super().__init__(message, cause)


class TimeoutError(AgentError):
    """Operation exceeded its timeout."""
    pass


class RetryExhaustedError(AgentError):
    """All retry attempts failed."""
    pass


class EnrichmentError(AgentError):
    """RAG/recon enrichment failed (usually non-fatal)."""
    pass


class VerificationError(AgentError):
    """Post-execution verification failed."""
    pass


class LLMError(AgentError):
    """LLM API call failed (rate limit, auth, network)."""

    def __init__(self, message: str, provider: str = "", model: str = "", cause: Exception = None):
        self.provider = provider
        self.model = model
        super().__init__(message, cause)
