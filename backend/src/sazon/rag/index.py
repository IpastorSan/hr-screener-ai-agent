"""Local retrieval over the curated Q&A and the company documents, embedded on this machine.

Each Q&A entry is one item; each document is chunked by heading into passages of about 80-200
words. Both live in one cosine index, and every snippet carries its `source` (`faq:<id>` or
`doc:<id>#<section>`) so recruiters can audit what backed an answer.

No corpus or candidate text leaves the machine for retrieval. The embedding model is downloaded
once by `sazon rag-index`; at runtime it is only read from the cache, and without it every
question takes the unknown path.
"""

import hashlib
import logging
import re
import threading
import unicodedata
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, model_validator

from sazon.config import get_settings
from sazon.engine.country import named_country
from sazon.engine.models import Language
from sazon.engine.validators import Country
from sazon.llm.interface import faq_snippet

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
FAQ_DIR = Path(__file__).parent / "faq"
DOCS_DIR = Path(__file__).parent / "docs"
MIN_SCORE = 0.5
"""Cosine similarity below which a question counts as not covered by the FAQ (see test_rag)."""
DOC_MIN_SCORE = 0.5
"""The same for document passages, matched sentence by sentence. Equal to `MIN_SCORE` today;
kept apart so the passages can be tuned on their own once misses show they need it. Both apply
to the raw score, while ordering uses `_rank`: the Q&A preference never lets an entry under
the threshold through."""
SEND_AS_WRITTEN_MIN_SCORE = 0.65
"""A fallback reply (the responder failed) sends the top snippet as written only when it is a
Q&A entry this close; a near-topic passage would answer another question."""
FAQ_PREFERENCE = 0.05
"""A Q&A entry outranks a passage scoring up to this much higher: curated answers are shorter."""
PART_K = 2
"""Snippets per part of a question that asks several things."""
MAX_SNIPPETS = 5
"""Snippets the responder reads for one message, all its parts together."""
CHUNK_MAX_WORDS = 200
LABEL_SEPARATOR = " \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} "
ALL_COUNTRIES = "both"

type Vectors = np.ndarray


class FaqEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    language: Language
    country: Country | None
    """None: valid in every country."""
    question: str
    also: tuple[str, ...] = ()
    answer: str

    def phrasings(self) -> tuple[str, ...]:
        """Texts embedded for this entry; a question matches the closest one."""
        return (self.question, *self.also, f"{self.question} {self.answer}")

    def snippet(self, score: float) -> "Snippet":
        return Snippet(id=self.id, question=self.question, answer=self.answer, score=score)


class DocChunk(BaseModel):
    """A passage of a company document, under one heading path."""

    model_config = ConfigDict(frozen=True)

    id: str
    """`<doc id>#<section slug>:<n>`, unique in the corpus."""
    doc_id: str
    title: str
    section: tuple[str, ...]
    language: Language
    country: Country | None
    text: str

    @property
    def source(self) -> str:
        return f"doc:{self.doc_id}#{section_slug(self.section)}"

    @property
    def label(self) -> str:
        return LABEL_SEPARATOR.join((self.title, *self.section))

    def phrasings(self) -> tuple[str, ...]:
        """The heading path, each sentence under its heading, and the passage itself.

        The model reads 128 tokens at most, and a question matches one sentence far better than
        a whole passage, so each sentence is embedded on its own.
        """
        heading = self.section[-1] if self.section else self.title
        sentences = [f"{heading}: {sentence}" for sentence in _sentences(self.text)]
        return (*sentences, self.text)

    def snippet(self, score: float) -> "Snippet":
        heading = self.section[-1] if self.section else self.title
        return Snippet(
            id=self.id,
            question=heading,
            answer=self.text,
            score=score,
            source=self.source,
            label=self.label,
        )


def send_as_written(snippets: Sequence["Snippet"]) -> bool:
    """Whether the best snippet may be sent as the answer without the responder."""
    return bool(snippets) and snippets[0].is_faq and snippets[0].score >= SEND_AS_WRITTEN_MIN_SCORE


