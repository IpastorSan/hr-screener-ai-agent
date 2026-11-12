"""Sentiment per conversation, for recruiters and analytics. The engine reads only the label of
each turn (`frustration_streak`); this keeps the labels of a whole conversation.

Only messages the extractor read carry a label. A widget tap, a nudge or a turn whose
extraction fell back to the offline default records none: a tap is not a mood.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from sazon.engine.models import Sentiment

SEVERITY = {
    Sentiment.FRUSTRATED: 3,
    Sentiment.CONFUSED: 2,
    Sentiment.NEUTRAL: 1,
    Sentiment.POSITIVE: 0,
}
"""Which label a conversation's peak is: frustrated > confused > neutral > positive."""
NEGATIVE = frozenset({Sentiment.FRUSTRATED, Sentiment.CONFUSED})


class SentimentStats(BaseModel):
    """The labels read from one conversation's messages.

    `peak` is the most severe label seen; `last_notable` the most recent one that is not
    neutral.
    """

    model_config = ConfigDict(frozen=True)

    counts: Mapping[Sentiment, int] = {}
    peak: Sentiment | None = None
    last_notable: Sentiment | None = None

    @classmethod
    def load(cls, stored: Mapping[str, Any] | None) -> "SentimentStats":
        return cls.model_validate(stored or {})

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def record(self, label: Sentiment) -> "SentimentStats":
        peak = self.peak
        if peak is None or SEVERITY[label] > SEVERITY[peak]:
            peak = label
        return SentimentStats(
            counts={**self.counts, label: self.counts.get(label, 0) + 1},
            peak=peak,
            last_notable=label if label != Sentiment.NEUTRAL else self.last_notable,
        )

    @property
    def mood(self) -> Sentiment | None:
        """What recruiters see: the worst label when it is frustrated or confused, otherwise the
        most recent label that is not neutral (positive), otherwise neutral. None before the
        candidate has written anything the extractor read."""
        if self.peak is None or self.peak in NEGATIVE:
            return self.peak
        return self.last_notable or self.peak
