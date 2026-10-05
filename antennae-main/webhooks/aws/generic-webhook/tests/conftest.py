"""
Shared test configuration for generic-webhook tests.
"""

import sys
from pathlib import Path

# Add generic-webhook dir to path
_generic_dir = Path(__file__).resolve().parent.parent
if str(_generic_dir) not in sys.path:
    sys.path.insert(0, str(_generic_dir))
