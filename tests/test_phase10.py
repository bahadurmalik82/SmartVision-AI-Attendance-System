"""Phase 10 tests (v4): landing page, open teacher registration,
student CMS ID + password system, student dashboard numbers,
change/reset password, CSV import credentials, no-admin, and the
v3 -> v4 migration run on a COPY of a pre-v4 database.
"""
import os
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, time, timedelta

from flask_bcrypt import Bcrypt

from app import create_app, db
from app.models import (AttendanceRecord, AttendanceSession, Student,
                        Teacher)
from app.routes.auth import reset_login_attempts
from app.routes.student import reset_student_login_attempts
from app.services.settings import get_threshold

TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


class Phase10TestCase(unittest.TestCase):
    def setUp(self):
        reset_login_attempts()
        reset_student_login_attempts()
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
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    # -- helpers ---------------------------------------------------------
    def _csrf_token(self, url='/auth/login'):
        resp = self.client.get(url)
        match = TOKEN_RE.search(resp.get_data(as_text=True))
        self.assertIsNotNone(match, f'No CSRF token found on {url}')
        return match.group(1)

    def _login_teacher(self, email='demo@iba-suk.edu.pk',
                       password='Demo1234'):
        token = self._csrf_token('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': password, 'csrf_token': token,
        }, follow_redirects=True)

    def _add_student(self, roll='K21-0145', name='Ahmed Ali',
                     cms_id='CMS-0001', password='StudPass1'):
        student = Student(roll_number=roll, name=name, cms_id=cms_id,
                          teacher_id=self.teacher.id)
        student.set_portal_password(password)
        db.session.add(student)
        db.session.commit()
        return student

    def _login_student(self, cms_id='CMS-0001', password='StudPass1'):
        token = self._csrf_token('/student/login')
        return self.client.post('/student/login', data={
            'cms_id': cms_id, 'password': password, 'csrf_token': token,
        }, follow_redirects=True)

    def _make_session_with_records(self, student, minutes_list):
        """One Machine Learning session + one record per minutes value."""
        session = AttendanceSession(
            course_name='Machine Learning', section='Section B',
            session_date=datetime.now().date(),
            start_time=datetime.now() - timedelta(hours=2),
            end_time=datetime.now() - timedelta(minutes=20),
            is_active=False, total_duration_minutes=100,
            teacher_id=self.teacher.id)
        db.session.add(session)
        db.session.commit()
        schedule = None
        from app.models import Schedule
        schedule = Schedule(
            teacher_id=self.teacher.id, course_name='Machine Learning',
            course_code='CS-302', department='Computer Science',
            semester='5th', section='Section B', day_of_week='Monday',
            start_time=time(9, 0), end_time=time(10, 40))
        db.session.add(schedule)
        db.session.commit()
        session.schedule_id = schedule.id
        db.session.commit()
        for minutes in minutes_list:
            record = AttendanceRecord(session_id=session.id,
                                      student_id=student.id,
                                      minutes_present=float(minutes),
                                      first_seen=session.start_time)
            record.calculate_status(100)
            db.session.add(record)
        db.session.commit()
        return session

    # -- Landing page ------------------------------------------------------
    def test_landing_page_has_both_doors(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('I am a Student', html)
        self.assertIn('I am a Teacher', html)
        self.assertIn('/student/login', html)
        self.assertIn('/auth/login', html)
        self.assertIn('Attenova', html)

    def test_landing_redirects_logged_in_teacher_to_dashboard(self):
        self._login_teacher()
        resp = self.client.get('/', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/dashboard', resp.headers['Location'])

    def test_dashboard_moved_off_root(self):
        resp = self.client.get('/dashboard', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/login', resp.headers['Location'])
        self._login_teacher()
        resp = self.client.get('/dashboard')
        self.assertEqual(resp.status_code, 200)

    # -- Open teacher registration -----------------------------------------
    def test_second_and_third_teachers_can_register(self):
        for i, email in enumerate(['t2@iba-suk.edu.pk',
                                   't3@iba-suk.edu.pk']):
            self.client.get('/auth/logout', follow_redirects=True)
            token = self._csrf_token('/auth/register')
            resp = self.client.post('/auth/register', data={
                'name': f'Teacher {i + 2}', 'email': email,
                'department': 'Computer Science',
                'password': 'Teach1234', 'confirm_password': 'Teach1234',
                'csrf_token': token}, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIsNotNone(
                Teacher.query.filter_by(email=email).first())
            self.assertIn(b'Faculty Portal', resp.data)

    # -- Student enrolment credentials --------------------------------------
    def test_enroll_assigns_cms_id_and_password(self):
        self._login_teacher()
        token = self._csrf_token('/students/')
        resp = self.client.post('/students/enroll', data={
            'roll_number': 'K21-0145', 'name': 'Ahmed Ali',
            'email': 'a@x.pk', 'department': 'Computer Science',
            'semester': '5th', 'section': 'Section B',
            'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        student = Student.query.filter_by(roll_number='K21-0145').first()
        self.assertIsNotNone(student)
        self.assertEqual(student.cms_id, 'CMS-0001')
        self.assertTrue(student.portal_password_hash)
        self.assertTrue(student.portal_password_plain)
        self.assertEqual(len(student.portal_password_plain), 8)
        self.assertIn(b'CMS-0001', resp.data)
        self.assertIn(student.portal_password_plain.encode(), resp.data)

    def test_second_enrolled_student_gets_next_cms_id(self):
        self._login_teacher()
        token = self._csrf_token('/students/')
        for roll, name in [('K21-0145', 'Ahmed Ali'),
                           ('K21-0231', 'Sara Ahmed')]:
            self.client.post('/students/enroll', data={
                'roll_number': roll, 'name': name,
                'department': 'Computer Science', 'semester': '5th',
                'section': 'Section B', 'csrf_token': token},
                follow_redirects=True)
            token = self._csrf_token('/students/')
        students = Student.query.order_by(Student.id).all()
        self.assertEqual([s.cms_id for s in students],
                         ['CMS-0001', 'CMS-0002'])
        self.assertNotEqual(students[0].portal_password_plain,
                            students[1].portal_password_plain)

    def test_teacher_reset_password_works(self):
        student = self._add_student()
        old_pw = student.portal_password_plain
        self._login_teacher()
        token = self._csrf_token('/students/')
        resp = self.client.post(
            f'/students/reset-password/{student.id}',
            data={'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        fresh = Student.query.get(student.id)
        self.assertNotEqual(fresh.portal_password_plain, old_pw)
        self.assertTrue(fresh.check_portal_password(
            fresh.portal_password_plain))
        self.assertFalse(fresh.check_portal_password(old_pw))

    def test_csv_import_assigns_credentials(self):
        self._login_teacher()
        token = self._csrf_token('/students/')
        csv_text = ('roll_number,name,department,semester,section,email\n'
                    'K21-0145,Ahmed Ali,Computer Science,5th,Section B,a@x.pk\n'
                    'K21-0231,Sara Ahmed,Computer Science,5th,Section B,s@x.pk\n')
        import io
        resp = self.client.post('/students/import', data={
            'csv_file': (io.BytesIO(csv_text.encode()), 'students.csv'),
            'csrf_token': token}, follow_redirects=True,
            content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 200)
        students = Student.query.order_by(Student.id).all()
        self.assertEqual(len(students), 2)
        self.assertEqual([s.cms_id for s in students],
                         ['CMS-0001', 'CMS-0002'])
        for s in students:
            self.assertTrue(s.check_portal_password(
                s.portal_password_plain))
        self.assertIn(b'2 student(s) imported', resp.data)

    # -- Student login + dashboard -------------------------------------------
    def test_student_login_wrong_password_is_generic(self):
        self._add_student()
        token = self._csrf_token('/student/login')
        resp = self.client.post('/student/login', data={
            'cms_id': 'CMS-0001', 'password': 'WrongPass1',
            'csrf_token': token}, follow_redirects=True)
        self.assertIn(b'Invalid CMS ID or password', resp.data)
        self.assertNotIn(b'Ahmed Ali', resp.data)

    def test_student_login_unknown_cms_id_is_same_generic_error(self):
        token = self._csrf_token('/student/login')
        resp = self.client.post('/student/login', data={
            'cms_id': 'CMS-9999', 'password': 'Whatever1',
            'csrf_token': token}, follow_redirects=True)
        self.assertIn(b'Invalid CMS ID or password', resp.data)

    def test_student_dashboard_numbers_match_the_database(self):
        student = self._add_student()
        other = self._add_student(roll='K21-0231', name='Sara Ahmed',
                                  cms_id='CMS-0002', password='SaraPass1')
        # Ahmed: 96 (Present) + 45 (Absent) minutes of 100-minute classes.
        self._make_session_with_records(student, [96, 45])
        # Sara has different records — they must never leak into Ahmed's view.
        self._make_session_with_records(other, [100])
        resp = self._login_student()
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Ahmed Ali', html)
        self.assertNotIn('Sara Ahmed', html)
        self.assertIn('70.5%', html)          # overall = (96+45)/2
        self.assertIn('Machine Learning', html)
        # 1 attended, 1 absent of 2 held; needs 1 more class in a row.
        self.assertIn('Attend the next 1 class in a row', html)

    def test_student_dashboard_without_records(self):
        self._add_student()
        resp = self._login_student()
        html = resp.get_data(as_text=True)
        self.assertIn('No classes recorded for you yet', html)
        self.assertIn('0.0%', html)

    def test_student_dashboard_requires_login(self):
        resp = self.client.get('/student/dashboard', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/student/login', resp.headers['Location'])

    def test_student_change_password_needs_old_password(self):
        student = self._add_student()
        self._login_student()
        token = self._csrf_token('/student/change-password')
        resp = self.client.post('/student/change-password', data={
            'old_password': 'StudPass1', 'new_password': 'NewPass99',
            'confirm_password': 'NewPass99',
            'csrf_token': token}, follow_redirects=True)
        self.assertIn(b'Password changed successfully', resp.data)
        self.client.get('/student/logout', follow_redirects=True)
        # Old password no longer works; new one does.
        resp = self._login_student(password='StudPass1')
        self.assertIn(b'Invalid CMS ID or password', resp.data)
        resp = self._login_student(password='NewPass99')
        self.assertIn(b'Ahmed Ali', resp.data)

    def test_student_change_password_rejects_wrong_old_password(self):
        self._add_student()
        self._login_student()
        token = self._csrf_token('/student/change-password')
        resp = self.client.post('/student/change-password', data={
            'old_password': 'WrongOld1', 'new_password': 'NewPass99',
            'confirm_password': 'NewPass99',
            'csrf_token': token}, follow_redirects=True)
        self.assertIn(b'old password is incorrect', resp.data)
        still = Student.query.filter_by(cms_id='CMS-0001').first()
        self.assertTrue(still.check_portal_password('StudPass1'))

    def test_student_login_lockout_after_five_failures(self):
        self._add_student()
        for _ in range(5):
            token = self._csrf_token('/student/login')
            self.client.post('/student/login', data={
                'cms_id': 'CMS-0001', 'password': 'WrongPass1',
                'csrf_token': token})
        token = self._csrf_token('/student/login')
        resp = self.client.post('/student/login', data={
            'cms_id': 'CMS-0001', 'password': 'StudPass1',
            'csrf_token': token}, follow_redirects=True)
        self.assertIn(b'Too many failed login attempts', resp.data)

    def test_student_forms_require_csrf_token(self):
        self._add_student()
        resp = self.client.post('/student/login', data={
            'cms_id': 'CMS-0001', 'password': 'StudPass1'})
        self.assertEqual(resp.status_code, 400)


class MigrationOnCopyTestCase(unittest.TestCase):
    """Run the v3 -> v4 migration on a COPY of a pre-v4 database.

    The copy simulates the user's real database: teachers table with
    is_admin/is_active columns (the pre-v3 teacher has is_active NULL,
    exactly like the v3.1 bug), students with teacher_id + pin_hash but
    NO cms_id / portal_password columns, and a legacy session.
    """

    def setUp(self):
        reset_login_attempts()
        reset_student_login_attempts()
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, 'attendance-v3-copy.db')
        bcrypt = Bcrypt()
        pw_hash = bcrypt.generate_password_hash(
            'Demo1234').decode('utf-8')
        con = sqlite3.connect(self.db_path)
        con.execute(
            "CREATE TABLE teachers (id INTEGER PRIMARY KEY, "
            "name VARCHAR(120) NOT NULL, email VARCHAR(150) NOT NULL UNIQUE, "
            "password_hash VARCHAR(256) NOT NULL, department VARCHAR(100), "
            "created_at DATETIME, is_admin BOOLEAN, is_active BOOLEAN)")
        # Pre-v3 teacher: is_active NULL (v3.1 bug state).
        con.execute(
            "INSERT INTO teachers (name, email, password_hash, department, "
            "is_admin, is_active) VALUES (?,?,?,?,?,?)",
            ('Demo Teacher', 'demo@iba-suk.edu.pk', pw_hash,
             'Computer Science', None, None))
        con.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, "
            "roll_number VARCHAR(50) NOT NULL UNIQUE, name VARCHAR(120) "
            "NOT NULL, department VARCHAR(100), semester VARCHAR(50), "
            "section VARCHAR(20), email VARCHAR(150), "
            "teacher_id INTEGER, pin_hash VARCHAR(256), "
            "face_embedding_json TEXT, created_at DATETIME)")
        pin_hash = bcrypt.generate_password_hash('1234').decode('utf-8')
        con.execute(
            "INSERT INTO students (roll_number, name, department, "
            "semester, section, email, teacher_id, pin_hash) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ('K21-0145', 'Ahmed Ali', 'Computer Science', '5th',
             'Section B', 'ahmed.ali@iba-suk.edu.pk', None, pin_hash))
        con.execute(
            "INSERT INTO students (roll_number, name, department, "
            "semester, section, email, teacher_id, pin_hash) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ('K21-0231', 'Sara Ahmed', 'Computer Science', '5th',
             'Section B', 'sara.ahmed@iba-suk.edu.pk', 1, pin_hash))
        con.execute(
            "CREATE TABLE attendance_sessions (id INTEGER PRIMARY KEY, "
            "schedule_id INTEGER, teacher_id INTEGER, "
            "course_name VARCHAR(150) NOT NULL, section VARCHAR(20), "
            "session_date DATE NOT NULL, start_time DATETIME, "
            "end_time DATETIME, is_active BOOLEAN, "
            "total_duration_minutes INTEGER)")
        con.execute(
            "INSERT INTO attendance_sessions (teacher_id, course_name, "
            "section, session_date, start_time, is_active, "
            "total_duration_minutes) VALUES (?,?,?,?,?,?,?)",
            (None, 'Machine Learning', 'Section B', '2026-11-20',
             '2026-11-20 09:00:00', 0, 100))
        con.commit()
        con.close()

        # Point a fresh app at the copy (TESTING so no scheduler starts).
        from app.config import config_by_name, DevelopmentConfig
        uri = 'sqlite:///' + self.db_path

        class MigrationConfig(DevelopmentConfig):
            TESTING = True
            DEBUG = False
            SQLALCHEMY_DATABASE_URI = uri

        config_by_name['migration_test'] = MigrationConfig
        self.app = create_app('migration_test')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

    def tearDown(self):
        reset_login_attempts()
        reset_student_login_attempts()
        db.session.remove()
        self.app_context.pop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_backfill_gives_every_student_cms_id_and_password(self):
        students = Student.query.order_by(Student.id).all()
        self.assertEqual(len(students), 2)
        self.assertEqual([s.cms_id for s in students],
                         ['CMS-0001', 'CMS-0002'])
        for s in students:
            self.assertTrue(s.portal_password_hash)
            self.assertTrue(s.portal_password_plain)
            self.assertEqual(len(s.portal_password_plain), 8)
            self.assertTrue(s.check_portal_password(
                s.portal_password_plain))
        # NULL counts after backfill must be zero (the skill's rule).
        null_cms = Student.query.filter(
            Student.cms_id.is_(None)).count()
        null_pw = Student.query.filter(
            Student.portal_password_hash.is_(None)).count()
        self.assertEqual(null_cms, 0)
        self.assertEqual(null_pw, 0)
        # Legacy students were assigned to the first teacher.
        self.assertTrue(all(s.teacher_id == 1 for s in students))

    def test_pre_v3_teacher_can_log_in_after_migration(self):
        teacher = Teacher.query.filter_by(
            email='demo@iba-suk.edu.pk').first()
        self.assertTrue(teacher.is_active)  # v3.1 NULL repair still works
        token_resp = self.client.get('/auth/login')
        match = TOKEN_RE.search(token_resp.get_data(as_text=True))
        token = match.group(1) if match else ''
        resp = self.client.post('/auth/login', data={
            'email': 'demo@iba-suk.edu.pk', 'password': 'Demo1234',
            'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Faculty Portal', resp.data)

    def test_backfilled_legacy_student_can_log_in(self):
        student = Student.query.filter_by(
            roll_number='K21-0145').first()
        resp = self.client.post('/student/login', data={
            'cms_id': student.cms_id,
            'password': student.portal_password_plain,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Ahmed Ali', resp.data)


if __name__ == '__main__':
    unittest.main()
