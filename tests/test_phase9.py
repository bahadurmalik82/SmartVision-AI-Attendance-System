"""Phase 9 tests (v3, updated for v4): CSRF, safe redirect, password
rules, open registration, login lockout, SSRF-safe camera ping,
security headers, 404 page, no-admin, settings, manual correction,
ownership, backup, stuck-session cleanup.

v4 changes: the student PIN portal is replaced by CMS ID + password
(see test_phase10.py), registration is open to every teacher, and
the admin system is deleted.
"""
import re
import unittest
from datetime import datetime, timedelta

from app import create_app, db
from app.models import (AttendanceRecord, AttendanceSession, Student,
                        Teacher)
from app.routes.auth import reset_login_attempts
from app.services.settings import get_threshold

TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


class Phase9TestCase(unittest.TestCase):
    def setUp(self):
        reset_login_attempts()
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
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    # -- helpers ---------------------------------------------------------
    def _csrf_token(self, url='/auth/login'):
        resp = self.client.get(url)
        match = TOKEN_RE.search(resp.get_data(as_text=True))
        self.assertIsNotNone(match, f'No CSRF token found on {url}')
        return match.group(1)

    def _login(self, email='demo@iba-suk.edu.pk', password='Demo1234'):
        token = self._csrf_token('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': password, 'csrf_token': token,
        }, follow_redirects=True)

    def _add_student(self, roll='K21-0145', name='Ahmed Ali',
                     password='StudPass1', teacher_id=None,
                     cms_id='CMS-0001'):
        student = Student(roll_number=roll, name=name, cms_id=cms_id,
                          teacher_id=teacher_id if teacher_id is not None
                          else self.teacher.id)
        if password:
            student.set_portal_password(password)
        db.session.add(student)
        db.session.commit()
        return student

    # -- CSRF ------------------------------------------------------------
    def test_post_without_csrf_token_is_rejected(self):
        self._login()
        resp = self.client.post('/students/delete/1')
        self.assertEqual(resp.status_code, 400)

    def test_post_with_csrf_token_is_accepted(self):
        self._login()
        student = self._add_student()
        token = self._csrf_token('/students/')
        resp = self.client.post(f'/students/delete/{student.id}',
                                data={'csrf_token': token},
                                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(Student.query.get(student.id))

    # -- Safe redirect ----------------------------------------------------
    def test_login_next_to_external_site_is_blocked(self):
        token = self._csrf_token('/auth/login')
        resp = self.client.post(
            '/auth/login?next=https://evil.example.com/steal',
            data={'email': 'demo@iba-suk.edu.pk', 'password': 'Demo1234',
                  'csrf_token': token})
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('evil.example.com', resp.headers['Location'])
        self.assertIn('/', resp.headers['Location'])

    def test_login_next_to_relative_path_is_allowed(self):
        token = self._csrf_token('/auth/login')
        resp = self.client.post(
            '/auth/login?next=/analytics/',
            data={'email': 'demo@iba-suk.edu.pk', 'password': 'Demo1234',
                  'csrf_token': token})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers['Location'], '/analytics/')

    # -- Password rules + open registration (v4) -------------------------
    def test_registration_is_open_for_a_second_teacher(self):
        token = self._csrf_token('/auth/register')
        resp = self.client.post('/auth/register', data={
            'name': 'Second Teacher', 'email': 'second@iba-suk.edu.pk',
            'department': 'Computer Science',
            'password': 'Second123', 'confirm_password': 'Second123',
            'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(
            Teacher.query.filter_by(email='second@iba-suk.edu.pk').first())
        self.assertIn(b'Faculty Portal', resp.data)

    def test_weak_password_is_rejected_on_first_registration(self):
        db.session.delete(self.teacher)
        db.session.commit()
        token = self._csrf_token('/auth/register')
        resp = self.client.post('/auth/register', data={
            'name': 'New Teacher', 'email': 'new@iba-suk.edu.pk',
            'department': 'Computer Science',
            'password': 'short', 'confirm_password': 'short',
            'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'at least 8 characters', resp.data)
        self.assertIsNone(
            Teacher.query.filter_by(email='new@iba-suk.edu.pk').first())

    # -- Login lockout ------------------------------------------------------
    def test_five_failed_logins_lock_the_account_out(self):
        for _ in range(5):
            token = self._csrf_token('/auth/login')
            self.client.post('/auth/login', data={
                'email': 'demo@iba-suk.edu.pk', 'password': 'WrongPass1',
                'csrf_token': token})
        token = self._csrf_token('/auth/login')
        resp = self.client.post('/auth/login', data={
            'email': 'demo@iba-suk.edu.pk', 'password': 'Demo1234',
            'csrf_token': token}, follow_redirects=True)
        self.assertIn(b'Too many failed login attempts', resp.data)

    # -- Old /portal/ URL redirects to the new student login (v4) --------
    def test_old_portal_url_redirects_to_student_login(self):
        resp = self.client.get('/portal/', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/student/login', resp.headers['Location'])

    # -- SSRF-safe camera ping ----------------------------------------------
    def test_test_ping_blocks_localhost_and_metadata(self):
        self._login()
        for bad_url in ['http://127.0.0.1:8080/video',
                        'http://169.254.169.254/latest/meta-data/',
                        'http://localhost:8080/video',
                        'http://192.168.1.15:9999/video']:
            token = self._csrf_token('/cameras/')
            resp = self.client.post(
                '/cameras/test-ping',
                json={'url': bad_url},
                headers={'X-CSRFToken': token})
            self.assertEqual(resp.status_code, 400, bad_url)
            self.assertFalse(resp.get_json()['success'])

    # -- Security headers + error pages ---------------------------------------
    def test_security_headers_present(self):
        resp = self.client.get('/auth/login')
        self.assertEqual(resp.headers.get('X-Frame-Options'), 'SAMEORIGIN')
        self.assertEqual(resp.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertIn('Content-Security-Policy', resp.headers)

    def test_404_page_is_themed(self):
        resp = self.client.get('/this-page-does-not-exist')
        self.assertEqual(resp.status_code, 404)
        self.assertIn(b'Page not found', resp.data)
        self.assertIn(b'Attenova', resp.data)

    # -- No admin system any more (v4) --------------------------------------
    def test_admin_routes_are_gone(self):
        self._login()
        self.assertEqual(self.client.get('/admin/teachers').status_code, 404)
        self.assertEqual(self.client.get('/admin').status_code, 404)

    def test_no_admin_link_in_sidebar(self):
        self._login()
        resp = self.client.get('/dashboard')
        self.assertNotIn(b'admin.teachers', resp.data)
        self.assertNotIn(b'>Admin<', resp.data)

    def test_settings_threshold_is_saved_and_used(self):
        self._login()
        token = self._csrf_token('/settings/')
        resp = self.client.post('/settings/', data={
            'attendance_threshold': '80',
            'late_threshold_minutes': '10',
            'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(get_threshold(), 80.0)

    # -- Manual correction + ownership ------------------------------------------
    def test_manual_correction_requires_reason_and_logs_override(self):
        self._login()
        student = self._add_student()
        session = AttendanceSession(course_name='Machine Learning',
                                    section='Section B',
                                    start_time=datetime.now(),
                                    is_active=False,
                                    total_duration_minutes=100,
                                    teacher_id=self.teacher.id)
        db.session.add(session)
        db.session.commit()
        record = AttendanceRecord(session_id=session.id,
                                  student_id=student.id,
                                  minutes_present=0.0)
        record.calculate_status(100)
        db.session.add(record)
        db.session.commit()
        token = self._csrf_token('/analytics/')
        resp = self.client.post(
            f'/analytics/correct/{record.id}',
            data={'status': 'Present', 'minutes_present': '90',
                  'reason': 'Camera missed the face',
                  'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        fixed = AttendanceRecord.query.get(record.id)
        self.assertEqual(fixed.status, 'Present')
        self.assertTrue(fixed.manual_override)
        self.assertEqual(fixed.override_reason, 'Camera missed the face')

    def test_teacher_cannot_delete_another_teachers_student(self):
        other = Teacher(name='Other', email='other@iba-suk.edu.pk')
        other.set_password('OtherPass1')
        db.session.add(other)
        db.session.commit()
        student = self._add_student(teacher_id=other.id)
        self._login()  # demo teacher
        token = self._csrf_token('/students/')
        resp = self.client.post(f'/students/delete/{student.id}',
                                data={'csrf_token': token})
        self.assertEqual(resp.status_code, 404)
        self.assertIsNotNone(Student.query.get(student.id))

    # -- Stuck sessions ------------------------------------------------------------
    def test_stuck_session_older_than_12h_is_closed_on_startup(self):
        from app import _close_stuck_sessions
        old = AttendanceSession(
            course_name='Old Class', section='Section B',
            start_time=datetime.now() - timedelta(hours=13),
            is_active=True, total_duration_minutes=100)
        db.session.add(old)
        db.session.commit()
        _close_stuck_sessions(self.app)
        self.assertFalse(AttendanceSession.query.get(old.id).is_active)


if __name__ == '__main__':
    unittest.main()
