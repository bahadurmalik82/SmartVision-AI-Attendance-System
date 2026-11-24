"""Phase 15 tests (v8.3): email OTP teacher registration + automatic
attendance-drop alerts.

Part 1 — OTP registration:
  A teacher's account is created ONLY after they enter the 6-digit
  code emailed to them. The code expires in 2 minutes, can be
  re-sent after 30 seconds (max 5 resends), and 5 wrong tries
  invalidate it. The plain code is never stored or logged — only
  its HMAC-SHA256 lives in PendingRegistration. Tests patch
  app.routes.auth._generate_otp_code to a fixed "123456" so the
  right code is known without ever reading a plain code from
  anywhere (there is none).

Part 2 — automatic drop alerts:
  When a session closes or a record is corrected, a student who
  drops into a worse band (warning / barred) gets the same warning
  email the Defaulters button sends by hand — once per band, never
  twice in a row. Recovering to 'eligible' re-arms the alert.
  Students with no attendance records yet are skipped (their class
  simply has not run), and students with no email on file are
  skipped silently.
"""
import contextlib
import os
import re
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from app import create_app, db
from app.models import (AttendanceRecord, AttendanceSession, ClassGroup,
                        PendingRegistration, Student, StudentAlertState,
                        Teacher, get_or_create_class_group)
from app.routes.auth import reset_login_attempts
from app.routes.student import reset_student_login_attempts
from app.services.attendance_alerts import check_and_send_drop_alerts
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


