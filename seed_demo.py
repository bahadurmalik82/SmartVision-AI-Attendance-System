"""Seed demo data (Phase 8, v4).

Creates, WITHOUT a camera:
  - Teacher: demo@iba-suk.edu.pk / Demo1234 (an ordinary teacher
    account — there is no admin. Delete it before showing the project
    to outsiders, or just change its password.)
  - 6 students with varied attendance (96, 88, 82, 72, 64, 45%),
    each with a CMS ID (CMS-0001, ...) and a random portal password
    (printed at the end of this script and shown on the Students page)
  - 1 schedule: Machine Learning CS-302, today, 09:00-10:40
  - 5 sessions (last 5 days) + attendance records for every student,
    so Analytics, Defaulters, Clusters and the Student Portal all show
    real data straight away.

Idempotent: safe to run twice — existing teacher / students /
sessions are detected by email / roll number / date and skipped.

Usage (from the project root, in your venv):
    python seed_demo.py
"""

import os
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db  # noqa: E402
from app.models import (AttendanceRecord, AttendanceSession, Schedule,  # noqa: E402
                        Student, Teacher, generate_student_password,
                        next_cms_id)

DEMO_EMAIL = "demo@iba-suk.edu.pk"
DEMO_PASSWORD = "Demo1234"

# (roll_number, name, email, target %, avg lateness in minutes)
STUDENTS = [
    ("K21-0145", "Ahmed Ali", "ahmed.ali@iba-suk.edu.pk", 96, 2),
    ("K21-0231", "Sara Ahmed", "sara.ahmed@iba-suk.edu.pk", 88, 6),
    ("K21-0456", "Zubair Khan", "zubair.khan@iba-suk.edu.pk", 82, 10),
    ("K21-0789", "Bilal Hussain", "bilal.hussain@iba-suk.edu.pk", 72, 18),
    ("K21-0901", "Fatima Noor", "fatima.noor@iba-suk.edu.pk", 64, 24),
    ("K21-1123", "Usman Tariq", "usman.tariq@iba-suk.edu.pk", 45, 30),
]


def main():
    app = create_app('development')
    with app.app_context():
        db.create_all()

        teacher = Teacher.query.filter_by(email=DEMO_EMAIL).first()
        if teacher:
            print(f"Teacher already exists: {DEMO_EMAIL} (keeping existing password)")
        else:
            teacher = Teacher(name="Demo Teacher", email=DEMO_EMAIL,
                              department="Computer Science",
                              is_active=True)
            teacher.set_password(DEMO_PASSWORD)
            db.session.add(teacher)
            db.session.commit()
            print(f"Created teacher: {DEMO_EMAIL} / {DEMO_PASSWORD}")

        students = {}
        for roll, name, email, _pct, _late in STUDENTS:
            student = Student.query.filter_by(roll_number=roll).first()
            if not student:
                password = generate_student_password()
                student = Student(roll_number=roll, name=name, email=email,
                                  department="Computer Science",
                                  semester="5th", section="Section B",
                                  teacher_id=teacher.id,
                                  cms_id=next_cms_id())
                student.set_portal_password(password)
                db.session.add(student)
                db.session.commit()
                print(f"Created student: {roll} — {name} "
                      f"(CMS ID {student.cms_id} / password {password})")
            if student.teacher_id is None:
                student.teacher_id = teacher.id
                db.session.commit()
            if not student.cms_id or not student.portal_password_hash:
                # Backfill runs on startup anyway; this just makes the
                # printed credentials below correct on a second run.
                db.session.refresh(student)
            students[roll] = student

        today = date.today()
        weekday = today.strftime('%A')
        schedule = Schedule.query.filter_by(
            teacher_id=teacher.id, course_code="CS-302").first()
        if not schedule:
            schedule = Schedule(
                teacher_id=teacher.id, course_name="Machine Learning",
                course_code="CS-302", department="Computer Science",
                semester="5th", section="Section B", day_of_week=weekday,
                start_time=time(9, 0), end_time=time(10, 40))
            db.session.add(schedule)
            db.session.commit()
            print("Created schedule: Machine Learning CS-302, "
                  f"{weekday} 09:00-10:40")
        else:
            print("Schedule already exists: Machine Learning CS-302")

        # 5 sessions over the last 5 days, 100 minutes each.
        sessions_made = 0
        for day_offset in range(4, -1, -1):
            session_date = today - timedelta(days=day_offset)
            session = AttendanceSession.query.filter_by(
                schedule_id=schedule.id, session_date=session_date).first()
            if session:
                continue
            start = datetime.combine(session_date, time(9, 0))
            session = AttendanceSession(
                schedule_id=schedule.id, course_name="Machine Learning",
                section="Section B", session_date=session_date,
                start_time=start, end_time=start + timedelta(minutes=100),
                is_active=False, total_duration_minutes=100)
            db.session.add(session)
            db.session.commit()
            sessions_made += 1

            for roll, _name, _email, target_pct, late_mins in STUDENTS:
                student = students[roll]
                # Vary each session a little around the target so the
                # average still lands on the target percentage.
                jitter = ((day_offset % 3) - 1) * 2  # -2, 0, +2
                pct = max(0.0, min(100.0, target_pct + jitter))
                minutes_present = pct  # out of a 100-minute class
                first_seen = start + timedelta(minutes=late_mins)
                last_seen = start + timedelta(minutes=int(minutes_present))
                record = AttendanceRecord(
                    session_id=session.id, student_id=student.id,
                    first_seen=first_seen, last_seen=last_seen,
                    minutes_present=minutes_present)
                record.calculate_status(100)
                # calculate_status() recomputes percentage from minutes;
                # keep the exact target percentage for a clean demo.
                record.attendance_percentage = round(pct, 1)
                db.session.add(record)
            db.session.commit()

        print(f"Created {sessions_made} new session(s) "
              f"(5 sessions total expected).")
        print("\nDemo data ready:")
        print("  Teacher login: demo@iba-suk.edu.pk / Demo1234")
        print("  Student logins (CMS ID / password — also on the Students page):")
        for roll, _name, _email, _pct, _late in STUDENTS:
            st = students[roll]
            print(f"    {st.cms_id} / {st.portal_password_plain} "
                  f"— {st.name} ({roll})")
        print("  Try: Ahmed Ali (CMS-0001, ~96%) and Usman Tariq "
              "(CMS-0006, ~45% — defaulter)")
        print("  Run: python run.py  ->  http://127.0.0.1:5000 "
              "(landing page, then I am a Student / I am a Teacher)")

        # Stop the background scheduler started by create_app() so the
        # script exits cleanly.
        try:
            from app.services.scheduler_service import AutomatedSchedulerService
            AutomatedSchedulerService.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
