"""Optional ChromaDB Vector Store driver for E.R.I.I. Engine.

Follows Google Python Style Guide.
"""

from typing import Any, Dict, List, Optional, Tuple

from erii.vector.base import BaseVectorStore, VectorIsolationError


class ChromaVectorStore(BaseVectorStore):
    """Adapter for ChromaDB vector store."""

    def __init__(self, collection_name: str = "erii_memory", path: Optional[str] = None) -> None:
        try:
            import chromadb
        except ImportError:
            raise ImportError(
                "chromadb package is required for ChromaVectorStore. Install via `pip install chromadb`."
            )

        if path:
            self.client = chromadb.PersistentClient(path=path)
        else:
            self.client = chromadb.Client()

        self.collection = self.client.get_or_create_collection(name=collection_name)

    def upsert(
        self,
        node_id: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.collection.upsert(
            ids=[node_id],
            embeddings=[vector],
            metadatas=[metadata or {}],
        )

    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[str, float]]:
        where = filter_metadata if filter_metadata else None
        res = self.collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            where=where,
        )
        results: List[Tuple[str, float]] = []
        ids_batches = res.get("ids") or [[]]
        distance_batches = res.get("distances") or [[]]
        ids = ids_batches[0]
        distances = distance_batches[0]
        if len(ids) != len(distances):
            raise RuntimeError("Vector DB returned mismatched ids and distances")

        metadata_batches = res.get("metadatas")
        metadatas = metadata_batches[0] if metadata_batches else None
        if filter_metadata and (metadatas is None or len(metadatas) != len(ids)):
            raise VectorIsolationError(
                "Vector DB omitted complete metadata for a scoped query"
            )

        for idx, (node_id, dist) in enumerate(zip(ids, distances)):
            # A scoped query must carry complete proof for every returned item.
            if filter_metadata:
                result_metadata = metadatas[idx]
                if not isinstance(result_metadata, dict):
                    raise VectorIsolationError(
                        "Vector DB returned invalid metadata for a scoped query"
                    )
                for key, expected_value in filter_metadata.items():
                    actual_value = result_metadata.get(key)
                    if actual_value != expected_value:
                        raise VectorIsolationError(
                            "Vector DB returned a result outside the requested scope"
                        )

            # Chroma returns L2 or Cosine distance; convert to similarity score
            similarity = 1.0 / (1.0 + float(dist))
            results.append((node_id, similarity))

        return results
