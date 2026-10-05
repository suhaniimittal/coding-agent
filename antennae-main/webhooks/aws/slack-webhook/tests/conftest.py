"""
Shared test configuration for slack-webhook tests.
"""

import sys
from pathlib import Path

# Add slack-webhook dir to path
_slack_webhook_dir = Path(__file__).resolve().parent.parent
if str(_slack_webhook_dir) not in sys.path:
    sys.path.insert(0, str(_slack_webhook_dir))
