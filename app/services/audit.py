"""Audit trail helper (v3).

log_action() records who did what. It never raises — a logging failure
must never break the request that triggered it.
"""
from flask_login import current_user

from app import db
from app.models import AuditLog


def log_action(action, detail='', teacher_id=None):
    """Record one audit entry. Safe to call anywhere (request or script)."""
    try:
        if teacher_id is None:
            try:
                if current_user and current_user.is_authenticated:
                    teacher_id = current_user.id
            except Exception:
                teacher_id = None
        db.session.add(AuditLog(teacher_id=teacher_id, action=action,
                                detail=str(detail)[:500] if detail else ''))
        db.session.commit()
    except Exception:
        db.session.rollback()