class Snippet(BaseModel):
    """A retrieved item. `source` and `label` are for recruiters; the candidate sees neither."""

    model_config = ConfigDict(frozen=True)

    id: str
    question: str
    answer: str
    score: float
    source: str = ""
    """`faq:<id>` or `doc:<id>#<section>`; defaults to the Q&A entry."""
    label: str = ""
    """What a recruiter reads: `FAQ: <question>` or the document title and section path."""

    @model_validator(mode="before")
    @classmethod
    def _faq_defaults(cls, data: object) -> object:
        if isinstance(data, dict) and not data.get("source"):
            data = {**data, "source": f"faq:{data.get('id')}"}
            data["label"] = data.get("label") or f"FAQ: {data.get('question')}"
        return data

    @property
    def text(self) -> str:
        """What the responder reads: the heading or question and the answer, never the source."""
        return faq_snippet(self.question, self.answer)

    @property
    def is_faq(self) -> bool:
        return self.source.startswith("faq:")

    def citation(self) -> dict[str, Any]:
        return {"source": self.source, "label": self.label, "score": round(self.score, 3)}


def citations(snippets: Iterable[Snippet]) -> list[dict[str, Any]]:
    """The distinct sources behind `snippets`, best first, as stored on the agent turn."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for snippet in snippets:
        if snippet.source not in seen:
            seen.add(snippet.source)
            out.append(snippet.citation())
    return out


class Part(BaseModel):
    """One part of a question that asks several things, with what retrieval found for it.
    `country`: the other country the part asks about, whose documents were searched."""

    model_config = ConfigDict(frozen=True)

    question: str
    snippets: tuple[Snippet, ...]
    country: Country | None = None


def retrieve_parts(
    retriever: "Retriever", parts: Sequence[str], language: Language, country: Country
) -> tuple[Part, ...]:
    """Each part looked up on its own, with the same thresholds and country filter as one
    question; `PART_K` snippets per part when there are several. A part that names the other
    country ("¿Y en México?" in a Spanish conversation) is looked up in that country's."""
    k = 3 if len(parts) == 1 else PART_K
    found: list[Part] = []
    for part in parts:
        other = named_country(part, country)
        snippets = retriever.retrieve(part, language, other or country, k=k)
        found.append(Part(question=part, snippets=tuple(snippets), country=other))
    return tuple(found)


def merge_parts(parts: Sequence[Part]) -> tuple[Snippet, ...]:
    """The parts' snippets in one list: each part's best first, then their second best, with
    no item twice and at most `MAX_SNIPPETS`."""
    merged: dict[str, Snippet] = {}
    for rank in range(max((len(part.snippets) for part in parts), default=0)):
        for part in parts:
            if rank < len(part.snippets):
                merged.setdefault(part.snippets[rank].id, part.snippets[rank])
    return tuple(merged.values())[:MAX_SNIPPETS]


type Item = FaqEntry | DocChunk


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> Vectors: ...


class Retriever(Protocol):
    def retrieve(
        self, question: str, language: Language, country: Country, k: int = 3
    ) -> list[Snippet]: ...


class FastEmbedder:
    """A fastembed ONNX model, loaded on first use. `offline` never downloads."""

    def __init__(self, cache_dir: Path, model_name: str = MODEL_NAME, offline: bool = True) -> None:
        self.name = model_name
        self.cache_dir = cache_dir
        self.offline = offline
        self._model = None

    def embed(self, texts: Sequence[str]) -> Vectors:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                self.name,
                cache_dir=str(self.cache_dir / "models"),
                local_files_only=self.offline,
            )
        return np.asarray(list(self._model.embed(list(texts))), dtype=np.float32)


def load_entries(directory: Path = FAQ_DIR) -> list[FaqEntry]:
    entries: list[FaqEntry] = []
    for language in Language:
        for path in sorted((directory / language.value).glob("*.md")):
            entries.append(parse_entry(path.read_text(encoding="utf-8"), language))
    return entries


def parse_entry(text: str, language: Language) -> FaqEntry:
    """`---` front matter of `key: value` lines, then the answer."""
    meta, body = _front_matter(text)
    return FaqEntry(
        id=meta["id"],
        language=language,
        country=_country(meta),
        question=meta["question"],
        also=tuple(part.strip() for part in meta.get("also", "").split("|") if part.strip()),
        answer=" ".join(body.split()),
    )


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    """The `key: value` lines between the `---` markers, and the text after them."""
    _, header, body = text.split("---\n", 2)
    pairs = (line.split(":", 1) for line in header.strip().splitlines())
    return {key.strip(): value.strip() for key, value in pairs}, body


