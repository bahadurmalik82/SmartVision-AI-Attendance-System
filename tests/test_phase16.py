"""Phase 16 tests (v9.1): student-list search, transcript emails with
select-and-send, and the Settings spacing fix.

Change 1 — search:
  Every student list (Analytics, class page, Defaulters, Non-defaulters)
  has a live search box that filters the shown rows as the teacher
  types (name, roll no, CMS ID, email, portal password) plus a clear
  (x) button and a "No students match your search." empty state.

Change 2 — transcript emails:
  The Defaulters page has a second section, Non-defaulters (eligible
  students, avg >= threshold). Both tables have a per-student "Send
  Transcript" button and a "Select Students" toggle: ticking boxes
  and pressing "Send Email" sends the transcript ONLY to the selected
  students — there is deliberately no send-to-all button. The email
  carries the complete transcript across all courses (per course:
  classes held, presents, absents, %, class dates), overall totals,
  and the courses the student is dropped from (below threshold).
  Students with no email on file are skipped and named in the toast;
  email unconfigured sends nothing.

Change 3 — Settings spacing:
  The "My recent activity" card sits inside the space-y-6 container,
  so it gets the same gap as every other card.
"""
import contextlib
import os
import re
import unittest
from datetime import date, datetime
from unittest import mock

from app import create_app, db
from app.models import (AttendanceRecord, AttendanceSession, AuditLog,
                        Student, Teacher, get_or_create_class_group)
from app.routes.auth import reset_login_attempts
from app.routes.student import reset_student_login_attempts
from app.services.email_service import build_transcript_data
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
    """Set SMTP_* env vars for one test, restoring them after."""
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


