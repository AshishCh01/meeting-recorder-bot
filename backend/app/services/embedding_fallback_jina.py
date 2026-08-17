"""
Fallback embedding path using Jina AI (jina-embeddings-v3), used when
Gemini's embedding API keeps failing (429/503) after retries are
exhausted. Sarvam AI (the transcription fallback) has no embeddings
API, so this is independent, Google-unrelated infra.

Uses Jina's asymmetric retrieval task types - `retrieval.passage` for
indexed meeting chunks, `retrieval.query` for chat queries - which
improves match quality over using a single generic task type. Both
request `embedding_dimensions`-dim output (768 by default) so vectors
still fit the existing pgvector column with no schema change.
"""

import httpx

from app.config import settings

JINA_URL = "https://api.jina.ai/v1/embeddings"


def _call_jina(texts: list[str], task: str) -> list[list[float]]:
    if not texts:
        return []

    response = httpx.post(
        JINA_URL,
        headers={
            "Authorization": f"Bearer {settings.jina_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.jina_embedding_model,
            "task": task,
            "dimensions": settings.embedding_dimensions,
            "input": texts,
        },
        timeout=60.0,
    )
    response.raise_for_status()

    data = response.json()["data"]
    # Jina doesn't guarantee `data` preserves input order - sort by the
    # index field it returns alongside each embedding, to be safe.
    data.sort(key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def embed_documents_with_jina(texts: list[str]) -> list[list[float]]:
    """Embeds meeting chunks (documents) for indexing."""
    return _call_jina(texts, task="retrieval.passage")


def embed_query_with_jina(query: str) -> list[float]:
    """Embeds a single chat query, using the query-side task type."""
    return _call_jina([query], task="retrieval.query")[0]