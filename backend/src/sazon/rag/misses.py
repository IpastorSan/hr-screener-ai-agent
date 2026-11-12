"""Group the questions Ana could not answer (`FaqMiss`) so recruiters see what to add to the FAQ.

Questions are embedded with the retrieval model (`index.FastEmbedder`, local) and clustered
greedily: each question, oldest first, joins the group whose centroid it is closest to when
the cosine similarity reaches `SIMILARITY`, or opens a new group. Without the model (no local
cache, offline tests) questions are grouped by their normalised text instead: case, accents,
punctuation and spacing ignored.
"""

import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from sazon.rag.index import Embedder, _normalize

logger = logging.getLogger(__name__)

SIMILARITY = 0.65
"""Cosine similarity to a group's centroid from which a question joins it. Tuned with the
retrieval model on 16 hand-written questions about the job, five topics asked two to four ways:
rewordings scored 0.64-0.91 against each other ("¿Quién paga el uniforme?" / "¿El uniforme lo
pone la empresa?" 0.72, staff parking in Spanish and English 0.76), different questions at most
0.58 (smoking area / staff parking). At 0.65 each topic became one group, with one parking
rewording left on its own ("¿Puedo aparcar el coche en el restaurante?") and no two topics
merged; at 0.8 the uniform and smoking questions all stayed apart."""


@dataclass(frozen=True)
class Question:
    id: int
    text: str
    created_at: datetime


@dataclass
class Group:
    members: list[Question] = field(default_factory=list)

    @property
    def representative(self) -> str:
        """The most frequent wording (by normalised text), the latest on a tie."""
        counts = Counter(normalize(q.text) for q in self.members)
        latest = sorted(self.members, key=lambda q: q.created_at, reverse=True)
        return max(latest, key=lambda q: counts[normalize(q.text)]).text


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", letters).split())


def group_questions(
    questions: Sequence[Question], embedder: Embedder | None
) -> tuple[list[Group], bool]:
    """Groups of similar questions, the largest first (then the most recent), and whether they
    were grouped by similarity (False: by normalised text)."""
    ordered = sorted(questions, key=lambda q: (q.created_at, q.id))
    groups: list[Group] | None = None
    if embedder is not None and ordered:
        try:
            vectors = _embed(embedder, [q.text for q in ordered])
        except Exception as exc:  # a missing model cache, a broken install
            logger.warning("FAQ misses grouped by text (%s: %s)", type(exc).__name__, exc)
        else:
            groups = _by_similarity(ordered, vectors)
    similar = groups is not None
    if groups is None:
        groups = _by_text(ordered)
    return sorted(groups, key=lambda g: (-len(g.members), -_latest(g))), similar


_vectors: dict[tuple[str, str], np.ndarray] = {}
MAX_CACHED = 10_000
"""Question vectors kept in memory, so the dashboard's refreshes embed only new questions."""


def _embed(embedder: Embedder, texts: Sequence[str]) -> np.ndarray:
    missing = list(dict.fromkeys(t for t in texts if (embedder.name, t) not in _vectors))
    if missing:
        if len(_vectors) + len(missing) > MAX_CACHED:
            _vectors.clear()
        for text, vector in zip(missing, _normalize(embedder.embed(missing)), strict=True):
            _vectors[(embedder.name, text)] = vector
    return np.stack([_vectors[(embedder.name, t)] for t in texts])


def _by_similarity(questions: Sequence[Question], vectors: np.ndarray) -> list[Group]:
    groups: list[Group] = []
    sums = np.zeros_like(vectors)
    centroids = np.zeros_like(vectors)  # the first len(groups) rows are in use
    for question, vector in zip(questions, vectors, strict=True):
        scores = centroids[: len(groups)] @ vector
        best = int(np.argmax(scores)) if len(groups) else -1
        if best < 0 or scores[best] < SIMILARITY:
            best = len(groups)
            groups.append(Group())
        groups[best].members.append(question)
        sums[best] += vector
        centroids[best] = _normalize(sums[best : best + 1])[0]
    return groups


def _by_text(questions: Sequence[Question]) -> list[Group]:
    groups: dict[str, Group] = {}
    for question in questions:
        groups.setdefault(normalize(question.text), Group()).members.append(question)
    return list(groups.values())


def _latest(group: Group) -> float:
    return max(q.created_at for q in group.members).timestamp()
