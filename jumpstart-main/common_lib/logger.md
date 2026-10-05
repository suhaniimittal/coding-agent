# Logger

**Module:** `common_lib.utils.logger`

`setup_logger(name)` returns a configured `logging.Logger`. Choose color or JSON output via the `LOG_FORMAT` environment variable.

```python
from common_lib.utils.logger import setup_logger

logger = setup_logger(__name__)
logger.info("Application started")
```

## Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Logger name (typically `__name__`). |

**Returns:** `logging.Logger`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Standard Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `LOG_FORMAT` | `color` | `color` for human-readable colored output; `json` for structured JSON logs. |
