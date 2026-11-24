"""Phase 13 tests (v8.1): bottom-right toast notifications, the
Registered Cameras table layout, and the Capture Photos modal.

v8 problems fixed here:
  1. Flash notifications were full-width in-flow banners at the top
     of the page, pushing every element below them down. They are
     now fixed bottom-right toasts (narrow, translucent, vertical
     stack) that live as direct children of <body>.
  2. The Registered Cameras table had display:flex on a <td>, which
     broke table layout, and the long stream URL forced the table
     wider than its card.
  3. The Capture Photos modal was included inside the page content,
     where the page animation's transform shifted its fixed
     positioning off the viewport. It now lives at body level.
"""
import os
import re
import unittest

from app import create_app, db
from app.models import (CameraSource, Student, Teacher,
                        get_or_create_class_group)
from app.routes.auth import reset_login_attempts
from app.routes.student import reset_student_login_attempts
from app.services.scheduler_service import AutomatedSchedulerService

TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
HERE = os.path.dirname(os.path.abspath(__file__))


def _read(rel_path):
    with open(os.path.join(HERE, '..', rel_path), encoding='utf-8') as fh:
        return fh.read()


class Phase13TestCase(unittest.TestCase):
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

    def _make_class(self, section='Section B'):
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

    def _add_camera(self, name, camera_type, stream_url, is_active):
        cam = CameraSource(teacher_id=self.teacher.id, name=name,
                           camera_type=camera_type, stream_url=stream_url,
                           is_active=is_active)
        db.session.add(cam)
        db.session.commit()
        return cam

    # -- (1) toast stack exists and lives at body level -------------------
    def test_toast_stack_is_present_and_outside_main(self):
        self._login_teacher()
        resp = self.client.get('/dashboard')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('iba-toast-stack', html)
        # The stack comes AFTER </main>: it is outside the page flow,
        # so it can never push page content down.
        self.assertLess(html.index('</main>'), html.index('iba-toast-stack'))

    def test_toast_stack_present_on_login_pages(self):
        for url in ['/auth/login', '/student/login']:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
            html = resp.get_data(as_text=True)
            self.assertIn('iba-toast-stack', html, url)
            self.assertLess(html.index('</main>'),
                            html.index('iba-toast-stack'), url)

    # -- (2) a flashed message renders as a toast -------------------------
    def test_flashed_message_renders_as_success_toast(self):
        self._login_teacher()
        with self.client.session_transaction() as sess:
            sess['_flashes'] = [
                ('success', 'Class closed. Attendance report generated!')]
        resp = self.client.get('/dashboard')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        match = re.search(
            r'<div class="iba-flash iba-toast iba-toast--success">(.*?)</div>',
            html, re.DOTALL)
        self.assertIsNotNone(match, 'success toast not rendered')
        self.assertIn('Class closed. Attendance report generated!',
                      match.group(1))
        self.assertIn('iba-toast-close', html)

    def test_base_template_has_no_in_flow_flash_wrapper(self):
        source = _read('app/templates/base.html')
        self.assertIn('iba-toast-stack', source)
        # The old full-width wrapper that pushed content down is gone.
        self.assertNotIn(
            'max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 mt-4 w-full', source)

    # -- (3) toast CSS ------------------------------------------------------
    def test_toast_css_is_fixed_bottom_right_narrow_transparent(self):
        css = _read('app/static/css/animations.css')
        stack_at = css.index('.iba-toast-stack {')
        stack_block = css[stack_at:stack_at + 500]
        self.assertIn('position: fixed', stack_block)
        self.assertIn('bottom: 1rem', stack_block)
        self.assertIn('right: 1rem', stack_block)
        self.assertIn('320px', stack_block)
        success_at = css.index('.iba-toast--success')
        self.assertIn('rgba(', css[success_at:success_at + 200])
        key_at = css.index('@keyframes iba-flash-in')
        self.assertIn('translateX', css[key_at:key_at + 200])

    # -- (4) Registered Cameras table ---------------------------------------
    def test_cameras_table_layout_fixed(self):
        self._add_camera('Front Wall Cam', 'ip_webcam',
                         'http://10.102.161.151:8080/video', False)
        self._add_camera('Laptop Webcam', 'browser', None, True)
        self._login_teacher()
        resp = self.client.get('/cameras/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # The broken display:flex on the Device Name <td> is gone.
        # (Checked with the p-3.5 prefix: the page h1 legitimately
        # uses "font-bold text-slate-900 flex items-center".)
        self.assertNotIn('p-3.5 font-bold text-slate-900 flex items-center',
                         html)
        self.assertIn('break-all', html)
        self.assertIn('<colgroup>', html)
        self.assertIn('justify-end gap-2', html)
        self.assertIn('Front Wall Cam', html)
        self.assertIn('Laptop Webcam', html)
        self.assertIn('http://10.102.161.151:8080/video', html)

    # -- (5) Capture Photos modal lives at body level -----------------------
    def test_capture_modal_is_at_body_level(self):
        group = self._make_class()
        self._add_student('023-24-0001', 'Hamza Channa', group)
        self._login_teacher()
        resp = self.client.get(f'/students/class/{group.id}')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="captureModal"', html)
        self.assertLess(html.index('</main>'), html.index('id="captureModal"'))
        # The include moved out of the class page and into base.html.
        self.assertNotIn('_capture_modal',
                         _read('app/templates/class_detail.html'))
        self.assertIn('_capture_modal', _read('app/templates/base.html'))
        # The long action-button labels may wrap instead of overflowing.
        modal_source = _read('app/templates/_capture_modal.html')
        self.assertIn('iba-btn-wrap', modal_source)
        css = _read('app/static/css/animations.css')
        wrap_at = css.index('.iba-btn-wrap')
        self.assertIn('white-space: normal', css[wrap_at:wrap_at + 200])

    # -- (6) regression: the modal still opens ------------------------------
    def test_capture_photos_button_still_present(self):
        group = self._make_class()
        self._add_student('023-24-0001', 'Hamza Channa', group)
        self._login_teacher()
        resp = self.client.get(f'/students/class/{group.id}')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('openCaptureModal', html)
        self.assertIn('Capture Photos', html)


if __name__ == '__main__':
    unittest.main()
