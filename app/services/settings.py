"""Live settings (v3): attendance threshold & late threshold.

Values live in the Settings table so a teacher can change them on the
/settings page. Falls back to the config defaults when the table is
empty (fresh install) or the row is missing.
"""
from flask import current_app

from app import db
from app.models import Settings

DEFAULTS = {
    'attendance_threshold': '75.0',
    'late_threshold_minutes': '15',
}


def get_setting(key, default=None):
    row = Settings.query.filter_by(key=key).first()
    if row is None:
        return DEFAULTS.get(key, default)
    return row.value


def set_setting(key, value):
    row = Settings.query.filter_by(key=key).first()
    if row is None:
        row = Settings(key=key, value=str(value))
        db.session.add(row)
    else:
        row.value = str(value)
    db.session.commit()
    return row


def seed_defaults():
    """Insert the default rows if they are missing. Safe to call often."""
    for key, value in DEFAULTS.items():
        if Settings.query.filter_by(key=key).first() is None:
            db.session.add(Settings(key=key, value=value))
    db.session.commit()


def _as_float(key, fallback):
    try:
        return float(get_setting(key, str(fallback)))
    except (TypeError, ValueError):
        return float(fallback)


def get_threshold():
    """Live exam-eligibility threshold (%), default 75."""
    try:
        return _as_float('attendance_threshold',
                         current_app.config.get('ATTENDANCE_THRESHOLD', 75.0))
    except RuntimeError:  # no app context (e.g. a script)
        return 75.0


def get_late_threshold():
    """Live late threshold (minutes), default 15."""
    try:
        return _as_float('late_threshold_minutes',
                         current_app.config.get('LATE_THRESHOLD_MINUTES', 15))
    except RuntimeError:
        return 15.0
