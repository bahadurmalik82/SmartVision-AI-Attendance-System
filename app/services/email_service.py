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