class Phase16TestCase(unittest.TestCase):
    def setUp(self):
        reset_login_attempts()
        reset_student_login_attempts()
        AutomatedSchedulerService.shutdown()
        self.app = create_app('testing')
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        AutomatedSchedulerService.get_duration_tracker().start_session()

        self.teacher = Teacher(name="Demo Teacher",
                               email="demo@iba-suk.edu.pk",
                               department="Computer Science",
                               is_active=True)
        self.teacher.set_password("Demo1234")
        db.session.add(self.teacher)
        db.session.commit()

        self.group = get_or_create_class_group(
            self.teacher.id, 'BS Computer Science - AI', '5th',
            'Section B', course_name='Mathematics')
        db.session.commit()
        self.ali = self._make_student(
            roll="023-24-0001", name="Ali", email="ali@example.com",
            cms_id="CMS-0001")
        self.sara = self._make_student(
            roll="023-24-0002", name="Sara", email="sara@example.com",
            cms_id="CMS-0002")
        self.noemail = self._make_student(
            roll="023-24-0003", name="NoEmail", email=None,
            cms_id="CMS-0003")

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
        token = self._csrf_token()
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

    def _make_student(self, roll, name, email, cms_id):
        student = Student(roll_number=roll, name=name,
                          department=self.group.department,
                          semester=self.group.semester,
                          section=self.group.section,
                          email=email, teacher_id=self.teacher.id,
                          class_group_id=self.group.id, cms_id=cms_id)
        student.set_portal_password('Student1')
        db.session.add(student)
        db.session.commit()
        return student

    def _session(self, course, day):
        session = AttendanceSession(
            teacher_id=self.teacher.id, course_name=course,
            section='Section B', session_date=date(2026, 11, day),
            start_time=datetime(2026, 11, day, 9, 0),
            end_time=datetime(2026, 11, day, 10, 40),
            is_active=False, total_duration_minutes=100)
        db.session.add(session)
        db.session.commit()
        return session

    def _record(self, session, student, pct):
        rec = AttendanceRecord(
            session_id=session.id, student_id=student.id,
            minutes_present=float(pct), attendance_percentage=float(pct),
            status='Present' if pct >= 75 else 'Absent')
        db.session.add(rec)
        db.session.commit()
        return rec

    def _seed_records(self):
        """Ali: Maths 100% (present), Physics 0% (absent) -> 50% avg,
        defaulter, dropped from Physics. Sara: 100% in both -> eligible.
        NoEmail: 100% in Maths -> eligible, but has no email on file."""
        maths = self._session('Mathematics', 20)
        physics = self._session('Physics', 21)
        self._record(maths, self.ali, 100)
        self._record(physics, self.ali, 0)
        self._record(maths, self.sara, 100)
        self._record(physics, self.sara, 100)
        self._record(maths, self.noemail, 100)

    def _sent_messages(self, server):
        """EmailMessage objects passed to the mocked SMTP server."""
        return [call.args[0]
                for call in server.send_message.call_args_list]

    # -- Change 1: search boxes ------------------------------------------
    def test_search_box_on_analytics(self):
        self._login_teacher()
        html = self.client.get('/analytics/').get_data(as_text=True)
        self.assertIn('data-iba-search="ibaAnalyticsTable"', html)
        self.assertIn('data-iba-search-clear="ibaAnalyticsTable"', html)
        self.assertIn('No students match your search.', html)
        self.assertIn('iba-student-lists.js', html)
        # The rows carry the searchable fields (roll, name, CMS ID,
        # email, portal password) even where the columns do not show them.
        self.assertIn('data-iba-search="023-24-0001 Ali CMS-0001 '
                      'ali@example.com Student1"', html)

    def test_search_box_on_class_detail(self):
        self._login_teacher()
        html = self.client.get(
            f'/students/class/{self.group.id}').get_data(as_text=True)
        self.assertIn('data-iba-search="ibaClassTable"', html)
        self.assertIn('data-iba-search-clear="ibaClassTable"', html)
        self.assertIn('No students match your search.', html)
        self.assertIn('iba-student-lists.js', html)
        # The server-side search form is still there (Search + Clear).
        self.assertIn('>Search</button>', html)

    def test_search_boxes_on_defaulters_both_tables(self):
        self._login_teacher()
        self._seed_records()
        html = self.client.get(
            '/analytics/defaulters').get_data(as_text=True)
        self.assertIn('data-iba-search="ibaDefaultersTable"', html)
        self.assertIn('data-iba-search="ibaNonDefaultersTable"', html)
        self.assertIn('No students match your search.', html)
        self.assertIn('iba-student-lists.js', html)
        # Non-defaulters section exists and holds the eligible student.
        self.assertIn('Non-defaulters', html)
        self.assertIn('Sara', html)
        self.assertIn('Students Eligible', html)
        # Select mode + per-student transcript, but no send-to-all.
        self.assertIn('Select Students', html)
        self.assertIn('Send Transcript', html)
        self.assertNotIn('Send Email to All', html)
        self.assertNotIn('send-all', html)
        self.assertNotIn('transcript-all', html)

    # -- Change 2: transcript content ------------------------------------
    def test_transcript_content(self):
        self._seed_records()
        data = build_transcript_data(self.ali, threshold=75.0)
        self.assertEqual(len(data['courses']), 2)
        maths = [c for c in data['courses']
                 if c['course_name'] == 'Mathematics'][0]
        physics = [c for c in data['courses']
                   if c['course_name'] == 'Physics'][0]
        self.assertEqual((maths['total'], maths['presents'],
                          maths['absents'], maths['percentage']),
                         (1, 1, 0, 100.0))
        self.assertEqual((physics['total'], physics['presents'],
                          physics['absents'], physics['percentage']),
                         (1, 0, 1, 0.0))
        self.assertEqual(str(maths['dates'][0]), '2026-11-20')
        self.assertEqual(str(physics['dates'][0]), '2026-11-21')
        self.assertEqual((data['total_classes'], data['total_presents'],
                          data['total_absents'],
                          data['overall_percentage']), (2, 1, 1, 50.0))
        self.assertEqual(data['dropped_courses'], ['Physics'])

        _mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            sent, message = send_transcript_for_test(self.ali, self.teacher)
        self.assertTrue(sent, message)
        self.assertEqual(len(self._sent_messages(server)), 1)
        msg = self._sent_messages(server)[0]
        plain = msg.get_body(preferencelist=('plain')).get_content()
        self.assertIn('Mathematics', plain)
        self.assertIn('100.0%', plain)
        self.assertIn('Physics', plain)
        self.assertIn('0.0%', plain)
        self.assertIn('20 Nov 2026', plain)
        self.assertIn('21 Nov 2026', plain)
        self.assertIn('Overall:', plain)
        self.assertIn('50.0%', plain)
        self.assertIn('dropped from', plain)
        self.assertIn('Demo Teacher', plain)
        html_body = msg.get_body(preferencelist=('html')).get_content()
        self.assertIn('<table', html_body)
        self.assertIn('Mathematics', html_body)

    def test_transcript_empty_for_student_with_no_records(self):
        data = build_transcript_data(self.ali, threshold=75.0)
        self.assertEqual(data['courses'], [])
        self.assertEqual(data['total_classes'], 0)
        self.assertEqual(data['overall_percentage'], 0.0)
        self.assertEqual(data['dropped_courses'], [])

    # -- Change 2: sending -----------------------------------------------
    def test_individual_transcript_send(self):
        self._login_teacher()
        self._seed_records()
        _mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            token = self._csrf_token('/analytics/defaulters')
            resp = self.client.post(
                f'/analytics/defaulters/transcript/{self.ali.id}',
                data={'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self._sent_messages(server)), 1)
        self.assertEqual(self._sent_messages(server)[0]['To'],
                         'ali@example.com')
        self.assertIn('Transcript sent to ali@example.com.',
                      resp.get_data(as_text=True))
        entry = AuditLog.query.filter_by(
            action='transcript_email').first()
        self.assertIsNotNone(entry, "individual send was not audit-logged")
        self.assertIn('023-24-0001', entry.detail)

    def test_selected_only_send(self):
        self._login_teacher()
        self._seed_records()
        _mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            token = self._csrf_token('/analytics/defaulters')
            resp = self.client.post(
                '/analytics/defaulters/transcript-selected',
                data={'csrf_token': token,
                      'student_ids': [str(self.ali.id),
                                      str(self.sara.id)]},
                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        sent = self._sent_messages(server)
        self.assertEqual(len(sent), 2, "must send to exactly the 2 selected")
        recipients = {m['To'] for m in sent}
        self.assertEqual(recipients, {'ali@example.com', 'sara@example.com'})
        html = resp.get_data(as_text=True)
        self.assertIn('Transcript sent to 2 student(s).', html)

    def test_selected_send_skips_no_email_student(self):
        self._login_teacher()
        self._seed_records()
        _mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            token = self._csrf_token('/analytics/defaulters')
            resp = self.client.post(
                '/analytics/defaulters/transcript-selected',
                data={'csrf_token': token,
                      'student_ids': [str(self.ali.id),
                                      str(self.noemail.id)]},
                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        sent = self._sent_messages(server)
        self.assertEqual(len(sent), 1, "the no-email student must be skipped")
        self.assertEqual(sent[0]['To'], 'ali@example.com')
        html = resp.get_data(as_text=True)
        self.assertIn('Transcript sent to 1 student(s).', html)
        self.assertIn('Skipped (no email on file)', html)
        self.assertIn('023-24-0003', html)

    def test_selected_send_with_nothing_selected(self):
        self._login_teacher()
        _mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env(**GOOD_ENV):
            token = self._csrf_token('/analytics/defaulters')
            resp = self.client.post(
                '/analytics/defaulters/transcript-selected',
                data={'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self._sent_messages(server)), 0)
        self.assertIn('No students selected',
                      resp.get_data(as_text=True))

    def test_transcript_unconfigured_sends_nothing(self):
        self._login_teacher()
        self._seed_records()
        _mock_smtp_cls, server = self._mock_smtp()
        with _smtp_env():  # no SMTP_* vars: email genuinely unconfigured
            token = self._csrf_token('/analytics/defaulters')
            resp = self.client.post(
                f'/analytics/defaulters/transcript/{self.ali.id}',
                data={'csrf_token': token}, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn('Email is not configured',
                          resp.get_data(as_text=True))
            resp = self.client.post(
                '/analytics/defaulters/transcript-selected',
                data={'csrf_token': token,
                      'student_ids': [str(self.ali.id)]},
                follow_redirects=True)
            self.assertIn('Email is not configured',
                          resp.get_data(as_text=True))
        self.assertEqual(len(self._sent_messages(server)), 0)

    # -- Change 3: Settings spacing --------------------------------------
    def test_settings_activity_card_inside_container(self):
        self._login_teacher()
        html = self.client.get('/settings/').get_data(as_text=True)
        start = html.find('space-y-6 max-w-2xl')
        activity = html.find('My recent activity')
        self.assertGreater(start, -1, "settings container not found")
        self.assertGreater(activity, -1, "activity card not found")
        # Between the container opening and the activity card, more
        # <div> tags opened than closed: the card is still INSIDE the
        # container (before the fix the container closed first, so the
        # counts were equal).
        before = html[start:activity]
        self.assertGreater(before.count('<div'), before.count('</div>'),
                           "activity card is outside the space-y-6 container")
        self.assertIn('v9.1', html)


def send_transcript_for_test(student, teacher):
    """Send one transcript the way the route does, for content tests."""
    from app.services.email_service import send_attendance_transcript
    data = build_transcript_data(student, threshold=75.0)
    return send_attendance_transcript(
        student.email, student.name, teacher.name, data, threshold=75.0)
