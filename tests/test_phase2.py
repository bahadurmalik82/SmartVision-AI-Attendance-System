import unittest
from datetime import time
from app import create_app, db
from app.models import Teacher, Schedule, CameraSource


class Phase2TestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('development')
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_unauthorized_access_redirect(self):
        """Verify protected routes redirect to login if not authenticated."""
        # '/' is the public landing page now (v4); the protected
        # dashboard lives at /dashboard.
        res_home = self.client.get('/')
        self.assertEqual(res_home.status_code, 200)
        self.assertIn(b'I am a Student', res_home.data)
        res_dash = self.client.get('/dashboard')
        self.assertEqual(res_dash.status_code, 302)
        self.assertIn('/auth/login', res_dash.headers['Location'])

        res_sched = self.client.get('/timetable/')
        self.assertEqual(res_sched.status_code, 302)

        res_cam = self.client.get('/cameras/')
        self.assertEqual(res_cam.status_code, 302)

    def test_teacher_register_and_login_flow(self):
        """Verify teacher registration, login, and dashboard access."""
        # Register a new teacher
        reg_data = {
            'name': 'Dr. Muhammad Ismail Mangrio',
            'email': 'ismail.mangrio@iba-suk.edu.pk',
            'department': 'Computer Science',
            'password': 'SuperSecretPassword2026',
            'confirm_password': 'SuperSecretPassword2026'
        }
        res_reg = self.client.post('/auth/register', data=reg_data, follow_redirects=True)
        self.assertEqual(res_reg.status_code, 200)

        # Verify teacher exists in DB
        teacher = Teacher.query.filter_by(email='ismail.mangrio@iba-suk.edu.pk').first()
        self.assertIsNotNone(teacher)
        self.assertEqual(teacher.name, 'Dr. Muhammad Ismail Mangrio')

        # Logout
        self.client.get('/auth/logout', follow_redirects=True)

        # Log back in
        login_data = {
            'email': 'ismail.mangrio@iba-suk.edu.pk',
            'password': 'SuperSecretPassword2026'
        }
        res_login = self.client.post('/auth/login', data=login_data, follow_redirects=True)
        self.assertEqual(res_login.status_code, 200)
        self.assertIn(b'Dr. Muhammad Ismail Mangrio', res_login.data)

    def test_timetable_add_and_holiday_toggle(self):
        """Verify timetable creation, holiday toggle, and deletion."""
        # Create teacher and authenticate
        teacher = Teacher(name="Dr. Zakriya", email="zakriya@iba-suk.edu.pk")
        teacher.set_password("pass123")
        db.session.add(teacher)
        db.session.commit()

        # Login
        self.client.post('/auth/login', data={'email': 'zakriya@iba-suk.edu.pk', 'password': 'pass123'})

        # Add a recurrent lecture
        sched_data = {
            'course_code': 'CS-302',
            'course_name': 'Machine Learning',
            'department': 'Computer Science',
            'semester': 'BS-V',
            'section': 'Section B',
            'day_of_week': 'Tuesday',
            'start_time': '09:00',
            'end_time': '10:40'
        }
        res_add = self.client.post('/timetable/add', data=sched_data, follow_redirects=True)
        self.assertEqual(res_add.status_code, 200)

        sched = Schedule.query.filter_by(course_code='CS-302').first()
        self.assertIsNotNone(sched)
        self.assertEqual(sched.duration_minutes(), 100)
        self.assertFalse(sched.is_paused)

        # Toggle pause (Holiday)
        self.client.post(f'/timetable/toggle-pause/{sched.id}', follow_redirects=True)
        sched_refreshed = Schedule.query.get(sched.id)
        self.assertTrue(sched_refreshed.is_paused)

        # Toggle unpause (Active)
        self.client.post(f'/timetable/toggle-pause/{sched.id}', follow_redirects=True)
        sched_refreshed2 = Schedule.query.get(sched.id)
        self.assertFalse(sched_refreshed2.is_paused)

        # Delete schedule
        self.client.post(f'/timetable/delete/{sched.id}', follow_redirects=True)
        self.assertIsNone(Schedule.query.get(sched.id))

    def test_camera_management_and_active_switching(self):
        """Verify multi-camera registration (USB & Mobile IP Webcam) and primary switching."""
        teacher = Teacher(name="Dr. Saif Hassan", email="saif@iba-suk.edu.pk")
        teacher.set_password("pass123")
        db.session.add(teacher)
        db.session.commit()

        self.client.post('/auth/login', data={'email': 'saif@iba-suk.edu.pk', 'password': 'pass123'})

        # 1. Add Browser/USB Camera
        self.client.post('/cameras/add', data={
            'name': 'Laptop Built-in Webcam',
            'camera_type': 'browser',
            'stream_url': ''
        }, follow_redirects=True)

        cam1 = CameraSource.query.filter_by(name='Laptop Built-in Webcam').first()
        self.assertIsNotNone(cam1)
        self.assertTrue(cam1.is_active)  # First camera is active by default

        # 2. Add Mobile Phone IP Webcam
        self.client.post('/cameras/add', data={
            'name': 'Android Mobile (Back Wall)',
            'camera_type': 'ip_webcam',
            'stream_url': '192.168.1.55:8080'  # Test URL auto-formatting
        }, follow_redirects=True)

        cam2 = CameraSource.query.filter_by(name='Android Mobile (Back Wall)').first()
        self.assertIsNotNone(cam2)
        self.assertEqual(cam2.stream_url, 'http://192.168.1.55:8080/video')
        self.assertFalse(cam2.is_active)

        # 3. Switch active camera to mobile IP Webcam
        self.client.post(f'/cameras/set-active/{cam2.id}', follow_redirects=True)
        
        # Verify cam2 is now active and cam1 is inactive
        cam1_check = CameraSource.query.get(cam1.id)
        cam2_check = CameraSource.query.get(cam2.id)
        self.assertFalse(cam1_check.is_active)
        self.assertTrue(cam2_check.is_active)


if __name__ == '__main__':
    unittest.main()
