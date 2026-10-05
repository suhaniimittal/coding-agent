# Vector Stores

**Module:** `common_lib.utils.vectors`

Store and search vector embeddings. Two backends are supported: PostgreSQL with the pgvector extension, and Milvus.

## Quick Example

```python
from common_lib.utils.vectors.vectore_store_factory import VectorStoreFactory
from common_lib.models.vectors import PostgresStorage

config = PostgresStorage(
    customer="customer1",
    collection_name="embeddings",
)
vector_store = VectorStoreFactory.get_vector_store_connection(config)

vector_store.store_embeddings(
    filename="document.pdf",
    embeddings=[[0.1, 0.2, ...]],
    chunks=["text chunk"],
    metadata={"source": "upload"},
)

hits = vector_store.search_embeddings(
    query_embeddings=[0.1, 0.2, ...],
    filenames=["document.pdf"],
    limit=10,
)
```

## Public API

`VectorStoreFactory.get_vector_store_connection(storage_config)` — returns a cached `VectorStore` for the given config. Pass `PostgresStorage` or `MilvusStorage`.

### `VectorStore`

| Method | Description |
|--------|-------------|
| `.store_embeddings(filename, embeddings, chunks, metadata)` | Upsert embeddings + chunks. |
| `.search_embeddings(query_embeddings, filenames, limit=10)` | Similarity search; optionally restrict to a set of filenames. |
| `.delete_embeddings(filename)` | Delete all embeddings for a filename. |

### Backends

**PostgresVectorStore** — module `common_lib.utils.vectors.postgres_vector_store`. Uses the same `POSTGRES_*` env vars as the database client.

**MilvusVectorStore** — module `common_lib.utils.vectors.milvus_vector_store`. Call `init_db()` once before first use.

| Variable | Description |
|----------|-------------|
| `MILVUS_HOST` | Milvus server host. |
| `MILVUS_PORT` | Milvus server port. |
