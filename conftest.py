"""Make the in-tree ``vmctl`` package importable when running the tests without
installing (e.g. a fresh clone: ``pytest``). Harmless when vmctl is installed."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
