"""Host-assigned research failures, independent of source or provider messages."""

from typing import Literal

ResearchErrorCode = Literal[
    "not_initialized",
    "operation_conflict",
    "budget_exhausted",
    "evidence_not_found",
    "context_conflict",
    "discovery_finished",
    "operation_failed",
]


class ResearchError(ValueError):
    """A domain failure whose public classification does not depend on its text."""

    def __init__(self, code: ResearchErrorCode, message: str):
        super().__init__(message)
        self.code = code
