"""Хранилище Qdrant: коллекция с named-векторами dense+sparse, Int8-квантизация,
гибридный поиск через нативный Reciprocal Rank Fusion.
"""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from docrag.common.embedder import Embedding
from docrag.common.logging import get_logger
from docrag.common.models import Chunk
from docrag.indexer.chunker import point_id_for
from docrag.settings import settings

log = get_logger("qdrant")

DENSE = "dense"
SPARSE = "sparse"
DENSE_DIM = 1024

_CLIENT: QdrantClient | None = None


@dataclass
class SearchHit:
    payload: dict
    score: float


def get_client() -> QdrantClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = QdrantClient(url=settings.qdrant_url, timeout=60)
    return _CLIENT


def recreate_collection() -> None:
    client = get_client()
    name = settings.qdrant_collection
    if client.collection_exists(name):
        client.delete_collection(name)
    quant = (
        qm.ScalarQuantization(
            scalar=qm.ScalarQuantizationConfig(type=qm.ScalarType.INT8, always_ram=True)
        )
        if settings.qdrant_use_quantization
        else None
    )
    client.create_collection(
        collection_name=name,
        vectors_config={DENSE: qm.VectorParams(size=DENSE_DIM, distance=qm.Distance.COSINE)},
        sparse_vectors_config={SPARSE: qm.SparseVectorParams()},
        quantization_config=quant,
    )
    # индексы по payload для строгой фильтрации (тип документа, редакция, пункт)
    for field, schema in [
        ("doc_id", qm.PayloadSchemaType.KEYWORD),
        ("doc_type", qm.PayloadSchemaType.KEYWORD),
        ("clause", qm.PayloadSchemaType.KEYWORD),
        ("effective_date", qm.PayloadSchemaType.KEYWORD),
    ]:
        client.create_payload_index(name, field_name=field, field_schema=schema)
    log.info("Коллекция '%s' пересоздана (dense+sparse, Int8=%s)", name, settings.qdrant_use_quantization)


def _chunk_payload(chunk: Chunk) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "doc_title": chunk.doc_title,
        "text": chunk.text,
        "parent_text": chunk.parent_text,
        "section_path": chunk.section_path,
        "clause": chunk.clause,
        "page": chunk.page,
        "doc_type": chunk.doc_type,
        "source_url": chunk.source_url,
        "effective_date": chunk.effective_date,
    }


def upsert_chunks(chunks: list[Chunk], embeddings: list[Embedding], batch: int = 128) -> int:
    client = get_client()
    name = settings.qdrant_collection
    points: list[qm.PointStruct] = []
    for chunk, emb in zip(chunks, embeddings):
        points.append(
            qm.PointStruct(
                id=point_id_for(chunk),
                vector={
                    DENSE: emb.dense,
                    SPARSE: qm.SparseVector(indices=emb.sparse.indices, values=emb.sparse.values),
                },
                payload=_chunk_payload(chunk),
            )
        )
    total = 0
    for i in range(0, len(points), batch):
        client.upsert(collection_name=name, points=points[i : i + batch])
        total += len(points[i : i + batch])
    log.info("Загружено точек в Qdrant: %d", total)
    return total


def _build_filter(
    doc_ids: list[str] | None = None,
    doc_types: list[str] | None = None,
) -> qm.Filter | None:
    must: list[qm.FieldCondition] = []
    if doc_ids:
        must.append(qm.FieldCondition(key="doc_id", match=qm.MatchAny(any=doc_ids)))
    if doc_types:
        must.append(qm.FieldCondition(key="doc_type", match=qm.MatchAny(any=doc_types)))
    return qm.Filter(must=must) if must else None


def hybrid_search(
    query_emb: Embedding,
    limit: int | None = None,
    doc_ids: list[str] | None = None,
    doc_types: list[str] | None = None,
) -> list[SearchHit]:
    """Гибридный поиск dense+sparse со слиянием RRF (нативно в Qdrant)."""
    client = get_client()
    name = settings.qdrant_collection
    limit = limit or settings.retrieve_top_k
    flt = _build_filter(doc_ids, doc_types)
    prefetch = [
        qm.Prefetch(query=query_emb.dense, using=DENSE, limit=limit, filter=flt),
        qm.Prefetch(
            query=qm.SparseVector(indices=query_emb.sparse.indices, values=query_emb.sparse.values),
            using=SPARSE,
            limit=limit,
            filter=flt,
        ),
    ]
    resp = client.query_points(
        collection_name=name,
        prefetch=prefetch,
        query=qm.FusionQuery(fusion=qm.Fusion.RRF),
        limit=limit,
        with_payload=True,
    )
    return [SearchHit(payload=p.payload, score=p.score or 0.0) for p in resp.points]


def collection_info() -> dict:
    client = get_client()
    name = settings.qdrant_collection
    if not client.collection_exists(name):
        return {"exists": False}
    info = client.get_collection(name)
    return {"exists": True, "points": info.points_count, "status": str(info.status)}
