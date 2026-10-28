from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

_KB_LOCK = threading.Lock()
_CLIENT = None
_COLLECTION = None
_EMBEDDER = None


def _ensure_collection():
    global _CLIENT, _COLLECTION
    if _COLLECTION is not None:
        return _COLLECTION
    with _KB_LOCK:
        if _COLLECTION is not None:
            return _COLLECTION
        import chromadb
        from chromadb.config import Settings

        path = Path("data") / "knowledge" / "chroma"
        path.mkdir(parents=True, exist_ok=True)
        _CLIENT = chromadb.PersistentClient(
            path=str(path),
            settings=Settings(anonymized_telemetry=False),
        )
        _COLLECTION = _CLIENT.get_or_create_collection(
            "bili_videos",
            metadata={"hnsw:space": "cosine"},
        )
        return _COLLECTION


def _ensure():
    global _EMBEDDER
    collection = _ensure_collection()
    if _EMBEDDER is None:
        with _KB_LOCK:
            if _EMBEDDER is None:
                from fastembed import TextEmbedding

                _EMBEDDER = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5")
    return collection, _EMBEDDER


def _chunks(text: str, size: int = 700, overlap: int = 80) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    size = max(200, int(size))
    overlap = max(0, min(int(overlap), size // 3))
    chunks = []
    start = 0
    while start < len(value):
        end = min(len(value), start + size)
        piece = value[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(value):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _embed(texts: list[str]) -> list[list[float]]:
    _, embedder = _ensure()
    return [list(map(float, vector)) for vector in embedder.embed(texts)]


def add_video(
    *,
    bvid: str,
    title: str,
    author: str,
    url: str,
    transcript: str,
    summary: str = "",
    topics: list[str] | None = None,
) -> dict[str, Any]:
    collection, _ = _ensure()
    bvid = str(bvid or "").strip()
    if not bvid:
        raise ValueError("bvid 不能为空")
    docs: list[str] = []
    metadatas: list[dict[str, Any]] = []
    ids: list[str] = []
    topic_text = "、".join(topics or [])
    if summary.strip():
        docs.append(f"【视频总结】{summary.strip()}")
        metadatas.append(
            {
                "bvid": bvid,
                "title": title[:300],
                "author": author[:160],
                "url": url,
                "kind": "summary",
                "topics": topic_text,
            }
        )
        ids.append(f"{bvid}:summary")
    for index, chunk in enumerate(_chunks(transcript)):
        docs.append(chunk)
        metadatas.append(
            {
                "bvid": bvid,
                "title": title[:300],
                "author": author[:160],
                "url": url,
                "kind": "transcript",
                "chunk": index,
                "topics": topic_text,
            }
        )
        ids.append(f"{bvid}:chunk:{index}")
    if not docs:
        return {"added": 0, "bvid": bvid}
    embeddings = _embed(docs)
    try:
        collection.delete(where={"bvid": bvid})
    except Exception:
        pass
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=docs,
        metadatas=metadatas,
    )
    return {"added": len(docs), "bvid": bvid}


def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    collection, _ = _ensure()
    query = str(query or "").strip()
    if not query:
        return []
    vector = _embed([query])[0]
    result = collection.query(
        query_embeddings=[vector],
        n_results=max(1, min(20, int(limit))),
        include=["documents", "metadatas", "distances"],
    )
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    items = []
    for index, doc in enumerate(docs):
        meta = metas[index] if index < len(metas) else {}
        items.append(
            {
                "text": doc,
                "metadata": meta,
                "distance": distances[index] if index < len(distances) else None,
            }
        )
    return items


def stats() -> dict[str, Any]:
    collection = _ensure_collection()
    try:
        count = int(collection.count())
    except Exception:
        count = 0
    return {"count": count, "collection": "bili_videos"}


async def add_video_async(**kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(add_video, **kwargs)


async def search_async(query: str, limit: int = 5) -> list[dict[str, Any]]:
    return await asyncio.to_thread(search, query, limit)


async def stats_async() -> dict[str, Any]:
    return await asyncio.to_thread(stats)
