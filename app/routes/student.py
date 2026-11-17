"""Student portal (v4): CMS ID + password login.

Replaces the v3 roll-number + PIN portal. A student logs in with the
CMS ID and password their teacher gave them (shown on the teacher's
Students page). The dashboard shows ONLY that student's own
attendance, computed from the real AttendanceRecord /
AttendanceSession tables — never another student's data.

Security: wrong CMS ID and wrong password give the SAME generic
message; logins are rate-limited (5 failed attempts per CMS ID + IP
in 15 minutes), same hand-rolled pattern as teacher login.
"""
import math
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)

from app import db
from app.models import AttendanceRecord, AttendanceSession, Schedule, Student
from app.routes.auth import validate_password
from app.services.settings import get_late_threshold, get_threshold

student_bp = Blueprint('student', __name__, url_prefix='/student')

# --- Hand-rolled login lockout (same pattern as teacher login) --------
_FAILED = {}
MAX_FAILED = 5
LOCKOUT_WINDOW = timedelta(minutes=15)


def reset_student_login_attempts():
    """Test helper: clear all recorded failed student logins."""
    _FAILED.clear()


def _failed_recently(key):
    now = datetime.now()
    attempts = [t for t in _FAILED.get(key, [])
                if now - t < LOCKOUT_WINDOW]
    _FAILED[key] = attempts
    return attempts


def _is_locked_out(cms_id, ip):
    return len(_failed_recently((cms_id, ip))) >= MAX_FAILED


def _record_failed(cms_id, ip):
    _FAILED.setdefault((cms_id, ip), []).append(datetime.now())


def _clear_failed(cms_id, ip):
    _FAILED.pop((cms_id, ip), None)


def _current_student():
    """The logged-in student, or None (from the Flask session)."""
    student_id = session.get('student_id')
    if not student_id:
        return None
    return Student.query.get(student_id)


def _login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if _current_student() is None:
            flash('Please log in with your CMS ID and password first.',
                  'info')
            return redirect(url_for('student.login'))
        return view(*args, **kwargs)
    return wrapped


# --- Login / logout ------------------------------------------------------
@student_bp.route('/login', methods=['GET', 'POST'])
def login():
    if _current_student() is not None:
        return redirect(url_for('student.dashboard'))

    if request.method == 'POST':
        cms_id = request.form.get('cms_id', '').strip().upper()
        password = request.form.get('password', '')
        ip = request.remote_addr or 'unknown'

        if _is_locked_out(cms_id, ip):
            flash('Too many failed login attempts. Please try again in '
                  '15 minutes.', 'danger')
            return render_template('student_login.html')

        student = Student.query.filter_by(cms_id=cms_id).first()
        # Same generic message for unknown CMS ID and wrong password.
        if student and student.check_portal_password(password):
            _clear_failed(cms_id, ip)
            session['student_id'] = student.id
            session['student_cms_id'] = student.cms_id
            session.permanent = True
            return redirect(url_for('student.dashboard'))

        _record_failed(cms_id, ip)
        current_app.logger.warning(
            f'Failed student login for {cms_id} from {ip}.')
        flash('Invalid CMS ID or password. Please try again.', 'danger')

    return render_template('student_login.html')


@student_bp.route('/logout')
def logout():
    session.pop('student_id', None)
    session.pop('student_cms_id', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('landing.index'))


# --- Dashboard -----------------------------------------------------------
WEEKDAY_ORDER = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2,
                 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}


