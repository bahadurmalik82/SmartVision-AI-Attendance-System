"""Automatic attendance-drop alerts (v8.3).

A student who drops into a worse attendance band gets the same
warning email the Defaulters page sends by hand — automatically,
the moment their attendance changes. The alerts fire on the two
events that change attendance: a class session closing
(``finalize_session``) and a manual correction (``correct_record``).
There is no extra scheduled job.

Bands mirror the Defaulters logic (threshold = the live Settings
threshold, default 75%):
  * avg >= threshold -> 'eligible'   (no email)
  * 70.0 <= avg < threshold -> 'warning'
  * avg < 70.0 -> 'barred'

The aggregate % is the SAME definition as
``get_student_aggregates`` in app/routes/analytics.py (unfiltered):
the mean of ``attendance_percentage`` over ALL of the student's
records. A student with NO records at all is skipped — they have
not dropped anything; their class simply has not run yet (alerting
them would email every student of a class that never met).

Each band is emailed at most once in a row per student: the
StudentAlertState row remembers the band of the last email sent.
A student who recovers to 'eligible' and drops again gets a new
email. A student with no email on file is skipped silently.

This module NEVER raises: every per-student step is guarded, and a
failed send is logged, not retried — the next attendance change
tries again.
"""
from datetime import datetime

from flask import current_app

from app import db
from app.models import Student, StudentAlertState, Teacher
from app.services.audit import log_action
from app.services.email_service import send_defaulter_alert
from app.services.settings import get_threshold

WARNING_FLOOR = 70.0


def _band(avg_pct, threshold):
    """'eligible' / 'warning' / 'barred' for one aggregate %."""
    if avg_pct >= threshold:
        return 'eligible'
    if avg_pct >= WARNING_FLOOR:
        return 'warning'
    return 'barred'


def _student_avg_percentage(student):
    """Overall aggregate % — mean of attendance_percentage over ALL
    of the student's records (same as get_student_aggregates,
    unfiltered). Returns None when the student has no records."""
    records = student.attendance_records
    if not records:
        return None
    return round(
        sum(r.attendance_percentage for r in records) / len(records), 1)


def check_and_send_drop_alerts(teacher_id, student_ids=None, threshold=None):
    """Send a warning email the first time each student drops into a
    worse band. Never raises. Never sends the same band twice in a
    row for a student. Commits the state changes it makes."""
    try:
        threshold = (float(threshold) if threshold is not None
                     else get_threshold())
        # v9.3: the warning is signed with the teacher's (current)
        # name, read live from the DB — so a name changed on the
        # Settings page shows in the next alert email.
        teacher = Teacher.query.get(teacher_id) if teacher_id else None
        teacher_name = teacher.name if teacher else None
    except (TypeError, ValueError):
        threshold = 75.0

    try:
        query = Student.query.filter_by(teacher_id=teacher_id)
        if student_ids:
            query = query.filter(Student.id.in_(list(student_ids)))
        students = query.all()
    except Exception as exc:
        try:
            current_app.logger.warning(
                f'Attendance alerts: could not list students: {exc}')
        except Exception:
            pass
        return

    for student in students:
        try:
            avg_pct = _student_avg_percentage(student)
            if avg_pct is None:
                # No attendance yet — nothing dropped. Skip silently.
                continue
            kind = _band(avg_pct, threshold)
            state = StudentAlertState.query.get(student.id)
            if state is None:
                state = StudentAlertState(student_id=student.id,
                                          last_kind='eligible')
                db.session.add(state)

            if kind == 'eligible':
                if state.last_kind != 'eligible':
                    state.last_kind = 'eligible'
                    state.last_sent_at = None
                    db.session.commit()
                continue
            if kind == state.last_kind:
                # Already alerted for this band — do not send again.
                continue
            if not student.email:
                # No address on file: skip silently (no log spam).
                # last_kind is left alone, so the alert goes out on
                # the next change once an email is added.
                continue

            sent, message = send_defaulter_alert(
                student.email, student.name, avg_pct, threshold=threshold,
                teacher_name=teacher_name)
            if sent:
                state.last_kind = kind
                state.last_sent_at = datetime.now()
                db.session.commit()
                log_action(
                    'attendance_alert',
                    f'Auto alert to {student.email}: {kind} at {avg_pct}%.',
                    teacher_id=teacher_id)
            else:
                try:
                    current_app.logger.warning(
                        f'Attendance alert not sent to {student.email}: '
                        f'{message}')
                except Exception:
                    pass
        except Exception as exc:
            db.session.rollback()
            try:
                current_app.logger.warning(
                    f'Attendance alert failed for student '
                    f'{getattr(student, "id", "?")}: {exc}')
            except Exception:
                pass
