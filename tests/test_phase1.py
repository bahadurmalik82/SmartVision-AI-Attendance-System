import unittest
from datetime import datetime, time, date
from app import create_app, db
from app.models import Teacher, CameraSource, Schedule, Student, AttendanceSession, AttendanceRecord


class Phase1TestCase(unittest.TestCase):
    def setUp(self):
        # Create app in testing mode with an in-memory SQLite database
        self.app = create_app('development')
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app.config['TESTING'] = True
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_database_tables_exist(self):
        """Verify all Phase 1 database tables are created."""
        table_names = db.metadata.tables.keys()
        expected = [
            'teachers',
            'camera_sources',
            'schedules',
            'students',
            'attendance_sessions',
            'attendance_records'
        ]
        for tbl in expected:
            self.assertIn(tbl, table_names, f"Table '{tbl}' is missing from database schema.")

    def test_teacher_password_hashing(self):
        """Verify teacher creation and Bcrypt password hashing."""
        teacher = Teacher(
            name="Dr. Muhammad Ismail",
            email="ismail@iba-suk.edu.pk",
            department="Computer Science"
        )
        teacher.set_password("SecurePassword123")
        db.session.add(teacher)
        db.session.commit()

        self.assertIsNotNone(teacher.id)
        self.assertNotEqual(teacher.password_hash, "SecurePassword123")
        self.assertTrue(teacher.check_password("SecurePassword123"))
        self.assertFalse(teacher.check_password("WrongPassword"))

    def test_camera_sources(self):
        """Verify registering USB and Mobile IP Webcam camera sources."""
        teacher = Teacher(name="Prof. Zakriya", email="zakriya@iba-suk.edu.pk")
        teacher.set_password("pass123")
        db.session.add(teacher)
        db.session.commit()

        cam1 = CameraSource(teacher_id=teacher.id, name="Laptop Webcam", camera_type="browser")
        cam2 = CameraSource(
            teacher_id=teacher.id,
            name="Mobile Phone (IP Webcam)",
            camera_type="ip_webcam",
            stream_url="http://192.168.1.15:8080/video"
        )
        db.session.add_all([cam1, cam2])
        db.session.commit()

        self.assertEqual(len(teacher.cameras), 2)
        self.assertEqual(cam2.stream_url, "http://192.168.1.15:8080/video")

    def test_weekly_schedule_duration(self):
        """Verify weekly timetable creation and duration calculation."""
        teacher = Teacher(name="Dr. Adil Khan", email="adil@iba-suk.edu.pk")
        teacher.set_password("pass123")
        db.session.add(teacher)
        db.session.commit()

        sched = Schedule(
            teacher_id=teacher.id,
            course_name="Machine Learning",
            course_code="CS-302",
            section="Section B",
            day_of_week="Tuesday",
            start_time=time(9, 0),
            end_time=time(10, 40)
        )
        db.session.add(sched)
        db.session.commit()

        # 9:00 AM to 10:40 AM is exactly 100 minutes
        self.assertEqual(sched.duration_minutes(), 100)
        self.assertFalse(sched.is_paused)

    def test_student_and_face_embedding_serialization(self):
        """Verify student enrollment and JSON embedding serialization."""
        student = Student(
            roll_number="K21-0145",
            name="Ali Raza",
            department="Computer Science",
            section="Section B"
        )
        # Dummy 128-D embedding vector
        dummy_vector = [0.1234] * 128
        student.set_face_embedding(dummy_vector)
        db.session.add(student)
        db.session.commit()

        loaded_student = Student.query.filter_by(roll_number="K21-0145").first()
        self.assertIsNotNone(loaded_student)
        retrieved_vector = loaded_student.get_face_embedding()
        self.assertEqual(len(retrieved_vector), 128)
        self.assertAlmostEqual(retrieved_vector[0], 0.1234)

    def test_attendance_status_calculation(self):
        """Verify university attendance criteria calculation."""
        teacher = Teacher(name="Dr. Saif Hassan", email="saif@iba-suk.edu.pk")
        teacher.set_password("pass123")
        db.session.add(teacher)
        db.session.commit()

        session = AttendanceSession(
            course_name="Machine Learning",
            section="Section B",
            session_date=date.today(),
            total_duration_minutes=100
        )
        student = Student(roll_number="K21-0999", name="Sara Ahmed")
        db.session.add_all([session, student])
        db.session.commit()

        # Case 1: Attended 85 mins out of 100 mins (85% -> Present)
        rec1 = AttendanceRecord(session_id=session.id, student_id=student.id, minutes_present=85.0)
        rec1.calculate_status(session.total_duration_minutes)
        self.assertEqual(rec1.attendance_percentage, 85.0)
        self.assertEqual(rec1.status, "Present")

        # Case 2: Attended 60 mins out of 100 mins (60% -> Left Early)
        rec2 = AttendanceRecord(session_id=session.id, student_id=student.id, minutes_present=60.0)
        rec2.calculate_status(session.total_duration_minutes)
        self.assertEqual(rec2.attendance_percentage, 60.0)
        self.assertEqual(rec2.status, "Left Early")

        # Case 3: Attended 20 mins out of 100 mins (20% -> Absent)
        rec3 = AttendanceRecord(session_id=session.id, student_id=student.id, minutes_present=20.0)
        rec3.calculate_status(session.total_duration_minutes)
        self.assertEqual(rec3.attendance_percentage, 20.0)
        self.assertEqual(rec3.status, "Absent")


if __name__ == '__main__':
    unittest.main()
