from datetime import datetime, time, date
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import or_
from app import db
from app.models import Schedule, AttendanceSession, AttendanceRecord, Student
from app.ml_engine.duration_tracker import DurationTracker


class AutomatedSchedulerService:
    """
    Automated Timetable & Attendance Daemon (APScheduler).
    Monitors weekly schedules and handles class start, duration tracking, and session closure.

    v5 fixes:
      * ``_check_timetable`` now takes an optional ``now`` (so Sunday
        and any other day can be tested), ends manual sessions too
        (previously only scheduled sessions ever ended — a manual
        class ran forever), and never starts a class twice in a day.
      * ``finalize_session`` only creates attendance records for the
        session's OWN class (``class_group_id`` / schedule class).
        Before v5 it created a record for EVERY student the teacher
        owned, so a Section B class marked Section C students absent.
      * The dashboard polls for a newly started session, so the camera
        can auto-start when class time arrives (see dashboard.html).
    """

    _scheduler = None
    _duration_tracker = DurationTracker()

    @classmethod
    def get_duration_tracker(cls):
        return cls._duration_tracker

    @classmethod
    def init_scheduler(cls, app):
        """Initializes APScheduler background daemon within Flask app context."""
        if cls._scheduler is not None and cls._scheduler.running:
            return cls._scheduler

        cls._scheduler = BackgroundScheduler(daemon=True)
        # Check timetable every 30 seconds
        cls._scheduler.add_job(
            func=cls._check_timetable_job,
            args=[app],
            trigger="interval",
            seconds=30,
            id="timetable_checker",
            replace_existing=True
        )
        cls._scheduler.start()
        return cls._scheduler

    @classmethod
    def shutdown(cls):
        if cls._scheduler and cls._scheduler.running:
            cls._scheduler.shutdown(wait=False)
        cls._scheduler = None

    @classmethod
    def _check_timetable_job(cls, app):
        """Periodic background job that checks if a class needs to start or end."""
        try:
            cls._check_timetable(app)
        except Exception as exc:
            # Background job must never crash the app (e.g. empty DB,
            # in-memory test DB from another thread, DB not ready yet).
            try:
                app.logger.warning(f"Scheduler tick skipped: {exc}")
            except Exception:
                pass

    @classmethod
    def _check_timetable(cls, app, now=None):
        """Start classes whose time has arrived, end classes whose time
        has passed. ``now`` is only for tests (a fixed datetime)."""
        with app.app_context():
            now = now or datetime.now()
            current_day = now.strftime('%A')  # 'Monday' ... 'Sunday'
            current_time = now.time()
            today = now.date()

            # 1. Check for classes that should end
            active_sessions = AttendanceSession.query.filter_by(is_active=True).all()
            for session in active_sessions:
                if session.schedule:
                    # If current time has passed end_time
                    if current_time >= session.schedule.end_time:
                        cls.finalize_session(session)
                elif session.start_time and session.total_duration_minutes:
                    # Manual session: end it once its duration has passed.
                    elapsed = (now - session.start_time).total_seconds() / 60.0
                    if elapsed >= session.total_duration_minutes:
                        cls.finalize_session(session)

            # 2. Check for classes that should start (any day, incl. Sunday)
            schedules = Schedule.query.filter_by(day_of_week=current_day, is_paused=False).all()
            for sched in schedules:
                # If current time is between start_time and end_time
                if sched.start_time <= current_time < sched.end_time:
                    # Check if session was already created today
                    existing = AttendanceSession.query.filter_by(
                        schedule_id=sched.id,
                        session_date=today
                    ).first()
                    if not existing:
                        cls.start_scheduled_session(sched, now=now)

    @classmethod
    def start_scheduled_session(cls, schedule, now=None):
        """Activates a scheduled class session and begins duration monitoring."""
        now = now or datetime.now()
        session = AttendanceSession(
            schedule_id=schedule.id,
            teacher_id=schedule.teacher_id,
            class_group_id=schedule.class_group_id,
            course_name=schedule.course_name,
            section=schedule.section,
            session_date=now.date(),
            start_time=now,
            is_active=True,
            total_duration_minutes=schedule.duration_minutes()
        )
        db.session.add(session)
        db.session.commit()

        cls._duration_tracker.start_session(start_time=now)
        return session

    @classmethod
    def start_manual_session(cls, course_name="General Lecture", section="Section B", duration_minutes=60, teacher_id=None, class_group_id=None):
        """Manual 'Start Class Now' override triggered by the teacher from dashboard.

        When the teacher picks a class, the session is tied to that
        class group, so only that class's students get records.
        """
        # Close any existing active sessions of THIS teacher first.
        active = AttendanceSession.query.filter_by(is_active=True).all()
        for s in active:
            owner = s.teacher_id or (s.schedule.teacher_id if s.schedule else None)
            if teacher_id is None or owner is None or owner == teacher_id:
                cls.finalize_session(s)

        # If a class was picked, use its section / course name.
        if class_group_id:
            from app.models import ClassGroup
            group = ClassGroup.query.get(class_group_id)
            if group is not None and (teacher_id is None or group.teacher_id == teacher_id):
                section = group.section
                if group.course_name:
                    course_name = group.course_name

        session = AttendanceSession(
            schedule_id=None,
            teacher_id=teacher_id,
            class_group_id=class_group_id,
            course_name=course_name,
            section=section,
            session_date=date.today(),
            start_time=datetime.now(),
            is_active=True,
            total_duration_minutes=duration_minutes
        )
        db.session.add(session)
        db.session.commit()

        cls._duration_tracker.start_session(start_time=datetime.now())
        return session

    @classmethod
    def _session_students(cls, session):
        """Students whose attendance this session should record.

        v5 rule: ONLY the session's own class. A Section B class must
        never create records for Section C students, and a Semester 5
        class must never mark Semester 3 students absent.
        Legacy rows (no class group yet) still match by their stored
        department / semester / section, so old data keeps working.
        """
        owner_id = session.teacher_id or (
            session.schedule.teacher_id if session.schedule else None)
        if owner_id is not None:
            base = Student.query.filter(
                (Student.teacher_id == owner_id)
                | (Student.teacher_id.is_(None)))
        else:
            base = Student.query

        # 1. The session knows its class group — the exact case.
        if getattr(session, 'class_group_id', None):
            return base.filter(
                Student.class_group_id == session.class_group_id).all()
        # 2. Scheduled session without a class id yet (legacy): match
        #    the schedule's department / semester / section.
        if session.schedule:
            sched = session.schedule
            return base.filter(
                Student.department == sched.department,
                Student.semester == sched.semester,
                Student.section == sched.section).all()
        # 3. Manual / legacy session: match by section only. Students
        #    keep their section in the Student row, so this still works
        #    for old sessions and for teachers with one class.
        if session.section:
            return base.filter(Student.section == session.section).all()
        return base.all()

    @classmethod
    def finalize_session(cls, session):
        """Closes an active session, evaluates all student durations, and saves final logs."""
        now = datetime.now()
        session.is_active = False
        session.end_time = now

        # Compute elapsed class minutes
        if session.start_time:
            delta = now - session.start_time
            actual_duration = max(1, int(delta.total_seconds() / 60))
        else:
            actual_duration = 1
        # Never finalize with 0/None total minutes: the duration tracker
        # would divide by zero for any student who was actually detected.
        if not session.total_duration_minutes or session.total_duration_minutes <= 0:
            session.total_duration_minutes = actual_duration

        # Attendance records for THIS session's class only (v5 fix).
        all_students = cls._session_students(session)
        for student in all_students:
            summary = cls._duration_tracker.compute_student_summary(
                student_id=student.name,
                class_end_time=now,
                total_class_minutes=session.total_duration_minutes
            )

            rec = AttendanceRecord(
                session_id=session.id,
                student_id=student.id,
                first_seen=summary['first_seen'],
                last_seen=summary['last_seen'],
                minutes_present=summary['minutes_present'],
                attendance_percentage=summary['percentage'],
                status=summary['status']
            )
            db.session.add(rec)

        db.session.commit()
        cls._duration_tracker.start_session(datetime.now())  # Reset
        return session
