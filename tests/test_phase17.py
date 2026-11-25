"""Phase 17 tests (v9.2): dashboard camera switch, cameras table roles,
class-card edit popup, Students page layout, bulk face enrolment on the
Students page, optional correction reason, capture modal with no
scrollbar, and the global rounded-select rule.
"""
import glob
import io
import os
import re
import unittest
import zipfile
from datetime import datetime

import numpy as np
from PIL import Image

from app import create_app, db
from app.models import (AttendanceRecord, AttendanceSession, CameraSource,
                        ClassGroup, Student, Teacher,
                        get_or_create_class_group)
from app.routes.auth import reset_login_attempts
from app.routes.student import reset_student_login_attempts
from app.services.scheduler_service import AutomatedSchedulerService

TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _test_image_bytes():
    """A sharp, well-lit random-noise JPEG (passes the blur check)."""
    arr = np.random.randint(0, 256, (128, 128, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, 'JPEG', quality=90)
    return buf.getvalue()


class Phase17TestCase(unittest.TestCase):
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
        token = self._csrf_token()
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

    def _add_student(self, roll, name, group, email=None):
        student = Student(roll_number=roll, name=name,
                          department=group.department,
                          semester=group.semester,
                          section=group.section,
                          email=email,
                          teacher_id=self.teacher.id,
                          class_group_id=group.id,
                          cms_id=f'CMS-{Student.query.count() + 1:04d}')
        student.set_portal_password('Student1')
        db.session.add(student)
        db.session.commit()
        return student

    def _add_camera(self, name, camera_type='browser', url=None,
                    is_active=False, teacher=None):
        cam = CameraSource(
            teacher_id=(teacher or self.teacher).id,
            name=name, camera_type=camera_type, stream_url=url,
            is_active=is_active)
        db.session.add(cam)
        db.session.commit()
        return cam

    # -- Dashboard camera switch -----------------------------------------
    def test_dashboard_switch_valid_camera_renders_it(self):
        self._login_teacher()
        primary = self._add_camera('Laptop Webcam', 'browser',
                                   is_active=True)
        other = self._add_camera('Phone Cam', 'browser')
        token = self._csrf_token('/dashboard')
        resp = self.client.post('/dashboard/camera/select', data={
            'camera_id': str(other.id), 'csrf_token': token},
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # The dashboard now shows the chosen camera, not the primary.
        self.assertIn('Phone Cam', html)
        self.assertIn(f'<option value="{other.id}" selected>', html)
        # Switching the view never changes the saved Primary Active.
        self.assertTrue(CameraSource.query.get(primary.id).is_active)
        self.assertFalse(CameraSource.query.get(other.id).is_active)

    def test_dashboard_switch_rejects_foreign_and_missing_cameras(self):
        self._login_teacher()
        self._add_camera('Laptop Webcam', 'browser', is_active=True)
        other_teacher = Teacher(name='Other', email='other@iba-suk.edu.pk')
        other_teacher.set_password('OtherPass1')
        db.session.add(other_teacher)
        db.session.commit()
        foreign = self._add_camera('Someone Elses Cam', 'browser',
                                   teacher=other_teacher)
        token = self._csrf_token('/dashboard')
        resp = self.client.post('/dashboard/camera/select', data={
            'camera_id': str(foreign.id), 'csrf_token': token})
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post('/dashboard/camera/select', data={
            'camera_id': '99999', 'csrf_token': token})
        self.assertEqual(resp.status_code, 404)

    # -- Cameras table -----------------------------------------------------
    def test_cameras_table_primary_first_with_roles_no_use_button(self):
        self._login_teacher()
        self._add_camera('Zebra Cam', 'browser')
        primary = self._add_camera('Alpha Cam', 'ip_webcam',
                                   url='http://192.168.1.15:8080/video',
                                   is_active=True)
        viewed = self._add_camera('Middle Cam', 'browser')
        token = self._csrf_token('/dashboard')
        self.client.post('/dashboard/camera/select', data={
            'camera_id': str(viewed.id), 'csrf_token': token})
        resp = self.client.get('/cameras/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # Primary Active sits on top, before every other camera.
        self.assertLess(html.index('Alpha Cam'), html.index('Middle Cam'))
        self.assertLess(html.index('Alpha Cam'), html.index('Zebra Cam'))
        self.assertIn('Primary Active', html)
        self.assertIn('Currently using', html)
        self.assertIn('Standby', html)
        self.assertNotIn('Use This Camera', html)
        # Hardware Type badges are two lines now, never one long label.
        self.assertIn('Wireless<br>IP Stream', html)
        self.assertIn('Browser<br>USB', html)

    # -- Class-card edit popup ---------------------------------------------
    def test_edit_badge_popup_present_and_save_updates_class(self):
        self._login_teacher()
        group = self._make_class()
        resp = self.client.get('/students/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="editClassDialog"', html)
        self.assertIn('Edit Class Details', html)
        self.assertIn('data-iba-edit-class', html)
        self.assertIn(f'data-class-id="{group.id}"', html)
        self.assertIn('data-semester="5th"', html)
        # The card keeps only View Students — Export/Delete live
        # inside the class page now.
        self.assertIn('View Students', html)
        self.assertNotIn('Delete Class', html)
        token = self._csrf_token('/students/')
        resp = self.client.post(
            f'/students/classes/{group.id}/edit', data={
                'name': 'BS CS-AI Section B — Semester 6',
                'department': 'BS Computer Science - AI',
                'semester': '6th', 'section': 'Section B',
                'course_name': 'Mathematics', 'course_code': 'CS-101',
                'csrf_token': token}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        fresh = ClassGroup.query.get(group.id)
        self.assertEqual(fresh.semester, '6th')
        self.assertEqual(fresh.name, 'BS CS-AI Section B — Semester 6')

    # -- Students page layout ----------------------------------------------
    def test_login_box_moved_below_how_classes_work(self):
        self._login_teacher()
        self._make_class()
        resp = self.client.get('/students/')
        html = resp.get_data(as_text=True)
        # The info text is on the page exactly once, and it sits
        # ABOVE the two cards — below "How classes work", not inside
        # the Register New Student card.
        self.assertEqual(html.count('Student login is automatic'), 1)
        self.assertLess(html.index('Student login is automatic'),
                        html.index('Register New Student'))
        self.assertLess(html.index('How classes work'),
                        html.index('Student login is automatic'))
        # Both cards stretch to the same height, buttons at the bottom.
        self.assertIn('items-stretch', html)
        self.assertIn('h-full flex flex-col', html)

    # -- Bulk face enrolment on the Students page ---------------------------
    def test_bulk_photos_on_students_page_with_class_select(self):
        self._login_teacher()
        group = self._make_class(section='Section B')
        good = self._add_student('023-24-0001', 'Good Student', group)
        missing = self._add_student('023-24-0002', 'Missing Photo', group)
        img = _test_image_bytes()
        csv_text = ('roll_number,front,left,right\n'
                    '023-24-0001,023-24-0001_front.jpg,023-24-0001_left.jpg,023-24-0001_right.jpg\n'
                    '023-24-0002,023-24-0002_front.jpg,023-24-0002_left.jpg,023-24-0002_right.jpg\n')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('photos.csv', csv_text)
            for suffix in ('front', 'left', 'right'):
                zf.writestr(f'023-24-0001_{suffix}.jpg', img)
            zf.writestr('023-24-0002_front.jpg', img)
        buf.seek(0)
        token = self._csrf_token('/students/')
        resp = self.client.post(
            '/students/bulk-photos',
            data={'class_group_id': str(group.id),
                  'zip_file': (buf, 'photos.zip'),
                  'csrf_token': token},
            follow_redirects=True, content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # The results render on the Students page, not a class page.
        self.assertIn('Bulk Face Enrolment', html)
        self.assertIn('1 enrolled, 1 with a problem', html)
        self.assertIsNotNone(
            Student.query.get(good.id).face_embedding_json)
        self.assertIsNone(
            Student.query.get(missing.id).face_embedding_json)
        # The old class-based route is gone — nothing links to it.
        self.assertNotIn(f'/students/class/{group.id}/bulk-photos', html)

    def test_bulk_photos_sample_follows_selected_class(self):
        self._login_teacher()
        group = self._make_class(section='Section B')
        self._add_student('023-24-0001', 'Student B1', group)
        other = self._make_class(section='Section C')
        self._add_student('023-24-0002', 'Student C1', other)
        resp = self.client.get(
            f'/students/bulk-photos/sample?class_group_id={group.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('023-24-0001', resp.get_data(as_text=True))
        self.assertNotIn('023-24-0002', resp.get_data(as_text=True))
        # No class chosen yet: no file, just back to the Students page.
        resp = self.client.get('/students/bulk-photos/sample',
                               follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Choose a class first', resp.get_data(as_text=True))

    # -- Correct a record: reason is optional --------------------------------
    def test_correct_record_without_reason_succeeds(self):
        self._login_teacher()
        group = self._make_class()
        student = self._add_student('023-24-0001', 'Student B1', group)
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
                  'reason': '', 'csrf_token': token},
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        fixed = AttendanceRecord.query.get(record.id)
        self.assertEqual(fixed.status, 'Present')
        self.assertTrue(fixed.manual_override)
        self.assertEqual(fixed.override_reason, '')
        html = self.client.get('/analytics/').get_data(as_text=True)
        self.assertIn('Minutes present', html)
        self.assertIn('Reason (optional)', html)
        self.assertNotIn('Reason (required)', html)

    # -- Capture modal: no scrollbar ------------------------------------------
    def test_capture_modal_has_no_scrollbar(self):
        modal = open(os.path.join(
            ROOT, 'app', 'templates', '_capture_modal.html')).read()
        self.assertNotIn('overflow-y-auto', modal)
        self.assertNotIn('max-height: 92vh', modal)
        self.assertIn('max-w-xl', modal)
        self.assertIn('max-height: 30vh', modal)
        self.assertIn('Start 3-Angle Automated Capture', modal)
        self.assertIn('Save & Generate 128-D Vector', modal)
        self.assertIn("Position face inside oval guide", modal)

    # -- Dropdowns: one global rounded rule ------------------------------------
    def test_selects_use_the_global_rounded_rule(self):
        css = open(os.path.join(
            ROOT, 'app', 'static', 'css', 'animations.css')).read()
        self.assertIn('select {', css)
        self.assertIn('border-radius: 0.625rem', css)
        for path in glob.glob(os.path.join(ROOT, 'app', 'templates',
                                           '*.html')):
            with open(path) as fh:
                text = fh.read()
            for match in re.finditer(r'<select\b[^>]*>', text):
                self.assertNotIn('rounded-lg', match.group(0),
                                 f'{path}: a select still has rounded-lg')
                self.assertNotRegex(match.group(0), r'\brounded\b',
                                    f'{path}: a select still has rounded')


if __name__ == '__main__':
    unittest.main()
