# src/db_utils.py
from __future__ import annotations

import json
import os
from datetime import datetime

from common_lib.database.connection import db
from common_lib.utils.logger import setup_logger
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

logger = setup_logger(__name__)
load_dotenv()

# --- CONFIGURATION (Loaded from .env) ---
# Required for Refresh flow, which sends these to Google's OAuth server
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")
TOKEN_URI = "https://oauth2.googleapis.com/token"

# Scopes are intentionally NOT included in the Credentials object.
# google-auth sends scopes in the refresh grant request body when they are set,
# and a mismatch with the originally-granted scopes causes 'invalid_grant'.
# Scopes are only relevant during the initial authorization (handled by milkyway).


fernet_key = os.getenv("ENCRYPTION_KEY")
if not fernet_key:
    raise RuntimeError("ENCRYPTION_KEY environment variable is not set.")
_f = Fernet(fernet_key.encode("utf-8"))


def decrypt_str(b: bytes) -> str:
    """Decrypts a byte string using Fernet."""
    try:
        return _f.decrypt(b).decode("utf-8")
    except InvalidToken:
        logger.error("Invalid decryption token. Check key or encrypted data.")
        raise


def get_decrypted_value(encrypted_bytes: bytes | memoryview | None) -> str | None:
    """Helper to safely handle and decrypt a token column."""
    if not encrypted_bytes:
        logger.debug("DEBUG: Encountered null or empty encrypted bytes.")
        return None

    if isinstance(encrypted_bytes, memoryview):
        encrypted_bytes = encrypted_bytes.tobytes()

    try:
        return decrypt_str(encrypted_bytes)
    except Exception as e:
        logger.error(f"Failed to decrypt token bytes: {e}")
        return None


async def get_db_session() -> Session:
    """Creates a tenant-specific DB session manually."""
    try:
        session = db.get_session()
        logger.debug("DEBUG: Successfully acquired DB session.")
        return session
    except OperationalError:
        logger.error("Database not found or is misconfigured.", exc_info=True)
        raise Exception("Database not found or is misconfigured.")
    except Exception as e:
        logger.critical(f"Unexpected DB error during session creation: {e}", exc_info=True)
        raise Exception(f"Unexpected DB error: {e}")


async def decrypt_token(tenant_id: str, LISTENER_EMAIL: str) -> str:
    """
    Fetches and decrypts the Gmail tokens, and formats them into a JSON string
    containing all fields necessary for a self-refreshing Credentials object.

    Returns: A JSON string containing access_token, refresh_token, client_id, etc.
    """
    session: Session | None = None
    try:
        if not GMAIL_CLIENT_ID or not GMAIL_CLIENT_SECRET:
            raise RuntimeError(
                "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set in the "
                "environment for token refresh."
            )

        logger.info(f"Fetching Gmail tokens for tenant ID: {tenant_id} and mail {LISTENER_EMAIL}")
        session = await get_db_session()

        token_record = session.execute(
            text("""
                SELECT access_token_enc, refresh_token_enc, token_expires_at
                FROM integrations 
                WHERE tenant_id = :tenant_id
                  AND provider_key = 'gmail'
                  AND active = true
                  AND external_user_id = :LISTENER_EMAIL
            """),
            {"tenant_id": tenant_id, "LISTENER_EMAIL": LISTENER_EMAIL},
        ).fetchone()

        if not token_record:
            raise Exception(f"No active Gmail integration found for tenant ID: {tenant_id}")

        # Unpack the fetched data
        encrypted_access_token, encrypted_refresh_token, token_expires_at = token_record

        # Decrypt tokens
        access_token = get_decrypted_value(encrypted_access_token)
        refresh_token = get_decrypted_value(encrypted_refresh_token)

        if not access_token:
            raise Exception(f"Missing decrypted access_token for tenant ID: {tenant_id}")

        # A refresh token is crucial for long-lived apps; flag if missing
        if not refresh_token:
            logger.warning(
                f"Refresh token is missing for tenant {tenant_id}. "
                "Token will not refresh when expired."
            )

        logger.info(f"Decrypted tokens successfully for tenant {tenant_id}")

        full_token_data = {
            "token": access_token,
            "refresh_token": refresh_token,
            "token_uri": TOKEN_URI,
            "client_id": GMAIL_CLIENT_ID,
            "client_secret": GMAIL_CLIENT_SECRET,
            "expiry": token_expires_at.isoformat()
            if isinstance(token_expires_at, datetime)
            else None,
        }

        logger.debug(f"DEBUG: Constructed token JSON keys: {list(full_token_data.keys())}")
        return json.dumps(full_token_data)

    except Exception as e:
        logger.error(
            f"CRITICAL: Error fetching, decrypting, or formatting token data: {e}", exc_info=True
        )
        raise
    finally:
        if session:
            session.close()
