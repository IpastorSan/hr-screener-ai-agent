"""All model access for the screener goes through this package."""

from sazon.llm.fake import FakeLLM
from sazon.llm.interface import (
    LLM,
    CallRecord,
    CandidateSummary,
    Channel,
    ChatTurn,
    ExtractResult,
    Reply,
    ReplyContext,
    ReplyResult,
    Role,
    SummaryResult,
)

__all__ = [
    "LLM",
    "CallRecord",
    "CandidateSummary",
    "Channel",
    "ChatTurn",
    "ExtractResult",
    "FakeLLM",
    "Reply",
    "ReplyContext",
    "ReplyResult",
    "Role",
    "SummaryResult",
]
