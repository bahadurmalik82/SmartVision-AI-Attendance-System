"""Email service (Phase 7).

Sends defaulter warning emails over SMTP. Configuration comes from
environment variables (see .env.example):

    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM (defaults to SMTP_USER), SMTP_USE_TLS (default true)

If SMTP is not configured, sending fails *gracefully*: it returns
(False, "Email is not configured...") and logs a warning instead of
raising — the app must never 500 just because email is not set up.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage


def is_configured():
    """True only when a host and credentials are present."""
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
                and os.environ.get("SMTP_PASSWORD"))


def send_defaulter_alert(to_email, student_name, avg_percentage,
                         course_name="your course", threshold=75.0):
    """Send a <75% attendance warning email to one student.

    Returns (sent: bool, message: str). Never raises.
    """
    if not to_email:
        return False, "No email address on file for this student."
    if not is_configured():
        return False, (
            "Email is not configured (set SMTP_HOST / SMTP_USER / "
            "SMTP_PASSWORD in .env). No email was sent."
        )

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.environ.get("SMTP_FROM") or user
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")

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
        return True, f"Warning email sent to {to_email}."
    except Exception as exc:
        return False, f"Could not send email to {to_email}: {exc}"