def _country(meta: dict[str, str]) -> Country | None:
    """None for a text valid in every country (`country: both`, or no country)."""
    country = meta.get("country", ALL_COUNTRIES)
    if country == ALL_COUNTRIES:
        return None
    if country == "ES" or country == "MX":
        return country
    raise ValueError(f"unknown country {country!r}")


class Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    language: Language
    country: Country | None
    updated: str
    sections: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]
    """(heading path, paragraphs) in order; the text before the first heading has no path."""

    def words(self) -> int:
        return sum(len(p.split()) for _, paragraphs in self.sections for p in paragraphs)


def load_documents(directory: Path = DOCS_DIR) -> list[Document]:
    documents: list[Document] = []
    for language in Language:
        for path in sorted((directory / language.value).glob("*.md")):
            document = parse_document(path.read_text(encoding="utf-8"))
            if document.language != language:
                raise ValueError(f"{path}: language {document.language} in the {language} folder")
            documents.append(document)
    return documents


def parse_document(text: str) -> Document:
    """`---` front matter (id, title, country, language, updated), then Markdown.

    `#` is the document title; `##` and `###` open sections. Paragraphs are separated by blank
    lines; a list counts as one paragraph.
    """
    meta, body = _front_matter(text)
    sections: list[tuple[tuple[str, ...], list[str]]] = [((), [])]
    path: tuple[str, ...] = ()
    for block in re.split(r"\n\s*\n", body.strip()):
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        while lines and lines[0].startswith("#"):
            marks, _, heading = lines.pop(0).partition(" ")
            if len(marks) >= 2:
                path = (*path[: len(marks) - 2], heading.strip())
                sections.append((path, []))
        if lines:
            sections[-1][1].append(" ".join(line.removeprefix("- ") for line in lines))
    return Document(
        id=meta["id"],
        title=meta["title"],
        language=Language(meta["language"]),
        country=_country(meta),
        updated=meta["updated"],
        sections=tuple((p, tuple(paragraphs)) for p, paragraphs in sections if paragraphs),
    )


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?;])\s+", text.strip()) if part]


def section_slug(section: Sequence[str]) -> str:
    if not section:
        return "intro"
    folded = unicodedata.normalize("NFKD", "/".join(section)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9/]+", "-", folded.lower()).strip("-")


def chunk_document(document: Document, max_words: int = CHUNK_MAX_WORDS) -> list[DocChunk]:
    """One chunk per section, split by sentence past `max_words` with one sentence of overlap."""
    chunks: list[DocChunk] = []
    for section, paragraphs in document.sections:
        sentences = [s for paragraph in paragraphs for s in _sentences(paragraph)]
        groups: list[list[str]] = [[]]
        for sentence in sentences:
            current = groups[-1]
            if current and sum(len(s.split()) for s in [*current, sentence]) > max_words:
                groups.append([current[-1]])
            groups[-1].append(sentence)
        slug = section_slug(section)
        chunks.extend(
            DocChunk(
                id=f"{document.id}#{slug}:{n}",
                doc_id=document.id,
                title=document.title,
                section=section,
                language=document.language,
                country=document.country,
                text=" ".join(group),
            )
            for n, group in enumerate(groups)
        )
    return chunks


def load_corpus(faq_dir: Path = FAQ_DIR, docs_dir: Path = DOCS_DIR) -> list[Item]:
    """The Q&A entries and every document chunk."""
    chunks = [chunk for document in load_documents(docs_dir) for chunk in chunk_document(document)]
    return [*load_entries(faq_dir), *chunks]