def _subject_stats(student, threshold, late_minutes):
    """Per-subject attendance, computed from real records.

    Returns a list of dicts, one per subject the student has records
    for. Percentages are minutes-based (minutes present / minutes of
    class held), so they always match the numbers on the record.
    """
    records = (AttendanceRecord.query
               .join(AttendanceSession,
                     AttendanceRecord.session_id == AttendanceSession.id)
               .filter(AttendanceRecord.student_id == student.id)
               .order_by(AttendanceSession.session_date.desc(),
                         AttendanceSession.start_time.desc())
               .all())
    by_subject = {}
    for record in records:
        sess = record.session
        key = (sess.schedule_id, sess.course_name)
        entry = by_subject.setdefault(key, {
            'schedule': sess.schedule,
            'course_name': sess.course_name,
            'course_code': (sess.schedule.course_code
                            if sess.schedule else ''),
            'records': [],
        })
        entry['records'].append(record)

    subjects = []
    for entry in by_subject.values():
        recs = entry['records']
        held = len(recs)
        attended = sum(1 for r in recs if r.status != 'Absent')
        absent = sum(1 for r in recs if r.status == 'Absent')
        late = 0
        minutes_present = 0.0
        minutes_total = 0.0
        for r in recs:
            sess = r.session
            duration = (sess.total_duration_minutes or 0) or 100
            minutes_total += duration
            minutes_present += r.minutes_present or 0.0
            if r.first_seen and sess.start_time:
                late_after = sess.start_time + timedelta(minutes=late_minutes)
                if r.first_seen > late_after:
                    late += 1
        pct = round(minutes_present / minutes_total * 100, 1) \
            if minutes_total > 0 else 0.0
        avg_duration = minutes_total / held if held else 100.0
        t = threshold / 100.0
        # How many classes in a row must be attended to reach threshold?
        if pct >= threshold:
            status = 'Safe'
            if t > 0:
                can_miss = int((minutes_present / t - minutes_total)
                               // avg_duration)
            else:
                can_miss = 0
            can_miss = max(0, can_miss)
            plan = (f'You are safe — you can miss {can_miss} more '
                    f'class{"es" if can_miss != 1 else ""} and still stay '
                    f'above {threshold:.0f}%.' if can_miss > 0 else
                    f'You are above {threshold:.0f}% — attend the next '
                    f'class to keep your buffer.')
            to_attend = 0
        else:
            status = 'Defaulter' if pct < threshold - 5 else 'Warning'
            if t < 1:
                to_attend = math.ceil(
                    (t * minutes_total - minutes_present)
                    / (avg_duration * (1 - t)))
            else:
                to_attend = 0
            to_attend = max(0, to_attend)
            plan = (f'Attend the next {to_attend} class'
                    f'{"es" if to_attend != 1 else ""} in a row to reach '
                    f'{threshold:.0f}%.' if to_attend > 0 else
                    'Attend every class from now on to recover.')
        subjects.append({
            'course_code': entry['course_code'],
            'course_name': entry['course_name'],
            'held': held, 'attended': attended, 'absent': absent,
            'late': late, 'percentage': pct, 'status': status,
            'to_attend': to_attend, 'plan': plan,
        })
    subjects.sort(key=lambda s: s['percentage'])
    return subjects, records


@student_bp.route('/dashboard')
@_login_required
def dashboard():
    student = _current_student()
    threshold = get_threshold()
    late_minutes = get_late_threshold()

    subjects, records = _subject_stats(student, threshold, late_minutes)
    held = len(records)
    attended = sum(1 for r in records if r.status != 'Absent')
    absent = sum(1 for r in records if r.status == 'Absent')
    late = sum(s['late'] for s in subjects)
    if records:
        overall = round(sum(r.attendance_percentage for r in records)
                        / len(records), 1)
    else:
        overall = 0.0
    at_risk = [s for s in subjects if s['percentage'] < threshold]

    absent_records = [r for r in records if r.status == 'Absent']

    # Weekly timetable: my class's schedules (v5: by class group, not
    # just section — two classes can share a section name in different
    # semesters). Legacy students without a class fall back to section.
    timetable = []
    if student.teacher_id:
        if student.class_group_id:
            timetable = (Schedule.query
                         .filter_by(teacher_id=student.teacher_id,
                                    class_group_id=student.class_group_id)
                         .order_by(Schedule.start_time).all())
        else:
            timetable = (Schedule.query
                         .filter_by(teacher_id=student.teacher_id,
                                    section=student.section)
                         .order_by(Schedule.start_time).all())
        timetable.sort(key=lambda sc: (WEEKDAY_ORDER.get(sc.day_of_week, 7),
                                       sc.start_time))

    return render_template(
        'student_dashboard.html',
        student=student, threshold=threshold,
        overall=overall, held=held, attended=attended, absent=absent,
        late=late, subjects=subjects, at_risk=at_risk,
        absent_records=absent_records, records=records,
        timetable=timetable)


# --- Change password -----------------------------------------------------
@student_bp.route('/change-password', methods=['GET', 'POST'])
@_login_required
def change_password():
    student = _current_student()
    if request.method == 'POST':
        old = request.form.get('old_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')

        if not student.check_portal_password(old):
            flash('Your old password is incorrect.', 'danger')
        elif new != confirm:
            flash('New passwords do not match.', 'danger')
        else:
            pw_error = validate_password(new)
            if pw_error:
                flash(pw_error, 'danger')
            else:
                student.set_portal_password(new)
                db.session.commit()
                flash('Password changed successfully. Use your new '
                      'password next time you log in.', 'success')
                return redirect(url_for('student.dashboard'))
    return render_template('student_change_password.html',
                           student=student)
