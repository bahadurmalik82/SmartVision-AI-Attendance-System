"""Email service (Phase 7, v8.2).

Sends defaulter warning emails over SMTP, plus a test email the
teacher can send to their own inbox from the Settings page.
Configuration comes from environment variables (see .env.example):

    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM (defaults to SMTP_USER), SMTP_USE_TLS (default true)

If SMTP is not configured, sending fails *gracefully*: it returns
(False, "Email is not configured...") and logs a warning instead of
raising — the app must never 500 just because email is not set up.

Sending never raises: a bad port falls back to 587, and common
failures (wrong App Password, unreachable server) are reported in
plain words with the raw error kept after a dash for diagnosis.
"""

import html
import os
import smtplib
import socket
import ssl
from email.message import EmailMessage

NOT_CONFIGURED_MSG = (
    "Email is not configured (set SMTP_HOST / SMTP_USER / "
    "SMTP_PASSWORD in .env). No email was sent."
)


def _parse_port():
    """SMTP port from the environment. Empty or non-numeric values fall
    back to 587 instead of raising."""
    raw = (os.environ.get("SMTP_PORT") or "").strip()
    if not raw:
        return 587
    try:
        return int(raw)
    except ValueError:
        return 587


def is_configured():
    """True only when a host and credentials are present."""
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
                and os.environ.get("SMTP_PASSWORD"))


def email_status():
    """What the Settings page shows about email. Never includes the
    password."""
    return {
        "configured": is_configured(),
        "host": os.environ.get("SMTP_HOST") or None,
        "user": os.environ.get("SMTP_USER") or None,
        "port": _parse_port(),
        "use_tls": os.environ.get("SMTP_USE_TLS", "true").lower()
                   in ("1", "true", "yes"),
    }


