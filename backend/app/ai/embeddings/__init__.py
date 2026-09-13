"""Embedding engines.

Re-exported at package level so callers import the capability, not the module that
happens to implement it.
"""

from app.ai.embeddings.embedding_service import (
    Embedder,
    HashingEmbedder,
    OpenAIEmbedder,
    cosine,
    get_embedder,
)

__all__ = ["Embedder", "HashingEmbedder", "OpenAIEmbedder", "cosine", "get_embedder"]
