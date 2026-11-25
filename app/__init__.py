import logging
import os
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, render_template
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

from app.config import config_by_name

# Initialize Flask extensions
db = SQLAlchemy()
bcrypt = Bcrypt()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'warning'

# Columns added in v3 (column name -> SQL type). Applied with
# ALTER TABLE so an existing attendance.db keeps working.
# NOTE (v4): 'teachers.is_admin' is NOT listed here any more — the
# column is unused since v4 (no admin/HOD) and old databases already
# have it. Nothing reads or writes it.
_V3_COLUMNS = {
    'teachers': [('is_active', 'BOOLEAN')],
    'students': [('teacher_id', 'INTEGER'), ('pin_hash', 'VARCHAR(256)')],
    'attendance_sessions': [('teacher_id', 'INTEGER')],
}

# Columns added in v4 (student portal login: CMS ID + password).
# Migration plan (see also INSTALL-V4.txt):
#   students.cms_id                 VARCHAR(50)  UNIQUE — CMS-0001, ...
#   students.portal_password_hash   VARCHAR(256) — bcrypt hash (login)
#   students.portal_password_plain  VARCHAR(50)  — plain password, shown
#     ONLY to the owning teacher (user requirement: a student who
#     forgets their password asks the teacher). Never logged.
# Backfill decision for existing rows (same change, runs on startup):
#   cms_id NULL  -> next free CMS-xxxx in student.id order.
#   password NULL -> a fresh random 8-char password (hash + plain copy).
# After the backfill both NULL counts must be zero (checked + logged).
_V4_COLUMNS = {
    'students': [('cms_id', 'VARCHAR(50)'),
                 ('portal_password_hash', 'VARCHAR(256)'),
                 ('portal_password_plain', 'VARCHAR(50)')],
}

# Columns added in v5 (classes: one class group per department /
# semester / section, so a class session only marks its own students).
#   students.class_group_id   INTEGER (FK class_groups.id)
#   schedules.class_group_id  INTEGER (FK class_groups.id)
# The class_groups table itself is created by db.create_all() (new
# model, no ALTER needed). Backfill decision for existing rows:
#   group students by (teacher_id, department, semester, section),
#   create one ClassGroup per group, assign every student in it;
#   link each schedule to the group with the same fields.
# After the backfill the NULL counts must be zero for students /
# schedules that have a teacher (checked + logged).
_V5_COLUMNS = {
    'students': [('class_group_id', 'INTEGER')],
    'schedules': [('class_group_id', 'INTEGER')],
    'attendance_sessions': [('class_group_id', 'INTEGER')],
}


# Columns added in v9.3 (Personal Details on the Settings page).
#   teachers.qualification  VARCHAR(150) — e.g. "M.Phil Computer Science".
# The Feedback table is a new model, created by db.create_all().
_V9_COLUMNS = {
    'teachers': [('qualification', 'VARCHAR(150)')],
}