class RagIndex:
    """Q&A entries and document chunks with one vector per phrasing, cached on disk in
    `cache_dir` (none: embedded on every start)."""

    def __init__(
        self,
        entries: Sequence[Item],
        embedder: Embedder,
        *,
        min_score: float = MIN_SCORE,
        doc_min_score: float = DOC_MIN_SCORE,
        cache_dir: Path | None = None,
    ) -> None:
        self.entries = tuple(entries)
        self.embedder = embedder
        self.min_score = min_score
        self.doc_min_score = doc_min_score
        self.cache_dir = cache_dir
        self._owner = np.array(
            [i for i, entry in enumerate(self.entries) for _ in entry.phrasings()], dtype=np.int64
        )
        self._vectors: Vectors | None = None
        self._failed = False
        self._lock = threading.Lock()

    def describe(self) -> str:
        faq = sum(isinstance(entry, FaqEntry) for entry in self.entries)
        documents = {entry.doc_id for entry in self.entries if isinstance(entry, DocChunk)}
        chunks = len(self.entries) - faq
        return f"{faq} Q&A entries, {chunks} passages from {len(documents)} documents"

    def retrieve(
        self, question: str, language: Language, country: Country, k: int = 3
    ) -> list[Snippet]:
        """The best `k` items for this language and country over their score threshold."""
        scored = self.scores(question, language, country)
        return [snippet for snippet in scored if self._passes(snippet)][:k]

    def _passes(self, snippet: Snippet) -> bool:
        return snippet.score >= (self.min_score if snippet.is_faq else self.doc_min_score)

    def scores(self, question: str, language: Language, country: Country) -> list[Snippet]:
        """Every eligible item with its score, best first, a Q&A entry winning near-ties.

        Empty when the model is missing.
        """
        vectors = self.vectors()
        if vectors is None or not question.strip():
            return []
        try:
            query = _normalize(self.embedder.embed([question]))[0]
        except Exception as exc:
            self._unavailable(exc)
            return []
        best = np.full(len(self.entries), -1.0, dtype=np.float32)
        np.maximum.at(best, self._owner, vectors @ query)
        snippets = [
            entry.snippet(float(score))
            for entry, score in zip(self.entries, best, strict=True)
            if entry.language == language and entry.country in (None, country)
        ]
        return sorted(snippets, key=_rank, reverse=True)

    def warm(self) -> bool:
        """Load the vectors and the embedding model now, not on the first question."""
        return bool(self.scores("hola", Language.ES, "ES"))

    @property
    def ready(self) -> bool:
        """Vectors loaded and nothing has failed since. A failure (a missing model, one failed
        query) lasts for the life of the process: nothing retries."""
        return self._vectors is not None and not self._failed

    def vectors(self) -> Vectors | None:
        with self._lock:
            if self._vectors is None and not self._failed:
                try:
                    self._vectors = self._load_or_embed()
                except Exception as exc:
                    self._unavailable(exc)
            return self._vectors

    def _unavailable(self, exc: Exception) -> None:
        self._failed = True
        self._vectors = None
        logger.warning(
            "FAQ and document retrieval unavailable (%s: %s); run `sazon rag-index` once.",
            type(exc).__name__,
            str(exc)[:200],
        )

    def _load_or_embed(self) -> Vectors:
        texts = [text for entry in self.entries for text in entry.phrasings()]
        path = self.cache_path(texts)
        if path is not None and path.exists():
            return np.load(path)
        vectors = _normalize(self.embedder.embed(texts))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, vectors)
        return vectors

    def cache_path(self, texts: Iterable[str]) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256("\n".join([self.embedder.name, *texts]).encode()).hexdigest()
        return self.cache_dir / "rag" / f"vectors-{digest[:16]}.npy"


class NoRetriever:
    """A retriever that knows nothing: every question takes the unknown path."""

    def retrieve(
        self, question: str, language: Language, country: Country, k: int = 3
    ) -> list[Snippet]:
        return []


_default: RagIndex | None = None
_default_lock = threading.Lock()


def default_index() -> RagIndex:
    """The shared index over the packaged corpus, reading the model from the local cache only."""
    global _default
    with _default_lock:
        if _default is None:
            cache_dir = get_settings().sazon_cache_dir
            _default = RagIndex(load_corpus(), FastEmbedder(cache_dir), cache_dir=cache_dir)
        return _default


def build_cache(cache_dir: Path | None = None) -> tuple[RagIndex, Path]:
    """Download the model if needed and embed the corpus. Returns the index and the cache folder
    (`SAZON_CACHE_DIR` by default)."""
    cache_dir = cache_dir or get_settings().sazon_cache_dir
    index = RagIndex(
        load_corpus(), FastEmbedder(cache_dir=cache_dir, offline=False), cache_dir=cache_dir
    )
    if index.vectors() is None or not index.scores("?", Language.ES, "ES"):
        raise RuntimeError("Could not build the retrieval index; see the log above.")
    return index, cache_dir


def _rank(snippet: Snippet) -> float:
    return snippet.score + (FAQ_PREFERENCE if snippet.is_faq else 0.0)


def _normalize(vectors: Vectors) -> Vectors:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)
