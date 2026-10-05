# Common Library

The `common_lib` package provides shared infrastructure utilities for Aetherion agents and tools — database, storage, logging, embeddings, vector stores, execution tracking, payload codecs, and authentication.

| Component | Description |
|-----------|-------------|
| **[Database Connection](database.md)** | PostgreSQL connections (sync and async) with pooling and multi-tenant support. |
| **[Storage Client](storage.md)** | S3-compatible object storage (MinIO local, AWS S3 in production). |
| **[Logger](logger.md)** | Standardised logger with color or JSON output. |
| **[Embeddings](embeddings.md)** | Generate and process text embeddings from PDF/DOCX/CSV/XLSX/JSON/HTML/MD/TXT files. |
| **[Vector Stores](vector_stores.md)** | Store and search vector embeddings (Postgres pgvector, Milvus). |
| **[Execution Helpers](execution.md)** | Track agent and activity runs (create, mark started/completed, status, schedules). |
| **[Payload Codec](payload_codec.md)** | Offload large workflow payloads to Redis to stay under message-size limits. |
| **[Data Models](data_models.md)** | ORM models for Agent, AgentRun, Activity, ActivityRun, WorkItem. |
| **[OAuth Token Management](oauth_token_management.md)** | Retrieve and decrypt stored OAuth tokens. |
