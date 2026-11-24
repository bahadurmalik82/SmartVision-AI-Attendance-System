"""Phase 14 tests (v8.2): email setup, complete.

v8.1 problems fixed here:
  1. The only way to send email was Defaulters -> per-student
     warning. There was no way to test email without warning a
     real student. Settings now has a "Send me a test email" button
     that mails the teacher's own inbox.
  2. No page showed whether email was configured. The Settings
     page now shows the email status (or that it is not set up).
  3. send_defaulter_alert parsed SMTP_PORT with int(...) BEFORE
     the try block, so an empty or non-numeric port raised
     ValueError and broke the "never raises" promise. The port now
     falls back to 587.
  4. Failure messages dumped the raw SMTP error. Gmail's 535 is
     now explained in plain words first, raw error after a dash.
  5. A failed send on Defaulters flashed "info" (neutral toast)
     instead of "warning".
"""
import contextlib
import os
import re
import smtplib
import socket
import unittest
from unittest import mock

from app import create_app, db
from app.models import Teacher
from app.routes.auth import reset_login_attempts
from app.routes.student import reset_student_login_attempts
from app.services.email_service import (email_status, send_defaulter_alert,
                                        send_test_email)
from app.services.scheduler_service import AutomatedSchedulerService

TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

SMTP_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
             "SMTP_FROM", "SMTP_USE_TLS"]
GOOD_ENV = {
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "SMTP_USER": "teacher@gmail.com",
    "SMTP_PASSWORD": "abcdefghijklmnop",
}


@contextlib.contextmanager
def _smtp_env(**overrides):
    """Set SMTP_* env vars for one test, restoring them after.

    All SMTP_* keys are cleared first, so a test that passes no
    overrides runs with email genuinely unconfigured.
    """
    saved = {k: os.environ.get(k) for k in SMTP_KEYS}
    for k in SMTP_KEYS:
        os.environ.pop(k, None)
    os.environ.update(overrides)
    try:
        yield
    finally:
        for k in SMTP_KEYS:
            os.environ.pop(k, None)
        for k, value in saved.items():
            if value is not None:
                os.environ[k] = value


