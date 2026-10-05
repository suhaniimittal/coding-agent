# OAuth Token Management

**Module:** `common_lib.database.connection`

OAuth tokens are stored encrypted in the integrations table. This section explains how to retrieve and decrypt tokens for use in your agents.

**Prerequisites:**

Set the encryption key in your `.env` file:
```
ENCRYPTION_KEY=your-fernet-encryption-key-here
```

The `ENCRYPTION_KEY` must be a valid Fernet key (32-byte URL-safe base64-encoded key).

**Integrations Table Schema:**

| Column | Type | Description |
|--------|------|-------------|
| `tenant_id` | text | Tenant identifier |
| `provider_key` | text | OAuth provider identifier (e.g., "slack", "google", "microsoft") |
| `access_token_enc` | bytea | Encrypted access token |
| `token_expires_at` | timestamptz | Token expiration timestamp |
| `active` | bool | Whether the integration is active |

**Example: Retrieving and Decrypting Tokens:**

```python
import os
from datetime import datetime, timezone
from cryptography.fernet import Fernet
from sqlalchemy import text
from common_lib.database.connection import db

# Initialize database
db.init_db()
session = db.get_session()

# Get encryption key from environment
encryption_key = os.getenv("ENCRYPTION_KEY")
if not encryption_key:
    raise ValueError("ENCRYPTION_KEY environment variable is not set")

fernet = Fernet(encryption_key.encode())

def get_oauth_token(tenant_id: str, provider_key: str):
    """
    Retrieve and decrypt OAuth token from integrations table.
    
    Parameters:
        tenant_id (str): Tenant identifier
        provider_key (str): OAuth provider key (e.g., "slack", "google")
    
    Returns:
        dict: Dictionary containing access_token, refresh_token, and expiration info
    """
    try:
        query = text("""
            SELECT
                access_token_enc,
                token_expires_at,
                scope,
                active
            FROM integrations
            WHERE tenant_id = :tenant_id
              AND provider_key = :provider_key
              AND active = true
            LIMIT 1
        """)

        result = session.execute(
            query,
            {"tenant_id": tenant_id, "provider_key": provider_key}
        ).fetchone()

        if not result:
            raise ValueError(
                f"No active integration found for tenant_id={tenant_id}, "
                f"provider_key={provider_key}"
            )

        # Decrypt the tokens
        access_token_enc = result.access_token_enc
        access_token = fernet.decrypt(access_token_enc).decode()

        return {
            "access_token": access_token,
            "token_expires_at": result.token_expires_at,
            "scope": result.scope,
            "is_expired": (
                result.token_expires_at is not None and
                result.token_expires_at < datetime.now(timezone.utc)
            )
        }
    finally:
        session.close()

# Usage example
tenant_id = "team-123"
provider_key = "slack"

token_data = get_oauth_token(tenant_id, provider_key)
print(f"Access Token: {token_data['access_token']}")
print(f"Is Expired: {token_data['is_expired']}")
```

> **Important Notes:**
> - Always set `ENCRYPTION_KEY` in your `.env` file before using token decryption
> - Tokens are encrypted at rest for security
> - Access tokens expire; always check expiration before use