def _friendly_error(exc, to_email):
    """Plain-words explanation first, raw error after a dash so the
    cause can still be diagnosed."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("Gmail did not accept the sign-in. Check the App Password "
                "(16 letters, no spaces) and that 2-Step Verification is "
                f"on. - {exc}")
    if isinstance(exc, (socket.gaierror, ConnectionRefusedError,
                        TimeoutError, smtplib.SMTPConnectError)):
        return ("Could not reach the mail server. Check SMTP_HOST and your "
                f"internet connection. - {exc}")
    return f"Could not send email to {to_email}: {exc}"


def _send(msg):
    """Send one prepared EmailMessage over SMTP.

    Returns (sent: bool, error_message: str | None) — None on success.
    Never raises.
    """
    host = os.environ["SMTP_HOST"]
    port = _parse_port()
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
    try:
        if use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls(context=context)
                server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.login(user, password)
                server.send_message(msg)
        return True, None
    except Exception as exc:
        return False, _friendly_error(exc, msg["To"])


def send_defaulter_alert(to_email, student_name, avg_percentage,
                         course_name="your course", threshold=75.0):
    """Send a <75% attendance warning email to one student.

    Returns (sent: bool, message: str). Never raises.
    """
    if not to_email:
        return False, "No email address on file for this student."
    if not is_configured():
        return False, NOT_CONFIGURED_MSG

    sender = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]

    status = "Barred from Final Exam" if avg_percentage < 70.0 else "Borderline Warning"
    msg = EmailMessage()
    msg["Subject"] = f"Attendance Warning — {student_name} ({avg_percentage}% < {threshold}%)"
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(
        f"Dear {student_name},\n\n"
        f"This is an automated warning from Attenova.\n\n"
        f"Your current aggregate attendance in {course_name} is {avg_percentage}%.\n"
        f"Your university requires at least {threshold}% attendance to sit "
        f"for the final examination.\n\n"
        f"Status: {status}\n\n"
        "Please attend all remaining classes and contact your course teacher "
        "if you have a genuine reason for absence.\n\n"
        "— Attenova\n"
    )

    sent, error = _send(msg)
    if sent:
        return True, f"Warning email sent to {to_email}."
    return False, error


def send_test_email():
    """Send a short test email to the teacher's own inbox (SMTP_USER).

    Lets the teacher check the email setup without warning a real
    student. Returns (sent: bool, message: str). Never raises.
    """
    if not is_configured():
        return False, NOT_CONFIGURED_MSG

    user = os.environ["SMTP_USER"]
    sender = os.environ.get("SMTP_FROM") or user
    msg = EmailMessage()
    msg["Subject"] = "Attenova test email - email alerts are working"
    msg["From"] = sender
    msg["To"] = user
    msg.set_content(
        "This is a test email from Attenova.\n\n"
        "Email alerts are working. Defaulter warnings will come from "
        "this address.\n\n"
        "No action needed.\n\n"
        "— Attenova\n"
    )

    sent, error = _send(msg)
    if sent:
        return True, f"Test email sent to {user}."
    return False, error


def send_verification_code(to_email, code):
    """Send a 6-digit registration verification code (v8.3).

    The code itself is only ever in this email — it is never logged
    or stored. Returns (sent: bool, message: str). Never raises.
    """
    if not is_configured():
        return False, NOT_CONFIGURED_MSG

    sender = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
    msg = EmailMessage()
    msg["Subject"] = "Your Attenova verification code"
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(
        f"Your Attenova verification code is: {code}\n\n"
        "It expires in 2 minutes. If you did not ask for this, "
        "ignore this email.\n\n"
        "— Attenova\n"
    )

    sent, error = _send(msg)
    if sent:
        return True, f"Verification code sent to {to_email}."
    return False, error


def build_transcript_data(student, threshold=75.0):
    """Build a student's complete attendance transcript, all courses.

    Returns a dict with one row per course (course name, the dates
    classes ran, total classes held, presents, absents, percentage),
    overall totals, and the courses the student is dropped from
    (per-course % below ``threshold``). A record counts as present
    unless its status is 'Absent' — Present, Late and Left Early all
    mean the student was there. Percentages are presents / classes
    held, rounded to 1 decimal. Never raises on missing data: a
    student with no records gets an empty course list and zero
    totals.
    """
    from app.models import AttendanceRecord, AttendanceSession

    records = (AttendanceRecord.query
               .filter_by(student_id=student.id)
               .join(AttendanceSession,
                     AttendanceRecord.session_id == AttendanceSession.id)
               .order_by(AttendanceSession.session_date).all())
    by_course = {}
    for rec in records:
        course = (rec.session.course_name if rec.session
                  else 'Unknown course')
        entry = by_course.setdefault(course, {'dates': [], 'presents': 0,
                                              'absents': 0})
        if rec.session and rec.session.session_date:
            entry['dates'].append(rec.session.session_date)
        if rec.status == 'Absent':
            entry['absents'] += 1
        else:
            entry['presents'] += 1
    courses = []
    for name in sorted(by_course):
        entry = by_course[name]
        total = entry['presents'] + entry['absents']
        pct = round(entry['presents'] / total * 100, 1) if total else 0.0
        courses.append({'course_name': name,
                        'dates': sorted(set(entry['dates'])),
                        'total': total, 'presents': entry['presents'],
                        'absents': entry['absents'], 'percentage': pct})
    total_presents = sum(c['presents'] for c in courses)
    total_absents = sum(c['absents'] for c in courses)
    total_classes = total_presents + total_absents
    overall = (round(total_presents / total_classes * 100, 1)
               if total_classes else 0.0)
    dropped = [c['course_name'] for c in courses
               if c['percentage'] < threshold]
    return {'courses': courses, 'total_classes': total_classes,
            'total_presents': total_presents,
            'total_absents': total_absents,
            'overall_percentage': overall,
            'dropped_courses': dropped}


def send_attendance_transcript(to_email, student_name, teacher_name,
                               transcript, threshold=75.0):
    """Send a student's complete attendance transcript by email (v9.1).

    ``transcript`` is the dict from build_transcript_data(). The
    email has a plain-text body and a simple HTML table with the
    same content: per course the classes held, presents, absents,
    percentage and class dates; overall totals; and the courses the
    student is dropped from (below ``threshold``%). Returns
    (sent: bool, message: str). Never raises.
    """
    if not to_email:
        return False, "No email address on file for this student."
    if not is_configured():
        return False, NOT_CONFIGURED_MSG

    sender = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
    msg = EmailMessage()
    msg["Subject"] = f"Attendance Transcript — {student_name} (Attenova)"
    msg["From"] = sender
    msg["To"] = to_email

    def fmt_dates(dates):
        return ', '.join(d.strftime('%d %b %Y') for d in dates) or '—'

    lines = [f"Dear {student_name},", "",
             "Here is your complete attendance transcript from Attenova, "
             f"sent by {teacher_name}.", ""]
    if transcript['courses']:
        for c in transcript['courses']:
            lines.append(
                f"{c['course_name']}: {c['total']} classes held, "
                f"{c['presents']} present, {c['absents']} absent — "
                f"{c['percentage']}%")
            lines.append(f"  Class dates: {fmt_dates(c['dates'])}")
        lines.append("")
    else:
        lines.append("No attendance records yet — no class has run for "
                     "you.")
        lines.append("")
    lines.append(
        f"Overall: {transcript['total_classes']} classes, "
        f"{transcript['total_presents']} present, "
        f"{transcript['total_absents']} absent — "
        f"{transcript['overall_percentage']}%")
    lines.append("")
    if transcript['dropped_courses']:
        lines.append(
            f"Courses you are dropped from (below {threshold}% "
            "attendance):")
        for name in transcript['dropped_courses']:
            lines.append(f"  - {name}")
    else:
        lines.append(
            f"You are not dropped from any course (all at or above "
            f"{threshold}%).")
    lines.append("")
    lines.append("Please attend all remaining classes and contact your "
                 "course teacher if you have a genuine reason for absence.")
    lines.append("")
    lines.append("— Attenova")
    msg.set_content("\n".join(lines))

    course_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}%</td>"
        "<td>{}</td></tr>".format(
            html.escape(c['course_name']), c['total'], c['presents'],
            c['absents'], c['percentage'], html.escape(fmt_dates(c['dates'])))
        for c in transcript['courses'])
    if transcript['dropped_courses']:
        dropped_html = ("<p><strong>Courses you are dropped from (below "
                        f"{threshold}% attendance):</strong></p><ul>" +
                        "".join(f"<li>{html.escape(n)}</li>"
                                for n in transcript['dropped_courses']) +
                        "</ul>")
    else:
        dropped_html = (f"<p>You are not dropped from any course (all at "
                        f"or above {threshold}%).</p>")
    html_body = (
        f"<p>Dear {html.escape(student_name)},</p>"
        "<p>Here is your complete attendance transcript from Attenova, "
        f"sent by {html.escape(teacher_name)}.</p>"
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" "
        "style=\"border-collapse: collapse;\">"
        "<tr><th>Course</th><th>Classes held</th><th>Present</th>"
        "<th>Absent</th><th>Attendance</th><th>Class dates</th></tr>"
        f"{course_rows}</table>"
        f"<p><strong>Overall:</strong> {transcript['total_classes']} "
        f"classes, {transcript['total_presents']} present, "
        f"{transcript['total_absents']} absent — "
        f"{transcript['overall_percentage']}%</p>"
        f"{dropped_html}"
        "<p>Please attend all remaining classes and contact your course "
        "teacher if you have a genuine reason for absence.</p>"
        "<p>— Attenova</p>")
    msg.add_alternative(html_body, subtype='html')

    sent, error = _send(msg)
    if sent:
        return True, f"Transcript sent to {to_email}."
    return False, error
