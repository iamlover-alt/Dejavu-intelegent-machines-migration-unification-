"""Wires the three stores for the gateway and the graph flag."""

from __future__ import annotations

from midplane.retrieval.chain import RetrievalChain
from midplane.retrieval.episodes import EpisodeMemoryStore
from midplane.retrieval.schema_store import SchemaConnectorStore
from midplane.retrieval.traces import DecisionTraceStore
from midplane.store import EpisodeStore


class RetrievalService:
    """Episode, schema, and trace stores on one database URL.

    Vectors use numpy when the URL is SQLite and pgvector when it is Postgres.
    The chain has no model provider. HTTP retrieval cannot spend model tokens.
    """

    def __init__(self, database_url: str, episode_store: EpisodeStore) -> None:
        self.database_url = database_url
        self.episode_store = episode_store
        self.memory = EpisodeMemoryStore(database_url=database_url)
        self.schema = SchemaConnectorStore(database_url=database_url)
        self.traces = DecisionTraceStore(database_url)
        self.chain = RetrievalChain(
            stores={
                "episodes": self.memory,
                "schema": self.schema,
                "traces": self.traces,
            },
            max_hops=3,
            provider=None,
        )

    def close(self) -> None:
        self.memory.close()
        self.schema.close()
        self.traces.close()
