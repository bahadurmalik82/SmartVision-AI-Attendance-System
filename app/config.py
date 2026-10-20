import os
import secrets
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file
load_dotenv(BASE_DIR / '.env')

# The secret key must NEVER fall back to a hard-coded value: anyone who
# reads the source could forge login sessions with it. Production
# refuses to start without one (see ProductionConfig below).
_ENV_SECRET = os.environ.get('SECRET_KEY') or ''
if _ENV_SECRET in ('', 'change-me-to-a-long-random-secret'):
    _ENV_SECRET = ''


# True only when a real (non-placeholder) SECRET_KEY was supplied.
SECRET_KEY_IS_REAL = bool(_ENV_SECRET)


class Config:
    """Base application configuration."""
    SECRET_KEY = _ENV_SECRET or secrets.token_hex(32)

    # Database: Default to local SQLite in data/, or use MySQL if configured (for Hostinger)
    db_url = os.environ.get('DATABASE_URL')
    if not db_url or db_url == 'sqlite:///attendance.db':
        db_path = (BASE_DIR / 'data' / 'attendance.db').as_posix()
        db_url = f"sqlite:///{db_path}"

    SQLALCHEMY_DATABASE_URI = db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Uploads: reject anything bigger than 64 MB at the WSGI level.
    # (v5: a bulk face-photo ZIP for a whole class can be large —
    # 3 photos per student. Individual photos are still limited to
    # 5 MB each, and ZIPs are checked again in the upload route.)
    MAX_CONTENT_LENGTH = 64 * 1024 * 1024

    # Session cookie safety (Secure is switched on for production below).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # Upload directories
    UPLOAD_FOLDER = BASE_DIR / 'data' / 'student_photos'
    EMBEDDINGS_FOLDER = BASE_DIR / 'data' / 'embeddings'
    SAVED_MODELS_FOLDER = BASE_DIR / 'saved_models'

    # Attendance policy settings (University criteria).
    # These are only the defaults — the live values live in the Settings
    # table and can be changed on the /settings page.
    ATTENDANCE_THRESHOLD = 75.0  # Percentage required for exam eligibility
    LATE_THRESHOLD_MINUTES = 15   # Arriving after 15 mins is marked Late
    MIN_ATTENDANCE_MINUTES_RATIO = 0.50  # Must attend at least 50% of class time to avoid Absent


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    """Used by unit tests: in-memory DB, no background scheduler."""
    TESTING = True
    DEBUG = False
    SECRET_KEY = 'test-secret-key-for-unit-tests-only'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    # NOTE: the "no SECRET_KEY in production" check lives in
    # create_app() (app/__init__.py) — Flask's from_object() reads
    # class attributes and never calls __init__, so a check here
    # would silently never run.


config_by_name = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
