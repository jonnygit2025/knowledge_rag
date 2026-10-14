import uuid

from qdrant_client import QdrantClient, models
from qdrant_client.local.qdrant_local import QdrantLocal
from sqlalchemy import select

from knowledge_rag.models import Chunk, Document, Manifest, RagMap, Version, now


def point_id(chunk_id, namespace):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"knowledge-rag:{namespace}:{chunk_id}"))


class VectorIndex:
    def __init__(self, cfg, provider, client=None):
        self.cfg, self.provider = cfg, provider
        self.client = client or QdrantClient(
            url=cfg.qdrant_url,
            api_key=cfg.qdrant_api_key.get_secret_value() or None,
            timeout=30,
        )

    def ensure(self, namespace=None):
        namespace = namespace or self.cfg.namespace
        if not self.client.collection_exists(namespace):
            self.client.create_collection(
                namespace,
                vectors_config=models.VectorParams(
                    size=self.cfg.dimensions,
                    distance=models.Distance.COSINE,
                ),
            )
            if not isinstance(self.client._client, QdrantLocal):
                for name in (
                    "document_id",
                    "version_id",
                    "source_type",
                    "channel_id",
                    "language",
                    "tags",
                ):
                    self.client.create_payload_index(
                        namespace, name, models.PayloadSchemaType.KEYWORD
                    )

    def apply(self, session, event):
        version = session.get(Version, event.version_id)
        document = session.get(Document, version.document_id)
        active = document.enabled and document.active_version_id == version.id
        manifest = session.get(Manifest, (version.id, event.namespace))
        # Delete actions are evaluated against CURRENT canonical state to survive reorder/retry.
        if event.action == "delete" and active:
            return
        if not active:
            if self.client.collection_exists(event.namespace):
                self.client.delete(
                    event.namespace,
                    models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="version_id", match=models.MatchValue(value=str(version.id))
                                ),
                            ]
                        )
                    ),
                    wait=True,
                )
            manifest.state, manifest.point_count = "retired", 0
            return
        if event.namespace != self.cfg.namespace:
            raise ValueError("Use matching embedding configuration to rebuild this namespace")
        self.ensure()
        chunks = session.scalars(
            select(Chunk).where(Chunk.version_id == version.id).order_by(Chunk.position)
        ).all()
        points = []
        for offset in range(0, len(chunks), 32):
            batch = chunks[offset : offset + 32]
            vectors = self.provider.embed([chunk.text for chunk in batch])
            for chunk, vector in zip(batch, vectors, strict=True):
                pid = point_id(chunk.id, event.namespace)
                payload = {
                    "document_id": str(document.id),
                    "version_id": str(version.id),
                    "chunk_id": str(chunk.id),
                    "source_type": document.source_type,
                    "index_namespace": event.namespace,
                    **{
                        key: version.metadata_json.get(key)
                        for key in ("channel_id", "published_at", "language", "tags")
                    },
                    "ingested_at": version.created_at.isoformat(),
                }
                points.append(models.PointStruct(id=pid, vector=vector, payload=payload))
                if session.get(RagMap, ("qdrant", event.namespace, pid)) is None:
                    session.add(
                        RagMap(
                            backend="qdrant",
                            namespace=event.namespace,
                            external_id=pid,
                            document_id=document.id,
                            version_id=version.id,
                            chunk_id=chunk.id,
                        )
                    )
            self.client.upsert(event.namespace, points=points, wait=True)
            points = []
        manifest.state, manifest.point_count, manifest.indexed_at = "ready", len(chunks), now()

    def search(self, question, limit=100, filters=None):
        if not self.client.collection_exists(self.cfg.namespace):
            return []
        self.ensure()
        return self.client.query_points(
            self.cfg.namespace,
            query=self.provider.embed([question])[0],
            limit=limit,
            query_filter=filters,
            with_payload=True,
        ).points
