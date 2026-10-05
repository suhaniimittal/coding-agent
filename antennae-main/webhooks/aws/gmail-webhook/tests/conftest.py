"""
Shared test configuration for gmail-webhook tests.
"""

import sys
from pathlib import Path

# Add gmail-webhook dir to path
_gmail_webhook_dir = Path(__file__).resolve().parent.parent
if str(_gmail_webhook_dir) not in sys.path:
    sys.path.insert(0, str(_gmail_webhook_dir))
