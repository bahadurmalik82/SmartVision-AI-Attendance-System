"""Phase 11 tests (v5): classes, per-class attendance scoping, auth
split layout, detached brand header, no university-specific branding,
Sunday timetable + scheduler, camera auto-start status, bulk face
enrolment, and the visual Analytics clustering section.
"""
import io
import re
import unittest
import zipfile
from datetime import datetime, time

import numpy as np
from PIL import Image

from app import create_app, db
from app.models import (AttendanceSession, ClassGroup, Schedule, Student,
                        Teacher, get_or_create_class_group)
from app.routes.auth import reset_login_attempts
from app.routes.student import reset_student_login_attempts
from app.services.scheduler_service import AutomatedSchedulerService

TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _test_image_bytes():
    """A sharp, well-lit random-noise JPEG (passes the blur check)."""
    arr = np.random.randint(0, 256, (128, 128, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, 'JPEG', quality=90)
    return buf.getvalue()


class Phase11TestCase(unittest.TestCase):
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

    def _login_teacher(self, email='demo@iba-suk.edu.pk', password='Demo1234'):
        token = self._csrf_token('/auth/login')
        return self.client.post('/auth/login', data={
            'email': email, 'password': password, 'csrf_token': token,
        }, follow_redirects=True)

    def _make_class(self, department='BS Computer Science - AI',
                    semester='5th', section='Section B', course='Mathematics'):
        group = get_or_create_class_group(
            self.teacher.id, department, semester, section,
            course_name=course)
        db.session.commit()
        return group

    def _add_student(self, roll, name, group, cms_id=None):
        student = Student(roll_number=roll, name=name,
                          department=group.department,
                          semester=group.semester,
                          section=group.section,
                          teacher_id=self.teacher.id,
                          class_group_id=group.id,
                          cms_id=cms_id or f'CMS-{Student.query.count() + 1:04d}')
        student.set_portal_password('Student1')
        db.session.add(student)
        db.session.commit()
        return student

    # -- Auth split layout: no sidebar on login pages ---------------------
    def test_login_pages_have_no_sidebar_but_have_choice_panel(self):
        for url in ['/auth/login', '/auth/register', '/student/login']:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
            html = resp.get_data(as_text=True)
            self.assertNotIn('iba-sidebar', html, url)
            self.assertNotIn('ibaSidebar', html, url)
            self.assertIn('I am Teacher', html, url)
            self.assertIn('I am Student', html, url)
            self.assertIn('Login Account', html, url)
            self.assertIn('Create Account', html, url)
            self.assertIn('Student Portal', html, url)

    def test_teacher_login_still_works_from_new_layout(self):
        resp = self._login_teacher()
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Faculty Portal', resp.data)

    # -- Generic branding --------------------------------------------------
    def test_no_university_specific_branding_on_main_pages(self):
        # The landing page redirects a logged-in teacher to /dashboard,
        # so it is checked logged out below.
        self._login_teacher()
        for url in ['/dashboard', '/timetable/', '/students/',
                    '/analytics/', '/analytics/defaulters',
                    '/analytics/benchmark']:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
            html = resp.get_data(as_text=True)
            self.assertNotIn('Sukkur', html, url)
            self.assertNotIn('CS-302', html, url)
        resp = self.client.get('/student/login')
        self.assertNotIn('Sukkur', resp.get_data(as_text=True))
        self.client.get('/auth/logout', follow_redirects=True)
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('Sukkur', resp.get_data(as_text=True))
        self.assertNotIn('CS-302', resp.get_data(as_text=True))

    # -- Sunday + scheduler -------------------------------------------------
    def test_sunday_timetable_add_works(self):
        self._login_teacher()
        group = self._make_class()
        token = self._csrf_token('/timetable/')
        resp = self.client.post('/timetable/add', data={
            'course_name': 'Mathematics', 'course_code': 'MATH-101',
            'department': group.department, 'semester': group.semester,
            'section': group.section, 'day_of_week': 'Sunday',
            'start_time': '09:00', 'end_time': '10:40',
            'class_group_id': str(group.id),
            'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        sched = Schedule.query.filter_by(day_of_week='Sunday').first()
        self.assertIsNotNone(sched)
        self.assertEqual(sched.class_group_id, group.id)

    def test_scheduler_starts_sunday_class(self):
        group = self._make_class()
        sched = Schedule(
            teacher_id=self.teacher.id, course_name='Mathematics',
            course_code='MATH-101', department=group.department,
            semester=group.semester, section=group.section,
            day_of_week='Sunday', start_time=time(9, 0),
            end_time=time(10, 40), class_group_id=group.id)
        db.session.add(sched)
        db.session.commit()
        # 2026-11-22 is a Sunday.
        AutomatedSchedulerService._check_timetable(
            self.app, now=datetime(2026, 11, 22, 9, 30))
        session = AttendanceSession.query.filter_by(
            schedule_id=sched.id).first()
        self.assertIsNotNone(session)
        self.assertTrue(session.is_active)
        self.assertEqual(session.class_group_id, group.id)

    def test_dashboard_session_status_endpoint(self):
        self._login_teacher()
        resp = self.client.get('/dashboard/session-status')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {'active': False})
        session = AttendanceSession(
            course_name='Mathematics', section='Section B',
            start_time=datetime.now(), is_active=True,
            total_duration_minutes=100, teacher_id=self.teacher.id)
        db.session.add(session)
        db.session.commit()
        resp = self.client.get('/dashboard/session-status')
        data = resp.get_json()
        self.assertTrue(data['active'])
        self.assertEqual(data['course_name'], 'Mathematics')

    # -- Classes: the core v5 fix -------------------------------------------
    def test_finalize_only_marks_the_session_own_class(self):
        group_b = self._make_class(section='Section B')
        group_c = self._make_class(section='Section C')
        b1 = self._add_student('023-24-0001', 'Student B1', group_b)
        b2 = self._add_student('023-24-0002', 'Student B2', group_b)
        c1 = self._add_student('023-24-0003', 'Student C1', group_c)
        c2 = self._add_student('023-24-0004', 'Student C2', group_c)
        sched = Schedule(
            teacher_id=self.teacher.id, course_name='Mathematics',
            course_code='MATH-101', department=group_b.department,
            semester=group_b.semester, section=group_b.section,
            day_of_week='Monday', start_time=time(9, 0),
            end_time=time(10, 40), class_group_id=group_b.id)
        db.session.add(sched)
        db.session.commit()
        session = AutomatedSchedulerService.start_scheduled_session(sched)
        AutomatedSchedulerService.finalize_session(session)
        from app.models import AttendanceRecord
        records = AttendanceRecord.query.filter_by(session_id=session.id).all()
        got = {r.student_id for r in records}
        self.assertEqual(got, {b1.id, b2.id})
        self.assertNotIn(c1.id, got)
        self.assertNotIn(c2.id, got)

    def test_create_class_then_enrol_student_into_it(self):
        self._login_teacher()
        token = self._csrf_token('/students/')
        resp = self.client.post('/students/classes/create', data={
            'name': 'BS CS-AI Section C — Semester 5',
            'department': 'BS Computer Science - AI', 'semester': '5th',
            'section': 'Section C', 'course_name': 'Mathematics',
            'course_code': 'MATH-101', 'csrf_token': token},
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        group = ClassGroup.query.filter_by(section='Section C').first()
        self.assertIsNotNone(group)
        token = self._csrf_token('/students/')
        resp = self.client.post('/students/enroll', data={
            'roll_number': '023-24-0001', 'name': 'Hamza Channa',
            'email': 'h@x.pk', 'class_group_id': str(group.id),
            'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        student = Student.query.filter_by(roll_number='023-24-0001').first()
        self.assertEqual(student.class_group_id, group.id)
        self.assertEqual(student.section, 'Section C')
        self.assertEqual(student.cms_id, 'CMS-0001')

    def test_csv_import_goes_into_the_chosen_class(self):
        self._login_teacher()
        group = self._make_class(section='Section B')
        other = self._make_class(section='Section C')
        token = self._csrf_token('/students/')
        csv_text = ('roll_number,name,department,semester,section,email\n'
                    '023-24-0001,Hamza Channa,Other Dept,1st,Section Z,h@x.pk\n'
                    '023-24-0002,Amara Latif,Other Dept,1st,Section Z,a@x.pk\n')
        resp = self.client.post('/students/import', data={
            'csv_file': (io.BytesIO(csv_text.encode()), 'students.csv'),
            'class_group_id': str(group.id),
            'csrf_token': token}, follow_redirects=True,
            content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'2 student(s) imported', resp.data)
        for roll in ('023-24-0001', '023-24-0002'):
            s = Student.query.filter_by(roll_number=roll).first()
            self.assertEqual(s.class_group_id, group.id)
            self.assertEqual(s.section, 'Section B')
        self.assertEqual(Student.query.filter_by(
            class_group_id=other.id).count(), 0)

    def test_class_detail_shows_only_its_students(self):
        self._login_teacher()
        group_b = self._make_class(section='Section B')
        group_c = self._make_class(section='Section C')
        self._add_student('023-24-0001', 'Student B1', group_b)
        self._add_student('023-24-0002', 'Student C1', group_c)
        resp = self.client.get(f'/students/class/{group_b.id}')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('Student B1', html)
        self.assertNotIn('Student C1', html)
        # No enrol forms on the class list page; capture modal is there.
        self.assertNotIn('action="/students/enroll"', html)
        self.assertNotIn('action="/students/import"', html)
        self.assertIn('captureModal', html)
        self.assertIn('Bulk Face Enrolment', html)
        self.assertIn('iba-scroll-list', html)

    def test_delete_class_removes_only_its_students(self):
        self._login_teacher()
        group_b = self._make_class(section='Section B')
        group_c = self._make_class(section='Section C')
        self._add_student('023-24-0001', 'Student B1', group_b)
        self._add_student('023-24-0002', 'Student C1', group_c)
        token = self._csrf_token(f'/students/class/{group_b.id}')
        resp = self.client.post(
            f'/students/classes/{group_b.id}/delete',
            data={'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(Student.query.filter_by(
            roll_number='023-24-0001').first())
        self.assertIsNotNone(Student.query.filter_by(
            roll_number='023-24-0002').first())
        self.assertIsNone(ClassGroup.query.get(group_b.id))

    def test_manual_start_is_tied_to_the_chosen_class(self):
        self._login_teacher()
        group = self._make_class(section='Section B')
        token = self._csrf_token('/dashboard')
        resp = self.client.post('/start-class-manual', data={
            'class_group_id': str(group.id), 'csrf_token': token},
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        session = AttendanceSession.query.filter_by(is_active=True).first()
        self.assertIsNotNone(session)
        self.assertEqual(session.class_group_id, group.id)

    # -- Backfill: old rows get a class on startup ---------------------------
    def test_restart_backfill_assigns_classes_to_old_students(self):
        from app import _migrate_and_backfill
        group_b = self._make_class(section='Section B')
        student = self._add_student('023-24-0001', 'Student B1', group_b)
        # Simulate a pre-v5 row: no class at all.
        student.class_group_id = None
        db.session.commit()
        _migrate_and_backfill(self.app)
        fresh = Student.query.get(student.id)
        self.assertIsNotNone(fresh.class_group_id)
        self.assertEqual(
            Student.query.filter(Student.class_group_id.is_(None)).count(), 0)

    # -- Bulk face enrolment ---------------------------------------------------
    def test_bulk_photos_enrols_good_student_and_reports_bad_ones(self):
        self._login_teacher()
        group = self._make_class(section='Section B')
        good = self._add_student('023-24-0001', 'Good Student', group)
        missing = self._add_student('023-24-0002', 'Missing Photo', group)
        img = _test_image_bytes()
        csv_text = ('roll_number,front,left,right\n'
                    '023-24-0001,023-24-0001_front.jpg,023-24-0001_left.jpg,023-24-0001_right.jpg\n'
                    '023-24-0002,023-24-0002_front.jpg,023-24-0002_left.jpg,023-24-0002_right.jpg\n'
                    '023-24-9999,023-24-9999_front.jpg,023-24-9999_left.jpg,023-24-9999_right.jpg\n')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('photos.csv', csv_text)
            for suffix in ('front', 'left', 'right'):
                zf.writestr(f'023-24-0001_{suffix}.jpg', img)
            # Missing Photo student: only the front photo is in the ZIP.
            zf.writestr('023-24-0002_front.jpg', img)
        buf.seek(0)
        token = self._csrf_token(f'/students/class/{group.id}')
        resp = self.client.post(
            f'/students/class/{group.id}/bulk-photos',
            data={'zip_file': (buf, 'photos.zip'), 'csrf_token': token},
            follow_redirects=True, content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('1 enrolled, 2 with a problem', html)
        self.assertIsNotNone(
            Student.query.get(good.id).face_embedding_json)
        self.assertIsNone(
            Student.query.get(missing.id).face_embedding_json)
        self.assertIn('not enrolled yet', html)
        self.assertIn('Missing photo', html)

    # -- Analytics visual -------------------------------------------------------
    def test_analytics_clustering_is_visual_not_a_name_list(self):
        self._login_teacher()
        group = self._make_class(section='Section B')
        self._add_student('023-24-0001', 'Student B1', group)
        self._add_student('023-24-0002', 'Student B2', group)
        resp = self.client.get('/analytics/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertNotIn('Scatter data (Attendance', html)
        self.assertIn('How the class splits', html)
        self.assertIn('Where every student sits', html)
        self.assertIn('No class has run yet', html)


if __name__ == '__main__':
    unittest.main()
