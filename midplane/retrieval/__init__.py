"""Retrieval layer for episodes, schema, and decision traces.

The default embedder and reranker are local. Optional model adapters live in
``embedder`` and ``rerank`` and are not constructed unless a caller asks.
"""

from midplane.retrieval.chain import (
    DEFAULT_RETRIEVAL_STEPS,
    ChainResult,
    ChainStep,
    RetrievalChain,
    retrieval_payload,
    retrieval_prefix,
)
from midplane.retrieval.embedder import EMBEDDING_DIM, HashedNgramEmbedder
from midplane.retrieval.episodes import EpisodeMemoryStore
from midplane.retrieval.schema_store import BuiltinSchemaLoader, SchemaConnectorStore, SchemaDocumentLoader
from midplane.retrieval.traces import DecisionTraceStore
from midplane.retrieval.types import Document, RetrievalResult, ScoreBreakdown

__all__ = [
    "BuiltinSchemaLoader",
    "ChainResult",
    "ChainStep",
    "DEFAULT_RETRIEVAL_STEPS",
    "DecisionTraceStore",
    "Document",
    "EMBEDDING_DIM",
    "EpisodeMemoryStore",
    "HashedNgramEmbedder",
    "RetrievalChain",
    "RetrievalResult",
    "SchemaConnectorStore",
    "SchemaDocumentLoader",
    "ScoreBreakdown",
    "retrieval_payload",
    "retrieval_prefix",
]
