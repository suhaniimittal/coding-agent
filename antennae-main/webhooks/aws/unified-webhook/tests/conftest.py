"""
Shared test configuration for unified-webhook tests.
"""

import sys
from pathlib import Path

# Add unified-webhook dir to path
_unified_dir = Path(__file__).resolve().parent.parent
if str(_unified_dir) not in sys.path:
    sys.path.insert(0, str(_unified_dir))
