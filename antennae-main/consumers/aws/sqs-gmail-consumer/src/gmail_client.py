# src/gmail_client.py
from __future__ import annotations

import base64
import json
import re
from email.message import EmailMessage

from common_lib.utils.logger import setup_logger
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.email_filter import get_label_config

logger = setup_logger(__name__)


class GmailAPIClient:
    """A client to interact with the Gmail API."""

    def __init__(self, token: str, email_address: str):
        """
        Initializes the Gmail client by loading the full credential JSON string
        (including access_token, refresh_token, client_id, etc.) and enabling
        automatic token refresh.
        """
        self.email_address = email_address

        # --- Authentication Setup ---
        try:
            # 1. Load the full token JSON string into a dictionary
            token_dict = json.loads(token)
            logger.debug(f"DEBUG: Loaded token dictionary keys: {list(token_dict.keys())}")

            # 2. Construct the Credentials object using the full token data
            #    Scopes are intentionally omitted — google-auth includes them in the
            #    refresh grant body when set, causing 'invalid_grant' on mismatch.
            self.creds = Credentials(
                token=token_dict.get("token"),  # access_token
                refresh_token=token_dict.get("refresh_token"),
                token_uri=token_dict.get("token_uri"),
                client_id=token_dict.get("client_id"),
                client_secret=token_dict.get("client_secret"),
            )

            # 3. Proactively refresh if the access token is expired
            if self.creds.expired or not self.creds.valid:
                if self.creds.refresh_token:
                    logger.info("Access token expired or invalid — attempting proactive refresh")
                    self.creds.refresh(Request())
                    logger.info("Token refreshed successfully")
                else:
                    logger.warning(
                        "Access token is expired and no refresh token available. "
                        "API calls will fail."
                    )

            self.service = build("gmail", "v1", credentials=self.creds)
            logger.info(f"Gmail service client initialized for: {email_address}")
        except Exception as e:
            logger.error(
                f"Failed to initialize Gmail client with full credentials: {e}", exc_info=True
            )
            raise

    def get_new_message_id(self, history_id: str) -> str | None:
        """
        Try to find new messages after given historyId.
        If none, fall back to the one that triggered it.
        """
        response = (
            self.service.users().history().list(userId="me", startHistoryId=history_id).execute()
        )

        history = response.get("history", [])
        for record in history:
            messages_added = record.get("messagesAdded", [])
            if messages_added:
                return messages_added[-1]["message"]["id"]

        # No new message found — fallback logic
        logger.warning(
            f"No new message found since history ID: {history_id}. "
            "Trying to fetch triggering message..."
        )

        try:
            # Get the most recent message directly
            messages_resp = (
                self.service.users()
                .messages()
                .list(userId="me", maxResults=1, q="in:inbox")
                .execute()
            )

            messages = messages_resp.get("messages", [])
            if messages:
                latest_message_id = messages[0]["id"]
                logger.info(
                    f"Fetched latest message ID {latest_message_id} "
                    f"as fallback for history ID {history_id}"
                )
                return latest_message_id
            else:
                logger.warning("No messages found in inbox during fallback check.")
                return None

        except Exception as e:
            logger.exception(f"Error fetching fallback message for history ID {history_id}: {e}")
            return None

    async def _fetch_message(self, msg_id: str) -> dict:
        """
        Fetch message from Gmail API.
        """
        logger.info(f"Fetching basic message info for ID: {msg_id}")

        try:
            message = (
                self.service.users()
                .messages()
                .get(userId=self.email_address, id=msg_id, format="full")
                .execute()
            )
            return message
        except HttpError as e:
            logger.error(f"Gmail API Error: Failed to get message {msg_id}: {e}")
            raise

    async def extract_basic_info(self, message: dict) -> tuple[str, str]:
        """
        Extract only message body and sender email (lightweight operation).
        Returns: (message_body, sender_email)
        """

        message_body = ""
        sender_email = ""

        # Extract Sender Email
        for header in message["payload"]["headers"]:
            if header["name"] == "From":
                # Basic regex to extract email from "Name <email@example.com>"
                match = re.search(r"<(.*?)>", header["value"])
                sender_email = match.group(1) if match else header["value"]
                logger.debug(f"DEBUG: Extracted Sender: {sender_email}")
                break

        # Function to decode Base64 URL-safe string
        def safe_base64_decode(data):
            return base64.urlsafe_b64decode(data + "=" * (4 - len(data) % 4))

        # Extract message body (lightweight - no deep parsing for attachments)
        if message["payload"].get("body") and message["payload"]["mimeType"] == "text/plain":
            data = message["payload"]["body"].get("data")
            if data:
                message_body = safe_base64_decode(data).decode("utf-8")
        elif "parts" in message["payload"]:
            # Look for text/plain in first level parts only (lightweight)
            for part in message["payload"]["parts"]:
                if part.get("mimeType") == "text/plain" and part["body"].get("data"):
                    message_body = safe_base64_decode(part["body"]["data"]).decode("utf-8")
                    break

        return message_body.strip(), sender_email

    async def build_body_eml(self, message: dict) -> bytes:
        """
        Build a minimal .eml file containing only headers and the email body
        (text/plain + text/html). Attachments are excluded.
        """

        headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}

        def safe_base64_decode(data):
            return base64.urlsafe_b64decode(data + "=" * (4 - len(data) % 4))

        text_body = ""
        html_body = ""

        def extract_body_parts(payload):
            nonlocal text_body, html_body
            mime = payload.get("mimeType", "")
            body_data = payload.get("body", {}).get("data")

            if mime == "text/plain" and body_data and not text_body:
                text_body = safe_base64_decode(body_data).decode("utf-8")
            elif mime == "text/html" and body_data and not html_body:
                html_body = safe_base64_decode(body_data).decode("utf-8")

            for part in payload.get("parts", []):
                extract_body_parts(part)

        extract_body_parts(message["payload"])

        eml = EmailMessage()
        for name in (
            "From",
            "To",
            "Cc",
            "Subject",
            "Date",
            "Message-ID",
            "In-Reply-To",
            "References",
        ):
            if name in headers:
                eml[name] = headers[name]

        if text_body:
            eml.set_content(text_body)
        if html_body:
            eml.add_alternative(html_body, subtype="html")

        logger.info("Built body-only .eml from message")
        return eml.as_bytes()

    async def extract_attachments(self, message: dict, msg_id: str) -> list[tuple[str, bytes]]:
        """
        Extract all attachments from message (heavier operation, only called for filtered emails).
        Returns: List of (filename, file_content) tuples for all attachments found
        """

        # Function to decode Base64 URL-safe string
        def safe_base64_decode(data):
            return base64.urlsafe_b64decode(data + "=" * (4 - len(data) % 4))

        attachments = []

        # Recursive function to find all attachments
        async def find_attachments(parts):
            for part in parts:
                # Look for attachment
                if part.get("filename") and part.get("body") and part["body"].get("attachmentId"):
                    filename = part["filename"]
                    attachment_id = part["body"]["attachmentId"]

                    logger.info(f"Found attachment: {filename} with ID: {attachment_id}")

                    try:
                        # Get attachment data
                        att_response = (
                            self.service.users()
                            .messages()
                            .attachments()
                            .get(userId=self.email_address, messageId=msg_id, id=attachment_id)
                            .execute()
                        )

                        file_content = safe_base64_decode(att_response["data"])
                        attachments.append((filename, file_content))
                        logger.debug(f"DEBUG: Attachment '{filename}' extracted successfully.")

                    except Exception as e:
                        logger.error(f"Failed to extract attachment '{filename}': {e}")
                        continue

                # Recurse into nested parts
                if "parts" in part:
                    await find_attachments(part["parts"])

        if "parts" in message["payload"]:
            await find_attachments(message["payload"]["parts"])

        logger.info(f"Total attachments found: {len(attachments)}")
        return attachments

    async def ensure_label_exists(self) -> str:
        """
        Ensure the configured label exists, creating it if necessary. Returns the label ID.
        Uses centralized label configuration from get_label_config().

        Returns:
            str: The label ID
        """
        label_config = get_label_config()
        label_name = label_config.name
        color_bg = label_config.color_bg
        color_text = label_config.color_text

        try:
            # First, try to find existing label
            labels_result = self.service.users().labels().list(userId="me").execute()
            labels = labels_result.get("labels", [])

            for label in labels:
                if label["name"] == label_name:
                    logger.info(f"Found existing label '{label_name}' with ID: {label['id']}")
                    return label["id"]

            # Label doesn't exist, create it
            logger.info(f"Creating new label: {label_name}")
            label_object = {
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
                "color": {"backgroundColor": color_bg, "textColor": color_text},
            }

            created_label = (
                self.service.users().labels().create(userId="me", body=label_object).execute()
            )
            logger.info(f"Created label '{label_name}' with ID: {created_label['id']}")
            return created_label["id"]

        except HttpError as e:
            logger.error(f"Failed to create/get label '{label_name}': {e}")
            raise

    async def apply_label_and_mark_read(
        self,
        message_id: str,
        label_id: str,
        mark_as_read: bool = True,
        remove_from_inbox: bool = False,
    ):
        """
        Apply label to message, optionally mark as read and remove from inbox.

        Args:
            message_id: The message ID to modify
            label_id: The label ID to apply
            mark_as_read: Whether to mark the message as read
            remove_from_inbox: Whether to remove from inbox (move to label folder)
        """
        try:
            add_labels = [label_id]
            remove_labels = []

            if mark_as_read:
                remove_labels.append("UNREAD")

            if remove_from_inbox:
                remove_labels.append("INBOX")

            modify_request = {"addLabelIds": add_labels, "removeLabelIds": remove_labels}

            self.service.users().messages().modify(
                userId="me", id=message_id, body=modify_request
            ).execute()

            action_summary = []
            action_summary.append("applied label")
            if mark_as_read:
                action_summary.append("marked as read")
            if remove_from_inbox:
                action_summary.append("moved to label folder")

            logger.info(f"Successfully {', '.join(action_summary)} for message {message_id}")

        except HttpError as e:
            logger.error(f"Failed to modify message {message_id}: {e}")
            raise

    async def get_label_id(self, label_name: str) -> str | None:
        """
        Get label ID by name.

        Args:
            label_name: Name of the label

        Returns:
            Optional[str]: Label ID if found, None otherwise
        """
        try:
            labels_result = self.service.users().labels().list(userId="me").execute()
            labels = labels_result.get("labels", [])

            for label in labels:
                if label["name"] == label_name:
                    return label["id"]

            return None

        except HttpError as e:
            logger.error(f"Failed to get labels: {e}")
            return None

    async def reply_to_email(
        self,
        original_msg_id: str,
        sender_email: str,
        response_s3_key: str,
    ):
        """
        Sends a reply email with the agent's generated attachment.
        (Placeholder logic as per original requirement)
        """
        logger.info(
            f"Preparing to send placeholder reply to {sender_email} (Msg ID: {original_msg_id})"
        )

        # Simple reply structure
        message_body = f"""
        Dear Sender,
        
        Your request has been processed by our AI agent.
        
        The generated response/attachment (stored at S3 key: {response_s3_key})
        will be attached to this email.
        
        ---
        This is an automated response.
        """

        # Placeholder for simple send:
        raw_message = base64.urlsafe_b64encode(message_body.encode("utf-8")).decode("utf-8")

        try:
            self.service.users().messages().send(
                userId=self.email_address, body={"raw": raw_message}
            ).execute()
            logger.info("Successfully sent placeholder reply.")
        except HttpError as e:
            logger.error(f"Failed to send reply to {sender_email}: {e}")
            raise
