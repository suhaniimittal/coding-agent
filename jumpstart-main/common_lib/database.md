# Database Connection

**Module:** `common_lib.database.connection`

The `Database` class manages PostgreSQL connections with pooling. Import the shared singleton `db` and call `init_db()` once at startup; then call `get_session()` whenever you need a session.

## Sync Usage

```python
from common_lib.database.connection import db

db.init_db()
session = db.get_session()
try:
    rows = session.execute(...).fetchall()
finally:
    session.close()
```

## Async Usage

```python
from common_lib.database.connection import db

db.init_async_db()

async with db.get_async_session() as session:
    rows = (await session.execute(...)).fetchall()

# Close the async pool on shutdown
await db.close_async_pool()
```

## Public API

| Method | Description |
|--------|-------------|
| `db.init_db()` | Initialise the sync engine and session factory. |
| `db.get_session(tenant_id=None)` | Return a sync SQLAlchemy `Session`. |
| `db.init_async_db()` | Initialise the async engine. |
| `db.get_async_session()` | Async context manager yielding an `AsyncSession`. |
| `db.close_async_pool()` | Dispose the async connection pool. |
| `db.check_async_connection()` | Async health check. |

> **Note:** Always close sync sessions (or use a `try/finally`) to avoid leaking connections.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | Database host. |
| `POSTGRES_USER` | `aetherion` | Database user. |
| `POSTGRES_PASSWORD` | `aetherion` | Database password. |
| `POSTGRES_DB` | `aetherion` | Database name. |
| `POSTGRES_PORT` | `5432` | Port. |
| `DATABASE_URL` | _none_ | Full DSN override; takes precedence over the individual `POSTGRES_*` vars. |
| `DB_POOL_SIZE` | `5` | Pool size. |
| `DB_MAX_OVERFLOW` | `10` | Max overflow connections. |
| `DB_POOL_TIMEOUT` | `30` | Pool acquisition timeout in seconds. |
| `DB_POOL_RECYCLE` | `3600` | Recycle connections older than this many seconds. |
| `DB_POOL_PRE_PING` | `true` | Run a pre-ping before checkout. |
