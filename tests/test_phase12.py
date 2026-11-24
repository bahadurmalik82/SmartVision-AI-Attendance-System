"""Phase 12 tests (v8): Analytics never silently drops students.

The v7 bug: with a course or date filter active, get_student_aggregates()
omitted every student with no record in that slice, so the Analytics
page showed "26 students" for a 31-student class and nobody could tell
it was a filtered count. v8 keeps the filtered rows but also passes the
full roster count and the hidden students (with a reason each), and the
page says so in one banner under the filter bar.
"""
import re
import unittest
from datetime import datetime, timedelta

from flask_login import login_user

from app import create_app, db
from app.models import (AttendanceRecord, AttendanceSession, Student,
                        Teacher, get_or_create_class_group)
from app.routes.analytics import _analytics_context
from app.routes.auth import reset_login_attempts
from app.routes.student import reset_student_login_attempts
from app.services.scheduler_service import AutomatedSchedulerService

TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


class Phase12TestCase(unittest.TestCase):
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

    def _make_class(self, section):
        group = get_or_create_class_group(
            self.teacher.id, 'BS Computer Science - AI', '5th', section,
            course_name='Mathematics')
        db.session.commit()
        return group

    def _add_student(self, roll, name, group):
        student = Student(roll_number=roll, name=name,
                          department=group.department,
                          semester=group.semester,
                          section=group.section,
                          teacher_id=self.teacher.id,
                          class_group_id=group.id,
                          cms_id=f'CMS-{Student.query.count() + 1:04d}')
        student.set_portal_password('Student1')
        db.session.add(student)
        db.session.commit()
        return student

    def _make_roster(self):
        """31 students (20 in Section B, 11 in Section C) + a
        Mathematics session with records for only the first 2."""
        group_b = self._make_class('Section B')
        group_c = self._make_class('Section C')
        students = []
        for i in range(1, 32):
            group = group_b if i <= 20 else group_c
            students.append(self._add_student(
                f'023-24-{i:04d}', f'Student {i:02d}', group))
        today = datetime.now().date()
        session = AttendanceSession(
            course_name='Mathematics', section='Section B',
            session_date=today,
            start_time=datetime.now() - timedelta(hours=2),
            end_time=datetime.now() - timedelta(minutes=20),
            is_active=False, total_duration_minutes=100,
            teacher_id=self.teacher.id)
        db.session.add(session)
        db.session.commit()
        for student in students[:2]:
            record = AttendanceRecord(
                session_id=session.id, student_id=student.id,
                minutes_present=100.0, first_seen=session.start_time)
            record.calculate_status(100)
            db.session.add(record)
        db.session.commit()
        return students, session

    def _context(self, query_string):
        """_analytics_context() for one filter, with the teacher logged in."""
        with self.app.test_request_context(f'/analytics/{query_string}'):
            login_user(self.teacher)
            return _analytics_context()

    # -- (a) no filter: the whole roster shows ------------------------------
    def test_no_filter_shows_all_31_students(self):
        self._make_roster()
        self._login_teacher()
        resp = self.client.get('/analytics/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('Student attendance records (31 of 31 shown)', html)
        self.assertIn('How the class splits — 31 of 31 student(s)', html)
        # No banner: nobody is hidden when nothing is filtered.
        self.assertNotIn('hidden by the current filter', html)

    def test_no_filter_hidden_list_is_empty(self):
        self._make_roster()
        ctx = self._context('/')
        self.assertEqual(ctx['total_students'], 31)
        self.assertEqual(len(ctx['rows']), 31)
        self.assertEqual(ctx['hidden_students'], [])

    # -- (b) course filter: only 2 have records ------------------------------
    def test_course_filter_shows_2_of_31_and_names_the_hidden_29(self):
        self._make_roster()
        self._login_teacher()
        resp = self.client.get('/analytics/?course=Mathematics')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('Student attendance records (2 of 31 shown)', html)
        self.assertIn('Showing 2 of 31 students', html)
        self.assertIn('29 hidden by the current filter', html)
        # A hidden student is named in the banner, not silently dropped.
        self.assertIn('Student 03', html)
        self.assertIn('They have no attendance record for this course/date yet.', html)
        self.assertIn('Clear filters', html)

    def test_course_filter_hidden_reasons(self):
        self._make_roster()
        ctx = self._context('/?course=Mathematics')
        self.assertEqual(len(ctx['rows']), 2)
        self.assertEqual(len(ctx['hidden_students']), 29)
        reasons = {h['reason'] for h in ctx['hidden_students']}
        self.assertEqual(reasons, {'no record for this course/date'})
        hidden_rolls = {h['student'].roll_number
                        for h in ctx['hidden_students']}
        self.assertNotIn('023-24-0001', hidden_rolls)
        self.assertIn('023-24-0003', hidden_rolls)

    # -- (c) date filter: same idea -------------------------------------------
    def test_date_filter_shows_2_of_31(self):
        students, session = self._make_roster()
        self._login_teacher()
        today = session.session_date.isoformat()
        resp = self.client.get(f'/analytics/?date={today}')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('Student attendance records (2 of 31 shown)', html)
        self.assertIn('Showing 2 of 31 students', html)
        self.assertIn('29 hidden by the current filter', html)

    def test_date_with_no_records_shows_0_of_31(self):
        self._make_roster()
        ctx = self._context('/?date=2030-01-07')
        self.assertEqual(len(ctx['rows']), 0)
        self.assertEqual(ctx['total_students'], 31)
        self.assertEqual(len(ctx['hidden_students']), 31)

    # -- (d) section filter -----------------------------------------------------
    def test_section_filter_shows_20_of_31_with_section_reason(self):
        self._make_roster()
        self._login_teacher()
        resp = self.client.get('/analytics/?section=Section+C')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('Student attendance records (11 of 31 shown)', html)
        self.assertIn('Showing 11 of 31 students', html)
        self.assertIn('20 hidden by the current filter', html)
        self.assertIn('They are outside the current section/student filter.', html)

    def test_section_filter_hidden_reasons(self):
        self._make_roster()
        ctx = self._context('/?section=Section+C')
        self.assertEqual(len(ctx['rows']), 11)
        self.assertEqual(len(ctx['hidden_students']), 20)
        reasons = {h['reason'] for h in ctx['hidden_students']}
        self.assertEqual(reasons, {'hidden by the section filter'})

    # -- sections dropdown comes from the roster, not just schedules ----------
    def test_sections_dropdown_lists_student_sections(self):
        self._make_roster()
        self._login_teacher()
        resp = self.client.get('/analytics/')
        html = resp.get_data(as_text=True)
        self.assertIn('<option value="Section B"', html)
        self.assertIn('<option value="Section C"', html)

    # -- K-Means empty state is one message, not two -----------------------------
    def test_kmeans_empty_state_is_a_single_message(self):
        self._login_teacher()
        resp = self.client.get('/analytics/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # The method note ("No student attendance data yet ...") used to
        # print right above a second, near-identical empty message.
        # Now only one empty-state message remains.
        self.assertNotIn('No student attendance data yet', html)
        self.assertEqual(html.count('No clustering data yet'), 1)


if __name__ == '__main__':
    unittest.main()
