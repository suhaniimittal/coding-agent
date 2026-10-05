"""
Shared test configuration for voice-webhook tests.
"""

import sys
from pathlib import Path

# Add voice-webhook dir to path
_voice_webhook_dir = Path(__file__).resolve().parent.parent
if str(_voice_webhook_dir) not in sys.path:
    sys.path.insert(0, str(_voice_webhook_dir))