def _migrate_and_backfill(app):
    """Add v3 columns to an older database and fill sensible defaults.

    Safe to run on every startup: columns that already exist are skipped.
    Works on SQLite (PRAGMA table_info) and, best-effort, on MySQL.
    """
    from app.models import (AttendanceSession, Student, Teacher,
                            generate_student_password)

    # BUGFIX (v5): the old code merged the three dicts with
    # {**_V3_COLUMNS, **_V4_COLUMNS, **_V5_COLUMNS} — but all three
    # have a 'students' key, so the merge OVERWROTE the v3/v4 student
    # columns with the v5 list. On a pre-v3 database cms_id was then
    # never added and the first ORM Student query crashed with
    # "no such column: students.cms_id". Combine the column lists
    # per table instead, keeping v3 -> v4 -> v5 order.
    _all_columns = {}
    for _colset in (_V3_COLUMNS, _V4_COLUMNS, _V5_COLUMNS, _V9_COLUMNS):
        for _table, _cols in _colset.items():
            _all_columns.setdefault(_table, []).extend(_cols)
    engine = db.engine
    dialect = engine.dialect.name
    with engine.begin() as conn:
        for table, columns in _all_columns.items():
            if dialect == 'sqlite':
                existing = {row[1] for row in
                            conn.exec_driver_sql(f'PRAGMA table_info({table})')}
            else:
                existing = {row[0] for row in conn.exec_driver_sql(
                    f'SHOW COLUMNS FROM {table}')}
            for col_name, col_type in columns:
                if col_name not in existing:
                    try:
                        conn.exec_driver_sql(
                            f'ALTER TABLE {table} ADD COLUMN {col_name} {col_type}')
                        app.logger.info(f'Migration: added {table}.{col_name}')
                    except Exception as exc:  # never stop the app starting
                        app.logger.warning(
                            f'Migration skipped for {table}.{col_name}: {exc}')

    # Backfill: students / sessions created before v3 belong to the
    # first teacher. (v4: there is no admin any more — no teacher is
    # ever made an admin, and nobody is ever disabled.)
    first_teacher = Teacher.query.order_by(Teacher.id).first()
    if first_teacher is not None:
        # BUGFIX (v3.1, kept): ALTER TABLE ... ADD COLUMN has no
        # DEFAULT, so teachers created before v3 got is_active = NULL.
        # NULL is falsy, so login rejected every pre-v3 teacher with
        # "Invalid email or password" even with the right password.
        # Fix NULLs on every startup (idempotent, harmless on new DBs).
        changed = False
        for _t in Teacher.query.filter(Teacher.is_active.is_(None)).all():
            _t.is_active = True
            changed = True
        if changed:
            db.session.commit()
            app.logger.info(
                'Backfill: fixed NULL is_active on legacy teachers.')
        changed = False
        for student in Student.query.filter(
                Student.teacher_id.is_(None)).all():
            student.teacher_id = first_teacher.id
            changed = True
        for session in AttendanceSession.query.filter(
                AttendanceSession.teacher_id.is_(None)).all():
            if session.schedule is not None:
                session.teacher_id = session.schedule.teacher_id
            else:
                session.teacher_id = first_teacher.id
            changed = True
        if changed:
            db.session.commit()
            app.logger.info('Backfill: assigned legacy students/sessions '
                            'to the first teacher.')

    # ---- v4 backfill: CMS ID + portal password for every student ----
    # Runs BEFORE any code reads the new columns (this function runs
    # inside create_app, before the first request). Idempotent: a
    # student who already has a CMS ID / password is never touched,
    # so running it twice is safe.
    import re as _re
    _highest = 0
    for (_cid,) in db.session.query(Student.cms_id).all():
        _m = _re.fullmatch(r'CMS-(\d+)', _cid or '')
        if _m:
            _highest = max(_highest, int(_m.group(1)))
    _changed = False
    for student in Student.query.order_by(Student.id).all():
        if not student.cms_id:
            _highest += 1
            student.cms_id = f'CMS-{_highest:04d}'
            _changed = True
        if not student.portal_password_hash:
            student.set_portal_password(generate_student_password())
            _changed = True
    if _changed:
        db.session.commit()
    # Verify: no required value may stay NULL after the backfill.
    _null_cms = Student.query.filter(Student.cms_id.is_(None)).count()
    _null_pw = Student.query.filter(
        Student.portal_password_hash.is_(None)).count()
    app.logger.info(f'v4 backfill: students without CMS ID: {_null_cms}; '
                    f'students without portal password: {_null_pw}.')
    if _null_cms or _null_pw:
        app.logger.warning('v4 backfill INCOMPLETE — some students still '
                           'have no CMS ID or password!')

    # ---- v5 backfill: class groups for every existing student/schedule ----
    # Runs AFTER the v3 teacher backfill above, so every student has a
    # teacher by now. Idempotent: a student/schedule that already has a
    # class_group_id is never touched.
    try:
        from app.models import ClassGroup, Schedule, get_or_create_class_group
        _groups = {}
        _v5_changed = False
        for student in Student.query.filter(
                Student.class_group_id.is_(None)).all():
            if not student.teacher_id:
                continue
            key = (student.teacher_id, student.department or 'Computer Science',
                   student.semester or '5th', student.section or 'Section B')
            group = _groups.get(key)
            if group is None:
                group = get_or_create_class_group(
                    student.teacher_id, key[1], key[2], key[3])
                _groups[key] = group
            student.class_group_id = group.id
            _v5_changed = True
        for sched in Schedule.query.filter(
                Schedule.class_group_id.is_(None)).all():
            if not sched.teacher_id:
                continue
            key = (sched.teacher_id, sched.department or 'Computer Science',
                   sched.semester or '5th', sched.section or 'Section B')
            group = _groups.get(key)
            if group is None:
                group = get_or_create_class_group(
                    sched.teacher_id, key[1], key[2], key[3],
                    course_name=sched.course_name,
                    course_code=sched.course_code)
                _groups[key] = group
            sched.class_group_id = group.id
            _v5_changed = True
        if _v5_changed:
            db.session.commit()
        # Sessions: link to their schedule's class group.
        for sess in AttendanceSession.query.filter(
                AttendanceSession.class_group_id.is_(None)).all():
            if sess.schedule is not None and sess.schedule.class_group_id:
                sess.class_group_id = sess.schedule.class_group_id
                _v5_changed = True
        if _v5_changed:
            db.session.commit()
        _null_class = Student.query.filter(
            Student.class_group_id.is_(None)).count()
        _null_sched_class = Schedule.query.filter(
            Schedule.class_group_id.is_(None)).count()
        app.logger.info(f'v5 backfill: students without a class: '
                        f'{_null_class}; schedules without a class: '
                        f'{_null_sched_class}.')
        if _null_class or _null_sched_class:
            app.logger.warning('v5 backfill INCOMPLETE — some students or '
                               'schedules still have no class group!')
    except Exception as exc:
        app.logger.warning(f'v5 class backfill skipped: {exc}')

    # Seed the Settings defaults (75% threshold, 15-min late rule).
    try:
        from app.services.settings import seed_defaults
        seed_defaults()
    except Exception as exc:
        app.logger.warning(f'Settings seed skipped: {exc}')