class Phase15TestCase(unittest.TestCase):
    def setUp(self):
        reset_login_attempts()
        reset_student_login_attempts()
        AutomatedSchedulerService.shutdown()
        self.app = create_app('testing')
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        # The duration tracker is a process-wide singleton: clear any
        # detections left by an earlier test.
        AutomatedSchedulerService.get_duration_tracker().start_session()

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

    def _patch_otp_code(self, code="123456"):
        """Fix the generated OTP code for one test."""
        patcher = mock.patch("app.routes.auth._generate_otp_code",
                             return_value=code)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _register(self, email="new@teacher.com", name="New Teacher",
                  password="Newpass123"):
        token = self._csrf_token('/auth/register')
        return self.client.post('/auth/register', data={
            'name': name, 'email': email, 'department': 'Computer Science',
            'password': password, 'confirm_password': password,
            'csrf_token': token,
        }, follow_redirects=True)

    def _pending(self, email="new@teacher.com"):
        return PendingRegistration.query.filter_by(email=email).first()

    def _make_student(self, email="ali@example.com", roll="023-24-0001"):
        group = get_or_create_class_group(
            self.teacher.id, 'BS Computer Science - AI', '5th',
            'Section B', course_name='Mathematics')
        db.session.commit()
        student = Student(roll_number=roll, name="Ali",
                          department=group.department,
                          semester=group.semester, section=group.section,
                          email=email, teacher_id=self.teacher.id,
                          class_group_id=group.id, cms_id="CMS-0001")
        student.set_portal_password('Student1')
        db.session.add(student)
        db.session.commit()
        return group, student

    def _history_session(self, total_minutes=100):
        session = AttendanceSession(
            teacher_id=self.teacher.id, course_name='Mathematics',
            section='Section B', session_date=date.today(),
            start_time=datetime.now() - timedelta(minutes=total_minutes),
            end_time=datetime.now(), is_active=False,
            total_duration_minutes=total_minutes)
        db.session.add(session)
        db.session.commit()
        return session

    def _add_record(self, session, student, pct):
        rec = AttendanceRecord(
            session_id=session.id, student_id=student.id,
            minutes_present=float(pct), attendance_percentage=float(pct),
            status='Present' if pct >= 75 else 'Absent')
        db.session.add(rec)
        db.session.commit()
        return rec

    def _finalize_with_blocks(self, group, blocks, total_minutes=100):
        """Close a class session, crediting `blocks` 10-minute blocks
        to every student in the class via the duration tracker.

        blocks=7 on a 100-minute class -> 70.0% for a detected
        student; blocks=0 -> 0.0% (never detected)."""
        start = datetime.now() - timedelta(minutes=total_minutes)
        session = AttendanceSession(
            teacher_id=self.teacher.id, class_group_id=group.id,
            course_name='Mathematics', section='Section B',
            session_date=date.today(), start_time=start,
            is_active=True, total_duration_minutes=total_minutes)
        db.session.add(session)
        db.session.commit()
        tracker = AutomatedSchedulerService.get_duration_tracker()
        tracker.start_session(start)
        students = Student.query.filter_by(class_group_id=group.id).all()
        for student in students:
            for b in range(blocks):
                tracker.record_detection(
                    student.name, student.name,
                    timestamp=start + timedelta(minutes=b * 10))
        AutomatedSchedulerService.finalize_session(session)
        return session

    # -- Part 1: OTP registration -----------------------------------------
    # (1) register sends one code email, creates NO teacher yet --------
    def test_register_sends_code_and_creates_no_teacher(self):
        self._patch_otp_code()
        _mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            resp = self._register()
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Check your email", html)
        self.assertIn("iba-otp-box", html)
        self.assertIn("Code expires in", html)
        self.assertIn("Resend code", html)
        self.assertIn("new@teacher.com", html)

        pending = self._pending()
        self.assertIsNotNone(pending, "no pending registration row")
        self.assertEqual(pending.name, "New Teacher")
        self.assertEqual(pending.attempts, 0)
        self.assertEqual(pending.resend_count, 0)
        # The plain code is never stored — only its hash.
        self.assertNotEqual(pending.otp_hash, "123456")
        self.assertEqual(len(pending.otp_hash), 64)
        self.assertNotEqual(pending.password_hash, "Newpass123")

        server.send_message.assert_called_once()
        sent_msg = server.send_message.call_args[0][0]
        self.assertEqual(sent_msg["To"], "new@teacher.com")
        self.assertEqual(sent_msg["Subject"],
                         "Your Attenova verification code")
        self.assertIn("123456", sent_msg.get_content())
        self.assertIn("expires in 2 minutes", sent_msg.get_content())

        self.assertIsNone(
            Teacher.query.filter_by(email="new@teacher.com").first(),
            "teacher must NOT exist before the code is verified")

    # (2) wrong code: in-popup error, attempts +1, still no teacher -----
    def test_wrong_code_shows_popup_error_and_counts_attempt(self):
        self._patch_otp_code()
        _mock_smtp_cls, _server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            self._register()
            token = self._csrf_token('/auth/register')
            resp = self.client.post('/auth/verify-otp', data={
                'email': 'new@teacher.com', 'code': '000001',
                'csrf_token': token,
            }, follow_redirects=True)
        html = resp.get_data(as_text=True)
        self.assertIn("Wrong code. Try again.", html)
        self.assertIn("Check your email", html)
        self.assertIn("iba-otp-box", html)
        self.assertEqual(self._pending().attempts, 1)
        self.assertIsNone(
            Teacher.query.filter_by(email="new@teacher.com").first())

    # (3) right code: teacher created, pending row gone, logged in -------
    def test_right_code_creates_teacher_and_logs_in(self):
        self._patch_otp_code()
        _mock_smtp_cls, _server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            self._register()
            token = self._csrf_token('/auth/register')
            resp = self.client.post('/auth/verify-otp', data={
                'email': 'new@teacher.com', 'code': '123456',
                'csrf_token': token,
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Account created successfully", html)
        self.assertIn("New Teacher", html)
        teacher = Teacher.query.filter_by(email="new@teacher.com").first()
        self.assertIsNotNone(teacher)
        self.assertTrue(teacher.check_password("Newpass123"))
        self.assertIsNone(self._pending())
        # Logged in: the dashboard loads.
        resp = self.client.get('/dashboard')
        self.assertEqual(resp.status_code, 200)

    # (4) expired code is rejected and the row is deleted -----------------
    def test_expired_code_rejected_and_row_deleted(self):
        self._patch_otp_code()
        _mock_smtp_cls, _server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            self._register()
        pending = self._pending()
        pending.expires_at = datetime.now() - timedelta(minutes=1)
        db.session.commit()
        token = self._csrf_token('/auth/register')
        with _smtp_env(**GOOD_ENV):
            resp = self.client.post('/auth/verify-otp', data={
                'email': 'new@teacher.com', 'code': '123456',
                'csrf_token': token,
            }, follow_redirects=True)
        html = resp.get_data(as_text=True)
        self.assertIn("That code expired", html)
        self.assertIsNone(self._pending())
        self.assertIsNone(
            Teacher.query.filter_by(email="new@teacher.com").first())

    # (5) resend: refused inside 30s, allowed after the cooldown ---------
    def test_resend_cooldown_refused_then_allowed(self):
        self._patch_otp_code()
        _mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            self._register()
            token = self._csrf_token('/auth/register')
            resp = self.client.post('/auth/resend-otp', data={
                'email': 'new@teacher.com', 'csrf_token': token,
            }, follow_redirects=True)
        html = resp.get_data(as_text=True)
        self.assertIn("Wait", html)
        self.assertIn("seconds before asking for a new code", html)
        self.assertIn("Check your email", html)
        server.send_message.assert_called_once()  # no second email yet

        pending = self._pending()
        pending.last_sent_at = datetime.now() - timedelta(seconds=31)
        db.session.commit()
        with _smtp_env(**GOOD_ENV):
            token = self._csrf_token('/auth/register')
            resp = self.client.post('/auth/resend-otp', data={
                'email': 'new@teacher.com', 'csrf_token': token,
            }, follow_redirects=True)
        html = resp.get_data(as_text=True)
        self.assertIn("A new code was sent to new@teacher.com.", html)
        self.assertEqual(server.send_message.call_count, 2)
        pending = self._pending()
        self.assertEqual(pending.resend_count, 1)
        self.assertGreater(pending.expires_at,
                           datetime.now() + timedelta(seconds=60))

    # (6) 5 wrong attempts invalidate the row ------------------------------
    def test_five_wrong_attempts_invalidate_row(self):
        self._patch_otp_code()
        _mock_smtp_cls, _server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            self._register()
            for i in range(4):
                token = self._csrf_token('/auth/register')
                resp = self.client.post('/auth/verify-otp', data={
                    'email': 'new@teacher.com', 'code': '000001',
                    'csrf_token': token,
                }, follow_redirects=True)
                self.assertIn("Wrong code. Try again.",
                              resp.get_data(as_text=True))
            self.assertEqual(self._pending().attempts, 4)
            token = self._csrf_token('/auth/register')
            resp = self.client.post('/auth/verify-otp', data={
                'email': 'new@teacher.com', 'code': '000001',
                'csrf_token': token,
            }, follow_redirects=True)
        self.assertIn("Too many wrong tries",
                      resp.get_data(as_text=True))
        self.assertIsNone(self._pending())
        self.assertIsNone(
            Teacher.query.filter_by(email="new@teacher.com").first())

    # (7) register when email is not configured -----------------------------
    def test_register_when_email_not_configured(self):
        self._patch_otp_code()
        mock_smtp_cls, _server = self._mock_smtp()
        with _smtp_env():
            resp = self._register()
        html = resp.get_data(as_text=True)
        self.assertIn("Email is not set up yet", html)
        mock_smtp_cls.assert_not_called()
        self.assertIsNone(self._pending())
        self.assertIsNone(
            Teacher.query.filter_by(email="new@teacher.com").first())

    # (extra) 5 resends invalidate the row ----------------------------------
    def test_resend_limit_invalidates_row(self):
        self._patch_otp_code()
        _mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            self._register()
        pending = self._pending()
        pending.resend_count = 5
        pending.last_sent_at = datetime.now() - timedelta(seconds=31)
        db.session.commit()
        with _smtp_env(**GOOD_ENV):
            token = self._csrf_token('/auth/register')
            resp = self.client.post('/auth/resend-otp', data={
                'email': 'new@teacher.com', 'csrf_token': token,
            }, follow_redirects=True)
        self.assertIn("Too many resends", resp.get_data(as_text=True))
        self.assertIsNone(self._pending())
        server.send_message.assert_called_once()  # only the original

    # -- Part 2: automatic drop alerts --------------------------------------
    # (8) finalize drops eligible -> warning: one email; second finalize
    #     in the same band: no second email ---------------------------------
    def test_finalize_drop_sends_one_email_then_no_repeat(self):
        _mock_smtp_cls, server = self._mock_smtp()
        group, student = self._make_student()
        history = self._history_session()
        self._add_record(history, student, 100)
        self._add_record(history, student, 50)   # avg 75.0 = eligible
        with _smtp_env(**GOOD_ENV):
            self._finalize_with_blocks(group, 7)  # 70% -> avg 73.3 warning
        server.send_message.assert_called_once()
        sent_msg = server.send_message.call_args[0][0]
        self.assertEqual(sent_msg["To"], "ali@example.com")
        self.assertIn("Attendance Warning", sent_msg["Subject"])
        state = StudentAlertState.query.get(student.id)
        self.assertIsNotNone(state)
        self.assertEqual(state.last_kind, 'warning')
        with _smtp_env(**GOOD_ENV):
            self._finalize_with_blocks(group, 7)  # avg 72.5, still warning
        server.send_message.assert_called_once()  # no second email

    # (9) recover to eligible, then drop again -> a new email ---------------
    def test_recover_then_drop_alerts_again(self):
        _mock_smtp_cls, server = self._mock_smtp()
        group, student = self._make_student()
        history = self._history_session()
        self._add_record(history, student, 100)
        self._add_record(history, student, 50)   # avg 75.0 = eligible
        with _smtp_env(**GOOD_ENV):
            self._finalize_with_blocks(group, 7)  # avg 73.3 warning
        self.assertEqual(server.send_message.call_count, 1)
        # Recovery: two perfect records bring the average back up.
        self._add_record(history, student, 100)
        self._add_record(history, student, 100)  # avg 84.0 = eligible
        with _smtp_env(**GOOD_ENV):
            check_and_send_drop_alerts(self.teacher.id)
        self.assertEqual(server.send_message.call_count, 1)
        self.assertEqual(StudentAlertState.query.get(student.id).last_kind,
                         'eligible')
        # Drop again: a class the student never shows up for.
        with _smtp_env(**GOOD_ENV):
            self._finalize_with_blocks(group, 0)  # 0% -> avg 70.0 warning
        self.assertEqual(server.send_message.call_count, 2)

    # (10) student with no email on file gets no email -----------------------
    def test_student_without_email_gets_no_alert(self):
        _mock_smtp_cls, server = self._mock_smtp()
        group, student = self._make_student(email=None)
        history = self._history_session()
        self._add_record(history, student, 100)
        self._add_record(history, student, 50)   # avg 75.0 = eligible
        with _smtp_env(**GOOD_ENV):
            self._finalize_with_blocks(group, 7)  # avg 73.3 warning
        server.send_message.assert_not_called()

    # (11) a manual correction that drops a student triggers the alert -----
    def test_manual_correction_drop_triggers_alert(self):
        self._login_teacher()
        _mock_smtp_cls, server = self._mock_smtp()
        group, student = self._make_student()
        history = self._history_session()
        rec1 = self._add_record(history, student, 100)
        self._add_record(history, student, 100)
        self._add_record(history, student, 50)   # avg 83.3 = eligible
        token = self._csrf_token('/analytics/')
        with _smtp_env(**GOOD_ENV):
            resp = self.client.post(
                f'/analytics/correct/{rec1.id}', data={
                    'status': 'Absent', 'minutes_present': '0',
                    'reason': 'test correction',
                    'csrf_token': token,
                }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        server.send_message.assert_called_once()
        sent_msg = server.send_message.call_args[0][0]
        self.assertEqual(sent_msg["To"], "ali@example.com")
        self.assertIn("Attendance Warning", sent_msg["Subject"])
        self.assertEqual(StudentAlertState.query.get(student.id).last_kind,
                         'barred')

    # (extra) finalize with email unconfigured never raises ------------------
    def test_finalize_drop_with_email_unconfigured_never_raises(self):
        mock_smtp_cls, _server = self._mock_smtp()
        group, student = self._make_student()
        history = self._history_session()
        self._add_record(history, student, 100)
        self._add_record(history, student, 50)   # avg 75.0 = eligible
        with _smtp_env():
            self._finalize_with_blocks(group, 7)  # avg 73.3 warning
        mock_smtp_cls.assert_not_called()


if __name__ == '__main__':
    unittest.main()
