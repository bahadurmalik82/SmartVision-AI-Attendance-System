import unittest
from datetime import datetime, timedelta, time, date
import numpy as np
from PIL import Image
import io
import base64
from app import create_app, db
from app.models import Teacher, Student, Schedule, AttendanceSession, AttendanceRecord
from app.ml_engine.duration_tracker import DurationTracker
from app.ml_engine.liveness_detector import LivenessDetector
from app.services.scheduler_service import AutomatedSchedulerService


class Phase5TestCase(unittest.TestCase):
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

    def test_duration_tracker_10_min_sampling_buffer(self):
        """Verify that a single detection in a 10-minute block credits the entire block."""
        tracker = DurationTracker(block_duration_minutes=10, late_threshold_minutes=15)
        start_time = datetime(2026, 11, 20, 9, 0, 0)
        tracker.start_session(start_time=start_time)

        # Detect Ali at 9:02 AM (Block 0)
        tracker.record_detection(student_id="Ali", student_name="Ali", timestamp=start_time + timedelta(minutes=2))
        # Detect Ali at 9:15 AM (Block 1) - Note: Ali looked down at notebook between 9:02 and 9:15!
        tracker.record_detection(student_id="Ali", student_name="Ali", timestamp=start_time + timedelta(minutes=15))
        # Detect Ali at 9:24 AM (Block 2)
        tracker.record_detection(student_id="Ali", student_name="Ali", timestamp=start_time + timedelta(minutes=24))

        # Check summary for a 30-minute test lecture
        summary = tracker.compute_student_summary(student_id="Ali", total_class_minutes=30.0)

        # 3 distinct blocks attended * 10 mins each = 30 mins out of 30 mins (100% presence)
        self.assertEqual(summary['minutes_present'], 30.0)
        self.assertEqual(summary['percentage'], 100.0)
        self.assertEqual(summary['status'], 'Present')

    def test_duration_tracker_punctuality_and_absence(self):
        """Verify Late arrival (>15 mins) and Absent (<50%) status classifications."""
        tracker = DurationTracker(block_duration_minutes=10, late_threshold_minutes=15)
        start_time = datetime(2026, 11, 20, 9, 0, 0)
        tracker.start_session(start_time=start_time)

        # Student LateComer arrives at 9:20 AM (20 mins late)
        t_late = start_time + timedelta(minutes=20)
        tracker.record_detection("LateComer", "LateComer", timestamp=t_late)
        tracker.record_detection("LateComer", "LateComer", timestamp=start_time + timedelta(minutes=35))
        tracker.record_detection("LateComer", "LateComer", timestamp=start_time + timedelta(minutes=45))
        tracker.record_detection("LateComer", "LateComer", timestamp=start_time + timedelta(minutes=55))

        summary_late = tracker.compute_student_summary("LateComer", total_class_minutes=60.0)
        # Present for 40 mins out of 60 mins (66.7%), but arrived > 15 mins late -> 'Late'
        self.assertEqual(summary_late['status'], 'Late')

        # Student Skipper is only detected once for 10 minutes in a 100-minute lecture (10% -> Absent)
        tracker.record_detection("Skipper", "Skipper", timestamp=start_time + timedelta(minutes=1))
        summary_skip = tracker.compute_student_summary("Skipper", total_class_minutes=100.0)
        self.assertEqual(summary_skip['percentage'], 10.0)
        self.assertEqual(summary_skip['status'], 'Absent')

    def test_liveness_detector_anti_spoofing(self):
        """Verify liveness detector flags static photos (zero motion variance)."""
        detector = LivenessDetector()

        # Frame 1
        frame1 = np.ones((128, 128), dtype=np.uint8) * 120
        is_live1, score1, msg1 = detector.check_liveness(frame1, subject_id='person_1')
        self.assertTrue(is_live1)

        # Frame 2: Identical static image held still (simulating a paper photo)
        frame2 = frame1.copy()
        is_live2, score2, msg2 = detector.check_liveness(frame2, subject_id='person_1')
        self.assertFalse(is_live2)
        self.assertIn("Static Spoof", msg2)

        # Frame 3: Natural micro-motion / slight shift
        frame3 = frame1.copy()
        frame3[::2, ::2] = 135
        is_live3, score3, msg3 = detector.check_liveness(frame3, subject_id='person_1')
        self.assertTrue(is_live3)

    def test_manual_session_start_and_stop_override(self):
        """Verify teacher manual 'Start Class Now' and 'Stop Class Now' workflow."""
        teacher = Teacher(name="Prof. Zakriya", email="zakriya.cs@iba-suk.edu.pk")
        teacher.set_password("pass123")
        student = Student(roll_number="K21-0001", name="Fahad Ali")
        db.session.add_all([teacher, student])
        db.session.commit()

        self.client.post('/auth/login', data={'email': 'zakriya.cs@iba-suk.edu.pk', 'password': 'pass123'})

        # 1. Manual Start Class
        res_start = self.client.post('/start-class-manual', data={
            'course_name': 'CS-302 Machine Learning',
            'section': 'Section B',
            'duration': 100
        }, follow_redirects=True)
        self.assertEqual(res_start.status_code, 200)

        active = AttendanceSession.query.filter_by(is_active=True).first()
        self.assertIsNotNone(active)
        self.assertEqual(active.course_name, 'CS-302 Machine Learning')

        # 2. Simulate detection
        tracker = AutomatedSchedulerService.get_duration_tracker()
        tracker.record_detection("Fahad Ali", "Fahad Ali")

        # 3. Manual Stop Class
        res_stop = self.client.post('/stop-class-manual', follow_redirects=True)
        self.assertEqual(res_stop.status_code, 200)

        # Active session should now be closed
        active_check = AttendanceSession.query.filter_by(is_active=True).first()
        self.assertIsNone(active_check)

        # Verify AttendanceRecord was created in DB for the enrolled student
        rec = AttendanceRecord.query.filter_by(session_id=active.id, student_id=student.id).first()
        self.assertIsNotNone(rec)


if __name__ == '__main__':
    unittest.main()