class Phase14TestCase(unittest.TestCase):
    def setUp(self):
        reset_login_attempts()
        reset_student_login_attempts()
        AutomatedSchedulerService.shutdown()
        self.app = create_app('testing')
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.teacher = Teacher(name="Demo Teacher",
                               email="demo@iba-suk.edu.pk",
                               department="Computer Science",
                               is_active=True)
        self.teacher.set_password("Demo1234")
        db.session.add(self.teacher)
        db.session.commit()

    def tearDown(self):
        reset_login_attempts()
        reset_student_login_attempts()
        AutomatedSchedulerService.shutdown()
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    # -- helpers ---------------------------------------------------------
    def _csrf_token(self, url='/auth/login'):
        resp = self.client.get(url)
        match = TOKEN_RE.search(resp.get_data(as_text=True))
        self.assertIsNotNone(match, f'No CSRF token found on {url}')
        return match.group(1)

    def _login_teacher(self):
        token = self._csrf_token('/auth/login')
        return self.client.post('/auth/login', data={
            'email': 'demo@iba-suk.edu.pk', 'password': 'Demo1234',
            'csrf_token': token,
        }, follow_redirects=True)

    def _mock_smtp(self):
        """Patch smtplib.SMTP; return (class mock, server mock)."""
        patcher = mock.patch("app.services.email_service.smtplib.SMTP")
        mock_smtp_cls = patcher.start()
        self.addCleanup(patcher.stop)
        server = mock_smtp_cls.return_value.__enter__.return_value
        return mock_smtp_cls, server

    # -- (1) not configured: plain message, no SMTP call -------------------
    def test_not_configured_returns_message_and_never_calls_smtp(self):
        mock_smtp_cls, _server = self._mock_smtp()
        with _smtp_env():
            sent, message = send_defaulter_alert(
                "student@example.com", "Ali", 60.0)
        self.assertFalse(sent)
        self.assertIn("Email is not configured", message)
        self.assertIn("SMTP_HOST", message)
        mock_smtp_cls.assert_not_called()

    # -- (2) success path: starttls + login + send_message -----------------
    def test_success_path_calls_starttls_login_send(self):
        mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            sent, message = send_defaulter_alert(
                "student@example.com", "Ali", 60.0,
                course_name="Mathematics", threshold=75.0)
        self.assertTrue(sent)
        self.assertEqual(message, "Warning email sent to student@example.com.")
        mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587,
                                              timeout=15)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("teacher@gmail.com",
                                            "abcdefghijklmnop")
        server.send_message.assert_called_once()
        sent_msg = server.send_message.call_args[0][0]
        self.assertEqual(sent_msg["To"], "student@example.com")
        self.assertIn("Attendance Warning", sent_msg["Subject"])
        self.assertIn("Ali", sent_msg["Subject"])
        self.assertIn("Barred from Final Exam",
                      sent_msg.get_content())

    # -- (3) Gmail 535: friendly App Password message ----------------------
    def test_auth_error_explains_app_password(self):
        _mock_smtp_cls, server = self._mock_smtp()
        server.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"5.7.8 Username and Password not accepted")
        with _smtp_env(**GOOD_ENV):
            sent, message = send_defaulter_alert(
                "student@example.com", "Ali", 60.0)
        self.assertFalse(sent)
        self.assertTrue(
            message.startswith(
                "Gmail did not accept the sign-in. Check the App Password "
                "(16 letters, no spaces) and that 2-Step Verification is "
                "on."),
            message)
        # The raw error is kept after a dash for diagnosis.
        self.assertIn("535", message)

    # -- (4) empty / garbage port falls back to 587, never raises -----------
    def test_bad_port_falls_back_to_587(self):
        for bad_port in ("", "abc", "  "):
            mock_smtp_cls, _server = self._mock_smtp()
            env = dict(GOOD_ENV, SMTP_PORT=bad_port)
            with _smtp_env(**env):
                sent, message = send_defaulter_alert(
                    "student@example.com", "Ali", 60.0)
            self.assertTrue(sent, f"port={bad_port!r}: {message}")
            mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587,
                                                  timeout=15)

    def test_email_status_port_falls_back_to_587(self):
        with _smtp_env(**dict(GOOD_ENV, SMTP_PORT="abc")):
            status = email_status()
        self.assertEqual(status["port"], 587)
        self.assertTrue(status["configured"])
        self.assertEqual(status["host"], "smtp.gmail.com")
        self.assertEqual(status["user"], "teacher@gmail.com")
        # The password is never in the status dict.
        self.assertNotIn("password", str(status).lower())

    # -- (5) connection error: friendly could-not-reach message -------------
    def test_connection_error_is_friendly(self):
        mock_smtp_cls, _server = self._mock_smtp()
        mock_smtp_cls.side_effect = socket.gaierror(
            11001, "Name or service not known")
        with _smtp_env(**GOOD_ENV):
            sent, message = send_defaulter_alert(
                "student@example.com", "Ali", 60.0)
        self.assertFalse(sent)
        self.assertTrue(
            message.startswith(
                "Could not reach the mail server. Check SMTP_HOST and your "
                "internet connection."),
            message)

    # -- (6) test email goes to the teacher's own inbox ----------------------
    def test_send_test_email_sends_to_smtp_user(self):
        mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            sent, message = send_test_email()
        self.assertTrue(sent)
        self.assertEqual(message, "Test email sent to teacher@gmail.com.")
        mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587,
                                              timeout=15)
        server.send_message.assert_called_once()
        sent_msg = server.send_message.call_args[0][0]
        self.assertEqual(sent_msg["To"], "teacher@gmail.com")
        self.assertEqual(
            sent_msg["Subject"],
            "Attenova test email - email alerts are working")
        self.assertIn("test email from Attenova", sent_msg.get_content())
        self.assertIn("Defaulter warnings will come from this address",
                      sent_msg.get_content())
        self.assertIn("No action needed", sent_msg.get_content())

    def test_send_test_email_not_configured(self):
        mock_smtp_cls, _server = self._mock_smtp()
        with _smtp_env():
            sent, message = send_test_email()
        self.assertFalse(sent)
        self.assertIn("Email is not configured", message)
        mock_smtp_cls.assert_not_called()

    # -- (7) POST /settings/test-email, not configured -----------------------
    def test_settings_test_email_not_configured(self):
        self._login_teacher()
        mock_smtp_cls, _server = self._mock_smtp()
        token = self._csrf_token('/settings/')
        with _smtp_env():
            resp = self.client.post('/settings/test-email', data={
                'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Email is not configured", html)
        self.assertIn("iba-toast--warning", html)
        mock_smtp_cls.assert_not_called()

    # -- (8) POST /settings/test-email, configured ---------------------------
    def test_settings_test_email_success(self):
        self._login_teacher()
        _mock_smtp_cls, server = self._mock_smtp()
        token = self._csrf_token('/settings/')
        with _smtp_env(**GOOD_ENV):
            resp = self.client.post('/settings/test-email', data={
                'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Test email sent to teacher@gmail.com.", html)
        self.assertIn("iba-toast--success", html)
        server.send_message.assert_called_once()

    # -- (9) Settings page shows the Email alerts card + status line ---------
    def test_settings_page_shows_email_card_unconfigured(self):
        self._login_teacher()
        with _smtp_env():
            resp = self.client.get('/settings/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Email alerts", html)
        self.assertIn("Send me a test email", html)
        self.assertIn("/settings/test-email", html)
        self.assertIn("Email is not set up yet", html)

    def test_settings_page_shows_email_status_configured(self):
        self._login_teacher()
        with _smtp_env(**GOOD_ENV):
            resp = self.client.get('/settings/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn(
            "Email is set up — warnings go out from teacher@gmail.com "
            "via smtp.gmail.com:587.", html)

    # -- logged-out: settings needs a login ---------------------------------
    def test_settings_page_redirects_when_logged_out(self):
        resp = self.client.get('/settings/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/login', resp.headers.get('Location', ''))


if __name__ == '__main__':
    unittest.main()
