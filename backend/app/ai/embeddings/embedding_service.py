"""Embedding providers.

The default is a deterministic hashing embedder that runs locally with no API key,
so retrieval works out of the box. It is dimension-compatible with
text-embedding-3-small (1536), which is what makes switching providers a backfill
rather than a schema migration.

Every vector is stored with the model name that produced it, and retrieval only
compares vectors from the same model — mixing embedding spaces silently produces
nonsense similarity, so it is prevented structurally rather than by convention.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod

from app.ai.lexicon import tokenise
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Embedder(ABC):
    model: str
    dim: int

    #: Cosine similarity below which a retrieved passage is not an answer.
    #:
    #: An attribute of the embedder, not a global setting, because the scale is a
    #: property of the embedding space and nothing else. An ANN search always
    #: returns its ``limit`` rows, however irrelevant, so without a floor a question
    #: about something nobody has done comes back with the k least-unrelated
    #: passages — and the assistant would quote them as evidence.
    min_similarity: float = 0.0

    #: Cosine values mapping onto 0 and 100 for the semantic scoring category, for
    #: the same reason as above: the usable range is a property of the embedding
    #: space, so it has to travel with the embedder rather than sit in the scorer.
    #:
    #: Getting these wrong does not produce a slightly-off number, it produces no
    #: number at all — a ceiling below where candidates actually land collapses the
    #: whole category to 100 for everyone, which is how this started.
    #:
    #: Defaults describe the hashing embedder, whose cosines start at zero.
    similarity_floor: float = 0.0
    similarity_ceiling: float = 0.5

    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """A search query rather than a stored document.

        Symmetric embedders make no distinction, so this defaults to ``embed``.
        Providers with asymmetric retrieval modes override it: a short question and
        the paragraph that answers it are not the same kind of text, and telling the
        model which one it is measurably improves the match.
        """
        return self.embed(text)


def _stable_hash(token: str) -> int:
    """blake2b, not Python's `hash()` — the builtin is salted per process, which
    would make vectors written by one worker incomparable with another's."""
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")


class HashingEmbedder(Embedder):
    """Signed hashing trick over unigrams and bigrams, sublinear term frequency,
    L2 normalised so cosine similarity is a plain dot product."""

    #: Deliberately low, and it cannot be tuned better than this. Measured on real
    #: resumes: a one-word query that is genuinely present ("Terraform") scores below
    #: 0.08 against a 700-character passage, while unrelated text reaches 0.04-0.05 —
    #: true hits and noise overlap, so no threshold separates them. This value keeps
    #: recall and accepts noise. A real embedding model is the fix, not a better
    #: number: see GeminiEmbedder, where the same comparison is 0.68 against 0.48.
    min_similarity: float = 0.03

    def __init__(self, dim: int | None = None, model: str | None = None) -> None:
        self.dim = dim or settings.embedding_dim
        self.model = model or "offline-hashing-v1"

    def embed(self, text: str) -> list[float]:
        tokens = tokenise(text)
        if not tokens:
            return [0.0] * self.dim

        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        # Bigrams give the vector a little word-order sensitivity, which matters for
        # phrases like "machine learning" or "spring boot".
        for left, right in zip(tokens, tokens[1:], strict=False):
            bigram = f"{left}_{right}"
            counts[bigram] = counts.get(bigram, 0) + 1

        vector = [0.0] * self.dim
        for token, count in counts.items():
            digest = _stable_hash(token)
            index = digest % self.dim
            sign = 1.0 if (digest >> 63) & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


