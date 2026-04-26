"""Pytest configuration: patch Google auth and env vars before any service
module is imported, so tests never hit real Google or Meta APIs.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make the repo root importable (so `import services.foo` works from tests).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Seed the env so config.py doesn't read None for required values.
os.environ.setdefault("GOOGLE_SHEETS_ID", "test-sheet-id")
os.environ.setdefault("GOOGLE_CALENDAR_ID", "test-calendar-id")
os.environ.setdefault("GOOGLE_CREDENTIALS_FILE", "credentials.json")
os.environ.setdefault("BUSINESS_TIMEZONE", "Asia/Dubai")
os.environ.setdefault("BUSINESS_WORKING_HOURS_START", "10:00")
os.environ.setdefault("BUSINESS_WORKING_HOURS_END", "20:00")
os.environ.setdefault("BUSINESS_LASER_END", "23:00")
os.environ.setdefault("BUSINESS_BREAK_START", "14:00")
os.environ.setdefault("BUSINESS_BREAK_END", "15:00")
os.environ.setdefault("BUSINESS_CLOSED_DAY", "Friday")
os.environ.setdefault("BUSINESS_HOLIDAYS", "")
os.environ.setdefault("BUSINESS_NAME", "Test Clinic")

# Patch the Google service-account loader and the `discovery.build` factory
# so importing `services.google_calendar` doesn't try to open a real
# credentials file or call Google. Do this BEFORE any test imports the module.
from google.oauth2 import service_account  # noqa: E402
from googleapiclient import discovery  # noqa: E402

service_account.Credentials.from_service_account_file = MagicMock(
    return_value=MagicMock()
)
discovery.build = MagicMock(return_value=MagicMock())