def _close_stuck_sessions(app):
    """Close sessions left active by a crash/restart (older than 12h).

    Today's still-active sessions are never touched — a class that is
    genuinely running right now stays running.
    """
    from app.models import AttendanceSession

    cutoff = datetime.now() - timedelta(hours=12)
    stuck = AttendanceSession.query.filter(
        AttendanceSession.is_active.is_(True),
        AttendanceSession.start_time < cutoff).all()
    for session in stuck:
        session.is_active = False
        if session.start_time:
            minutes = session.total_duration_minutes or 0
            session.end_time = session.start_time + timedelta(minutes=minutes) \
                if minutes > 0 else datetime.now()
        else:
            session.end_time = datetime.now()
        app.logger.info(
            f'Closed stuck session #{session.id} '
            f'({session.course_name}, started {session.start_time}).')
    if stuck:
        db.session.commit()


def create_app(config_name='default'):
    """Application factory for Attenova (Smart Attendance System)."""
    # Production refuses to start without a real secret key.
    if config_name == 'production':
        from app.config import SECRET_KEY_IS_REAL
        if not SECRET_KEY_IS_REAL:
            raise RuntimeError(
                'SECRET_KEY is not set. Set a long random SECRET_KEY in '
                'your .env file (or environment) before running in '
                'production. Generate one with: '
                'python -c "import secrets; print(secrets.token_hex(32))"')

    app = Flask(__name__)
    app.config.from_object(config_by_name.get(config_name, config_by_name['default']))

    # ---- Logging: file (logs/attenova.log) + console, INFO level ----
    base_dir = Path(__file__).resolve().parent.parent
    log_dir = base_dir / 'logs'
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(log_dir / 'attenova.log',
                                  maxBytes=1_000_000, backupCount=3,
                                  encoding='utf-8')
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s'))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    from app.config import SECRET_KEY_IS_REAL
    if not SECRET_KEY_IS_REAL:
        app.logger.warning(
            'No real SECRET_KEY configured — generated a random '
            'per-process key. Set SECRET_KEY in .env for stable sessions.')

    # Initialize extensions with app
    db.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    # Ensure required data folders exist
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['EMBEDDINGS_FOLDER'], exist_ok=True)
    os.makedirs(app.config['SAVED_MODELS_FOLDER'], exist_ok=True)

    # Ensure database directory exists if using SQLite with a file path
    db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if db_uri.startswith('sqlite:///') and not db_uri.startswith('sqlite:///:memory:'):
        db_file = db_uri.replace('sqlite:///', '')
        db_dir = os.path.dirname(db_file)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    # ---- Security headers on every response ----
    @app.after_request
    def _security_headers(response):
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Tailwind + Font Awesome come from CDNs; the IP-webcam image
        # comes from the phone on the local network (http:).
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.tailwindcss.com; "
            "font-src 'self' https://cdnjs.cloudflare.com; "
            "img-src 'self' data: http: https:; "
            "connect-src 'self' http: https:; "
            "frame-ancestors 'self'")
        return response

    # ---- Friendly error pages (never a raw stack trace) ----
    @app.errorhandler(404)
    def _not_found(error):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def _server_error(error):
        db.session.rollback()
        app.logger.exception('Internal server error: %s', error)
        return render_template('500.html'), 500

    # Register blueprints (v4: no admin blueprint — there is no
    # admin/HOD. The old /portal/ URL redirects to /student/login.)
    from app.routes.analytics import analytics_bp
    from app.routes.auth import auth_bp
    from app.routes.cameras import cameras_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.landing import landing_bp
    from app.routes.settings import settings_bp
    from app.routes.student import student_bp
    from app.routes.student_portal import student_portal_bp
    from app.routes.students import students_bp
    from app.routes.timetable import timetable_bp

    app.register_blueprint(landing_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(timetable_bp)
    app.register_blueprint(cameras_bp)
    app.register_blueprint(students_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(student_portal_bp)
    app.register_blueprint(settings_bp)

    # Setup User loader for Flask-Login. (v4: no account is ever
    # disabled — there is nobody above the teacher.)
    from app.models import Teacher

    @login_manager.user_loader
    def load_user(user_id):
        return Teacher.query.get(int(user_id))

    # Create tables if they do not exist, then migrate + backfill + tidy.
    with app.app_context():
        db.create_all()
        _migrate_and_backfill(app)
        _close_stuck_sessions(app)

    # Start the automated timetable scheduler (Phase 5).
    # Never start it in tests: unit tests set TESTING=True and must not
    # spawn a background scheduler thread.
    if not app.config.get('TESTING'):
        try:
            from app.services.scheduler_service import AutomatedSchedulerService
            AutomatedSchedulerService.init_scheduler(app)
        except Exception as exc:  # scheduler must never stop the app starting
            app.logger.warning(f"Scheduler could not be started: {exc}")

    return app