class OpenAIEmbedder(Embedder):
    """Optional real embedding provider. Kept behind config so the default path
    needs no third-party account."""

    #: Not measured here — no credentials for it. Deliberately conservative rather
    #: than copied from another provider's scale.
    min_similarity: float = 0.30

    #: Also unmeasured. text-embedding-3-small is roughly centred, so these are wider
    #: and lower than Gemini's rather than borrowed from it. Measure them against
    #: real profiles before trusting the semantic category on this provider — the
    #: Gemini entry above is what an unmeasured range costs.
    similarity_floor: float = 0.35
    similarity_ceiling: float = 0.75

    def __init__(self) -> None:
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim
        if not settings.openai_api_key:
            raise ValueError("embedding_provider='openai' requires OPENAI_API_KEY.")

    def _post(self, texts: list[str]) -> list[list[float]]:
        import httpx

        response = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": self.model, "input": texts, "dimensions": self.dim},
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
        ordered = sorted(payload["data"], key=lambda row: row["index"])
        return [row["embedding"] for row in ordered]

    def embed(self, text: str) -> list[float]:
        return self._post([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._post(texts)


class GeminiEmbedder(Embedder):
    """``gemini-embedding-001`` through the same Vertex client the rest of the app uses.

    Two details carry the quality:

    * ``output_dimensionality`` is set to the configured dim rather than the model's
      native 3072. The model is trained so a truncated prefix is still a usable
      embedding, which is what lets this drop into a ``vector(1536)`` column with no
      migration. Google's guidance is to re-normalise after truncating, because only
      the full-length output arrives unit-length — so we do.
    * ``task_type`` distinguishes the stored passage from the question asked of it.
      Embedding both as the same kind of text is the most common way an otherwise
      correct retrieval stack underperforms.
    """

    #: Vertex rejects oversized batches; resumes chunk into tens, not thousands.
    BATCH = 32

    #: Measured against this model: a passage about YOLO object detection scores
    #: 0.68 for "computer vision experience" and 0.48 for "payroll tax compliance".
    #: Gemini embeddings are not centred on zero, so the floor sits well above 0 —
    #: retune it if the model or the output dimensionality changes.
    min_similarity: float = 0.58

    #: Measured over a 5-profile x 4-job grid (profile and JD text built by the same
    #: functions the pipeline uses). Genuine fits landed in 0.8755-0.9402; candidates
    #: from the wrong field in 0.6566-0.7987 — separable, but nowhere near the 0-0.5
    #: scale the hashing embedder uses. The inherited default put every one of those
    #: pairs at 100, so a lab technician and an ideal hire scored identically here.
    #:
    #: The floor sits just above the wrong-field median and the ceiling at the low
    #: end of true fits, which puts wrong-field candidates at 0-29 and real ones at
    #: 74-100. Retune if the model or output dimensionality changes.
    similarity_floor: float = 0.75
    similarity_ceiling: float = 0.92

    def __init__(self) -> None:
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim
        # Fail construction, not first use: the factory below falls back to the
        # offline embedder, and it can only do that if this raises here.
        from app.ai.llm.llm_service import _get_client

        self._client = _get_client()

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        out: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH):
            batch = texts[start : start + self.BATCH]
            response = self._client.models.embed_content(
                model=self.model,
                contents=batch,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=self.dim,
                ),
            )
            out.extend(_unit(list(item.values)) for item in response.embeddings)
        return out

    def embed(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_DOCUMENT")[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT") if texts else []

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


def _unit(vector: list[float]) -> list[float]:
    """L2 normalise. Required after dimensionality truncation, harmless otherwise."""
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


_embedder: Embedder | None = None


def _build(provider: str) -> Embedder:
    if provider == "gemini":
        return GeminiEmbedder()
    if provider == "openai":
        return OpenAIEmbedder()
    return HashingEmbedder()


def get_embedder() -> Embedder:
    """The configured embedder, or the offline one if it cannot be built.

    ``auto`` mirrors ``reranker_provider``: use the real provider when credentials
    are present, and the offline embedder otherwise, so a developer with no
    credentials still gets a working system.

    Degrading rather than failing matters here, but note what it costs: vectors are
    only ever compared within one model, so a run that quietly falls back writes
    vectors that will not match the ones already stored. The model name is logged
    for exactly that reason.
    """
    global _embedder
    if _embedder is None:
        provider = settings.embedding_provider
        if provider == "auto":
            provider = "gemini" if settings.llm_available else "offline"

        try:
            _embedder = _build(provider)
        except Exception as exc:  # noqa: BLE001 - degrade rather than fail startup
            logger.warning("Falling back to the offline embedder: %s", exc)
            _embedder = HashingEmbedder()
        logger.info("Embedder: %s (dim=%s)", _embedder.model, _embedder.dim)
    return _embedder


def reset_embedder() -> None:
    """Drop the cached embedder. For tests and for the backfill script, which has to
    see a configuration change made after import."""
    global _embedder
    _embedder = None


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)
