"""Fail-closed isolation contracts for optional vector retrieval."""

from __future__ import annotations

import unittest

from erii.core.retriever import MemoryRetriever
from erii.models.node import MemoryNode
from erii.vector.base import (
    BaseEmbeddingProvider,
    BaseVectorStore,
    VectorIsolationError,
)
from erii.vector.chroma_adapter import ChromaVectorStore


class _Collection:
    def __init__(self, response):
        self.response = response
        self.where = None

    def query(self, **kwargs):
        self.where = kwargs.get("where")
        return self.response


def _store(response):
    store = object.__new__(ChromaVectorStore)
    store.collection = _Collection(response)
    return store


class _Embedding(BaseEmbeddingProvider):
    def embed_text(self, text: str):
        del text
        return [1.0]


class _RecordingStore(BaseVectorStore):
    def __init__(self, results):
        self.results = results
        self.upserts = []
        self.filter_metadata = None

    def upsert(self, node_id, vector, metadata=None):
        self.upserts.append((node_id, vector, metadata))

    def search(self, query_vector, top_k=10, filter_metadata=None):
        del query_vector, top_k
        self.filter_metadata = filter_metadata
        return self.results


class ChromaIsolationTests(unittest.TestCase):
    def test_scoped_query_requires_metadata_for_every_result(self):
        store = _store({"ids": [["node"]], "distances": [[0.1]]})

        with self.assertRaises(VectorIsolationError):
            store.search([1.0], filter_metadata={"agent_id": "agent"})

    def test_scoped_query_rejects_mismatched_metadata(self):
        store = _store(
            {
                "ids": [["node"]],
                "distances": [[0.1]],
                "metadatas": [[{"agent_id": "other"}]],
            }
        )

        with self.assertRaises(VectorIsolationError):
            store.search([1.0], filter_metadata={"agent_id": "agent"})

    def test_scoped_query_accepts_matching_metadata(self):
        store = _store(
            {
                "ids": [["node"]],
                "distances": [[0.1]],
                "metadatas": [[{"agent_id": "agent", "user_id": "user"}]],
            }
        )

        self.assertEqual(
            store.search(
                [1.0],
                filter_metadata={"agent_id": "agent", "user_id": "user"},
            ),
            [("node", 1.0 / 1.1)],
        )
        self.assertEqual(
            store.collection.where,
            {"agent_id": "agent", "user_id": "user"},
        )


class RetrieverIsolationTests(unittest.TestCase):
    @staticmethod
    def _node(node_id="node", agent_id="agent", user_id="user"):
        return MemoryNode(
            node_id=node_id,
            agent_id=agent_id,
            user_id=user_id,
            content="memory",
        )

    def test_retriever_indexes_and_searches_with_agent_user_scope(self):
        store = _RecordingStore([("node", 1.0)])

        MemoryRetriever().rank_candidates(
            "memory",
            [self._node()],
            vector_store=store,
            embedding_provider=_Embedding(),
        )

        self.assertEqual(
            store.filter_metadata,
            {"agent_id": "agent", "user_id": "user"},
        )
        self.assertEqual(
            store.upserts[0][2],
            {"node_id": "node", "agent_id": "agent", "user_id": "user"},
        )

    def test_retriever_rejects_backend_ids_outside_candidates(self):
        store = _RecordingStore([("foreign", 1.0)])

        with self.assertRaises(VectorIsolationError):
            MemoryRetriever().rank_candidates(
                "memory",
                [self._node()],
                vector_store=store,
                embedding_provider=_Embedding(),
            )

    def test_retriever_rejects_mixed_candidate_scopes(self):
        store = _RecordingStore([])

        with self.assertRaises(VectorIsolationError):
            MemoryRetriever().rank_candidates(
                "memory",
                [self._node("one"), self._node("two", user_id="other")],
                vector_store=store,
                embedding_provider=_Embedding(),
            )

    def test_empty_candidate_set_does_not_query_the_vector_backend(self):
        store = _RecordingStore([("foreign", 1.0)])

        ranked = MemoryRetriever().rank_candidates(
            "memory",
            [],
            vector_store=store,
            embedding_provider=_Embedding(),
        )

        self.assertEqual(ranked, [])
        self.assertIsNone(store.filter_metadata)
        self.assertEqual(store.upserts, [])


if __name__ == "__main__":
    unittest.main()
