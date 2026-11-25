"""Loads .env secrets only. All other config lives in the settings DB table."""
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://jobnavigator:password@db:5432/jobnavigator")
# Empty default = no auth on first run; a shipped sentinel key would be a security hole and
# breaks the documented blank-key first sign-in.
INITIAL_API_KEY = os.getenv("INITIAL_API_KEY", "")

# Feature flags (safe defaults: Gmail OAuth & unofficial LinkedIn disabled initially)
JN_ENABLE_GMAIL = os.getenv("JN_ENABLE_GMAIL", "0").strip().lower() in ("1", "true", "yes", "on")
JN_ENABLE_UNOFFICIAL_LINKEDIN = os.getenv("JN_ENABLE_UNOFFICIAL_LINKEDIN", "0").strip().lower() in ("1", "true", "yes", "on")

# Configurable Gemini model IDs (empty until verified against Gemini models & pricing)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL_TRIAGE = os.getenv("GEMINI_MODEL_TRIAGE", "").strip()
GEMINI_MODEL_FULL = os.getenv("GEMINI_MODEL_FULL", "").strip()

# Budget guard configuration
AI_MONTHLY_BUDGET_INR = float(os.getenv("AI_MONTHLY_BUDGET_INR", "500"))
USD_TO_INR_RATE = float(os.getenv("USD_TO_INR_RATE", "85.0"))
AI_BUDGET_SAFETY_MARGIN = float(os.getenv("AI_BUDGET_SAFETY_MARGIN", "1.15"))

# age public recipient key for encrypted backups (private identity key stays off-server)
BACKUP_AGE_RECIPIENT = os.getenv("BACKUP_AGE_RECIPIENT", "").strip()
