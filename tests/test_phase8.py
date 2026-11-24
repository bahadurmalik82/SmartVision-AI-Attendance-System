"""Phase 8 tests: scheduler start guard, graceful failures, packaging
files, train-model guard, and the logged-in / logged-out smoke test."""
import os
import unittest
from datetime import datetime

from app import create_app, db
from app.models import AttendanceSession, Student, Teacher
from app.services.scheduler_service import AutomatedSchedulerService

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Phase8TestCase(unittest.TestCase):
    def setUp(self):
        # A previous test file may have started the scheduler (old tests
        # use the development config); stop it so the guard can be tested.
        AutomatedSchedulerService.shutdown()
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        teacher = Teacher(name="Demo Teacher", email="demo@iba-suk.edu.pk",
                          department="Computer Science")
        teacher.set_password("Demo1234")
        db.session.add(teacher)
        db.session.commit()
        self.teacher = teacher

    def tearDown(self):
        AutomatedSchedulerService.shutdown()
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self):
        return self.client.post('/auth/login', data={
            'email': 'demo@iba-suk.edu.pk', 'password': 'Demo1234',
        }, follow_redirects=True)

    # --- Scheduler guard ---
    def test_testing_app_does_not_start_scheduler(self):
        self.assertIsNone(AutomatedSchedulerService._scheduler)

    def test_scheduler_starts_once_and_is_idempotent(self):
        first = AutomatedSchedulerService.init_scheduler(self.app)
        self.assertIsNotNone(first)
        self.assertTrue(first.running)
        second = AutomatedSchedulerService.init_scheduler(self.app)
        self.assertIs(first, second)
        AutomatedSchedulerService.shutdown()
        self.assertIsNone(AutomatedSchedulerService._scheduler)

    def test_finalize_session_empty_db_zero_duration_never_crashes(self):
        session = AttendanceSession(
            course_name="Machine Learning", section="Section B",
            start_time=datetime.now(), is_active=True,
            total_duration_minutes=0)
        db.session.add(session)
        db.session.commit()
        # No students enrolled at all, and a 0-minute session.
        AutomatedSchedulerService.finalize_session(session)
        self.assertFalse(session.is_active)
        self.assertIsNotNone(session.end_time)

    def test_finalize_session_with_detected_student_zero_duration(self):
        student = Student(roll_number="K21-0145", name="Ahmed Ali")
        db.session.add(student)
        db.session.commit()
        tracker = AutomatedSchedulerService.get_duration_tracker()
        tracker.start_session(start_time=datetime.now())
        tracker.record_detection(student_id="Ahmed Ali", student_name="Ahmed Ali")
        session = AttendanceSession(
            course_name="Machine Learning", section="Section B",
            start_time=datetime.now(), is_active=True,
            total_duration_minutes=0)
        db.session.add(session)
        db.session.commit()
        # Previously this divided by zero in the duration tracker.
        AutomatedSchedulerService.finalize_session(session)
        self.assertFalse(session.is_active)

    # --- Train-model guard (Bug 1: missing numpy import / missing folder) ---
    def test_train_model_enrolled_with_too_few_students_redirects_not_500(self):
        self._login()
        resp = self.client.post('/analytics/train-model',
                                data={'source': 'enrolled'})
        self.assertIn(resp.status_code, (302, 303))
        resp2 = self.client.post('/analytics/train-model',
                                 data={'source': 'enrolled'},
                                 follow_redirects=True)
        self.assertEqual(resp2.status_code, 200)
        self.assertIn(b'At least 2 students', resp2.data)

    def test_train_model_dataset_folder_trains_successfully(self):
        # The project ships a small synthetic sample dataset in
        # 'Celebrity Images' so the benchmark can run out of the box.
        self._login()
        resp = self.client.post('/analytics/train-model',
                                data={'source': 'dataset'},
                                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'ML Model training complete', resp.data)

    # --- Packaging files ---
    def test_readme_and_env_example_exist(self):
        readme = os.path.join(BASE, 'README.md')
        env_example = os.path.join(BASE, '.env.example')
        self.assertTrue(os.path.exists(readme))
        self.assertTrue(os.path.exists(env_example))
        with open(readme) as f:
            text = f.read()
        # v8: the demo account is gone — the README must not print
        # demo credentials or tell the reader to run a seeder.
        self.assertNotIn('demo@iba-suk.edu.pk', text)
        self.assertNotIn('Demo1234', text)
        self.assertNotIn('seed_demo.py', text)
        self.assertIn('no demo account', text)
        with open(env_example) as f:
            env_text = f.read()
        self.assertIn('SECRET_KEY', env_text)
        self.assertIn('SMTP_HOST', env_text)
        # No real .env with secrets may be shipped.
        self.assertFalse(os.path.exists(os.path.join(BASE, '.env')))

    def test_requirements_include_scipy_and_openpyxl(self):
        with open(os.path.join(BASE, 'requirements.txt')) as f:
            reqs = f.read()
        self.assertIn('scipy', reqs)
        self.assertIn('openpyxl', reqs)

    def test_seed_script_removed(self):
        # v8: the demo seeder is deleted — there is no demo account.
        self.assertFalse(os.path.exists(os.path.join(BASE, 'seed_demo.py')))

    # --- Smoke test: every main page, logged in and logged out ---
    def test_smoke_logged_in_pages_never_500(self):
        self._login()
        for url in ['/', '/dashboard', '/timetable/', '/cameras/', '/students/',
                    '/analytics/', '/analytics/defaulters',
                    '/analytics/benchmark', '/portal/', '/student/login']:
            resp = self.client.get(url)
            self.assertIn(resp.status_code, (200, 302),
                          f"{url} returned {resp.status_code}")
            self.assertNotEqual(resp.status_code, 500, url)

    def test_smoke_logged_out_redirects_except_public_pages(self):
        for url in ['/dashboard', '/timetable/', '/cameras/', '/students/',
                    '/analytics/', '/analytics/defaulters',
                    '/analytics/benchmark']:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertIn('/auth/login', resp.headers['Location'])
        # The landing page and the student login are public by design.
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'I am a Student', resp.data)
        resp = self.client.get('/student/login')
        self.assertEqual(resp.status_code, 200)
        # The old /portal/ URL redirects to the new student login.
        resp = self.client.get('/portal/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/student/login', resp.headers['Location'])


if __name__ == '__main__':
    unittest.main()
