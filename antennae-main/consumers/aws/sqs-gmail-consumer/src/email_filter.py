# src/email_filter.py
from __future__ import annotations

import os
from dataclasses import dataclass

from common_lib.utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass(frozen=True)
class LabelConfig:
    """Centralized Gmail label configuration."""

    name: str = "Aetherion"
    color_bg: str = "#f691b2"  # Rose/Pink - valid Gmail label color
    color_text: str = "#ffffff"  # White text - valid Gmail label text color
    mark_as_read: bool = True
    remove_from_inbox: bool = True


def get_label_config() -> LabelConfig:
    """
    Returns the centralized label configuration.
    Single source of truth for label settings across the application.
    """
    return LabelConfig()


class EmailFilter:
    """
    Handles email sender filtering logic for determining which emails should trigger the agent.
    """

    def __init__(self):
        self.allowed_senders: set[str] = set()
        self.blocked_senders: set[str] = set()
        self.allow_domains: set[str] = set()
        self.block_domains: set[str] = set()
        self.whitelist_mode: bool = (
            True  # True = only allow listed senders, False = block listed senders
        )
        self._load_filter_config()

    def _load_filter_config(self):
        """
        Load email filter configuration from environment variables only.
        """
        try:
            logger.info("Loading email filter configuration from environment variables")
            self._load_from_env_vars()

            logger.info(f"Email filter loaded - Whitelist mode: {self.whitelist_mode}")
            logger.info(
                f"Allowed senders: {len(self.allowed_senders)}, "
                f"Allowed domains: {len(self.allow_domains)}"
            )
            logger.info(
                f"Blocked senders: {len(self.blocked_senders)}, "
                f"Blocked domains: {len(self.block_domains)}"
            )

        except Exception as e:
            logger.error(f"Error loading email filter config from environment: {e}")
            # Set safe defaults if environment loading fails
            self.whitelist_mode = True
            self.allowed_senders = set()
            self.blocked_senders = set()
            self.allow_domains = set()
            self.block_domains = set()
            logger.warning("Using default (empty) email filter configuration")

    def _parse_config(self, config: dict):
        """Parse configuration dictionary"""
        self.whitelist_mode = config.get("whitelist_mode", True)

        # Parse allowed/blocked senders
        self.allowed_senders = set(
            email.lower().strip() for email in config.get("allowed_senders", [])
        )
        self.blocked_senders = set(
            email.lower().strip() for email in config.get("blocked_senders", [])
        )

        # Parse allowed/blocked domains
        self.allow_domains = set(
            domain.lower().strip() for domain in config.get("allow_domains", [])
        )
        self.block_domains = set(
            domain.lower().strip() for domain in config.get("block_domains", [])
        )

        logger.info(f"Email filter loaded - Whitelist mode: {self.whitelist_mode}")
        logger.info(
            f"Allowed senders: {len(self.allowed_senders)}, Allowed domains: "
            f"{len(self.allow_domains)}"
        )
        logger.info(
            f"Blocked senders: {len(self.blocked_senders)}, Blocked domains: "
            f"{len(self.block_domains)}"
        )

    def _load_from_env_vars(self):
        """Load configuration from individual environment variables"""
        # Whitelist mode
        self.whitelist_mode = (
            os.environ.get("EMAIL_FILTER_WHITELIST_MODE", "true").lower() == "true"
        )

        # Allowed senders (comma-separated)
        allowed_str = os.environ.get("EMAIL_FILTER_ALLOWED_SENDERS", "")
        if allowed_str:
            self.allowed_senders = set(
                email.lower().strip() for email in allowed_str.split(",") if email.strip()
            )

        # Blocked senders (comma-separated)
        blocked_str = os.environ.get("EMAIL_FILTER_BLOCKED_SENDERS", "")
        if blocked_str:
            self.blocked_senders = set(
                email.lower().strip() for email in blocked_str.split(",") if email.strip()
            )

        # Allowed domains (comma-separated)
        allow_domains_str = os.environ.get("EMAIL_FILTER_ALLOW_DOMAINS", "")
        if allow_domains_str:
            self.allow_domains = set(
                domain.lower().strip() for domain in allow_domains_str.split(",") if domain.strip()
            )

        # Blocked domains (comma-separated)
        block_domains_str = os.environ.get("EMAIL_FILTER_BLOCK_DOMAINS", "")
        if block_domains_str:
            self.block_domains = set(
                domain.lower().strip() for domain in block_domains_str.split(",") if domain.strip()
            )

    def should_process_email(self, sender_email: str) -> bool:
        """
        Determine if an email from the given sender should be processed.

        Args:
            sender_email (str): The sender's email address

        Returns:
            bool: True if the email should be processed, False otherwise
        """
        if not sender_email:
            logger.warning("Empty sender email provided to filter")
            return False

        sender_email = sender_email.lower().strip()
        domain = self._extract_domain(sender_email)

        logger.debug(f"Checking email filter for: {sender_email} (domain: {domain})")

        # Check blocked lists first (takes precedence)
        if sender_email in self.blocked_senders:
            logger.info(f"Email blocked - sender in blocked list: {sender_email}")
            return False

        if domain and domain in self.block_domains:
            logger.info(f"Email blocked - domain in blocked list: {domain}")
            return False

        if self.whitelist_mode:
            # In whitelist mode, only allow specifically listed senders/domains
            allowed = sender_email in self.allowed_senders or (
                domain and domain in self.allow_domains
            )

            if allowed:
                logger.info(f"Email allowed - sender/domain in allow list: {sender_email}")
            else:
                logger.info(f"Email filtered out - not in allow list: {sender_email}")

            return allowed
        else:
            # In blacklist mode, allow all except blocked ones
            logger.info(f"Email allowed - not in block list: {sender_email}")
            return True

    def _extract_domain(self, email: str) -> str | None:
        """Extract domain from email address"""
        # Input validation first
        if not isinstance(email, str) or not email.strip():
            return None

        try:
            if "@" in email:
                domain = email.split("@")[1].strip().lower()
                return domain if domain else None
        except (IndexError, AttributeError):
            # IndexError: if split doesn't produce enough parts
            # AttributeError: if lower() fails (shouldn't happen with strings)
            pass
        return None

    def add_allowed_sender(self, email: str):
        """Dynamically add an allowed sender"""
        self.allowed_senders.add(email.lower().strip())
        logger.info(f"Added allowed sender: {email}")

    def add_blocked_sender(self, email: str):
        """Dynamically add a blocked sender"""
        self.blocked_senders.add(email.lower().strip())
        logger.info(f"Added blocked sender: {email}")

    def remove_allowed_sender(self, email: str):
        """Remove a sender from the allowed list"""
        self.allowed_senders.discard(email.lower().strip())
        logger.info(f"Removed allowed sender: {email}")

    def remove_blocked_sender(self, email: str):
        """Remove a sender from the blocked list"""
        self.blocked_senders.discard(email.lower().strip())
        logger.info(f"Removed blocked sender: {email}")

    def get_filter_status(self) -> dict:
        """Get current filter configuration status"""
        return {
            "whitelist_mode": self.whitelist_mode,
            "allowed_senders_count": len(self.allowed_senders),
            "blocked_senders_count": len(self.blocked_senders),
            "allow_domains_count": len(self.allow_domains),
            "block_domains_count": len(self.block_domains),
            "allowed_senders": sorted(list(self.allowed_senders)),
            "blocked_senders": sorted(list(self.blocked_senders)),
            "allow_domains": sorted(list(self.allow_domains)),
            "block_domains": sorted(list(self.block_domains)),
        }


# Global filter instance
email_filter = EmailFilter()
