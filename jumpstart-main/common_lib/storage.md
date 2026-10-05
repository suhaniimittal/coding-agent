# Storage Client

**Module:** `common_lib.storage.storage_client`

The `StorageService` class provides S3-compatible object storage. Import the shared singleton `storage`, call `init_client()` once, then use `store_object()` / `retrieve()`.

## Quick Example

```python
from common_lib.storage.storage_client import storage, RetrievalMode

storage.init_client()

team_id = "team-123"  # bucket name MUST be your team_id

# Upload
bucket, key = storage.store_object(
    team_id,
    "documents/file.pdf",
    pdf_bytes,
    "application/pdf",
)

# Full object
data = storage.retrieve(team_id, "file.pdf", RetrievalMode.FULL_OBJECT)

# Line by line (text files)
for line in storage.retrieve(team_id, "file.txt", RetrievalMode.LINE_BY_LINE):
    process(line)

# Chunked (large files; 1024-byte chunks)
for chunk in storage.retrieve(team_id, "large.bin", RetrievalMode.CHUNKED):
    process_chunk(chunk)
```

> ⚠️ **Always use `team_id` as the bucket name.** Do not create new buckets or use arbitrary names.

## Public API

| Method | Description |
|--------|-------------|
| `storage.init_client()` | Initialise the S3 client. Picks up local credentials or EKS IRSA automatically. |
| `storage.ensure_bucket_exists(bucket_name)` | Create the bucket if missing. |
| `storage.store_object(bucket_name, object_key, data, content_type)` | Upload bytes; returns `(bucket_name, object_key)`. |
| `storage.retrieve(bucket_name, object_key, retrieval_mode)` | Download an object. |
| `storage.local_download_pdf(bucket_name, file_name, file_path)` | Download an object directly to local disk. |

### `RetrievalMode`

| Mode | Returns |
|------|---------|
| `RetrievalMode.FULL_OBJECT` | `bytes` — full object in memory. |
| `RetrievalMode.LINE_BY_LINE` | Generator of UTF-8 strings, one per line. |
| `RetrievalMode.CHUNKED` | Generator of 1024-byte chunks. |

## Environment Variables

| Variable | Default / Behaviour |
|----------|---------------------|
| `APP_ENV` | `local`, `dev`, `prod`. Controls auth strategy. |
| `STORAGE_ENDPOINT` | S3 endpoint (e.g., `localhost:9000` for MinIO). |
| `STORAGE_REGION` | AWS region. |
| `STORAGE_SECURE` | `true`/`false` for HTTPS. |
| `STORAGE_ACCESS_KEY` | Access key (local dev only). |
| `STORAGE_SECRET_KEY` | Secret key (local dev only). |
| `STORAGE_SESSION_TOKEN` | Session token (local dev only). |
| `S3_ADDRESSING_STYLE` | `path` or `virtual`. |
| `AWS_DEFAULT_REGION` | Standard AWS region fallback. |

> **In EKS, the client uses IRSA** — do not set `STORAGE_ACCESS_KEY`/`SECRET_KEY` in production.
