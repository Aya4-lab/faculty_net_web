"""
WSGI entrypoint for production (gunicorn app:app).

Delegates to project_root/app_v2.py which serves the Faculty Net alpha UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT_DIR = Path(__file__).resolve().parent / "project_root"
if str(_PROJECT_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_DIR))

from app_v2 import app  # noqa: E402

__all__ = ["app"]
