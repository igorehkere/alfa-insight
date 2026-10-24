"""Domain errors: Igor's HTTP layer maps status_code to the agreed contract."""


class LLMError(RuntimeError):
    """Safe provider error; also re-exported from llm_client for compatibility."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AgentError(RuntimeError):
    status_code = 422


class NotFound(AgentError):
    status_code = 404


class AmbiguousProfile(AgentError):
    status_code = 409


class InvalidInput(AgentError):
    pass


class EvidenceError(AgentError):
    pass


class AgentUnavailable(AgentError):
    status_code = 503
