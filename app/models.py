import re
import secrets
import string
from datetime import datetime
import json
from flask_login import UserMixin
from app import db, bcrypt


class Teacher(UserMixin, db.Model):
    """Teacher user model with secure password hashing.

    Every teacher is the only owner of their own classes — there is no
    admin, no HOD and no one above a teacher (v4). The ``is_admin``
    column is intentionally UNUSED since v4; it is kept in the model
    (and in old databases) only so existing databases keep working.
    """
    __tablename__ = 'teachers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    department = db.Column(db.String(100), default='Computer Science')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # UNUSED since v4 (no admin/HOD). Kept so old databases keep working.
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Relationships
    schedules = db.relationship('Schedule', backref='teacher', lazy=True, cascade='all, delete-orphan')
    cameras = db.relationship('CameraSource', backref='teacher', lazy=True, cascade='all, delete-orphan')
    students = db.relationship('Student', backref='teacher', lazy=True)
    # v5: the teacher's classes. (ClassGroup.students cascades, so
    # deleting a teacher deletes their classes and their students.)
    class_groups = db.relationship('ClassGroup', backref='teacher', lazy=True,
                                   cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<Teacher {self.name} ({self.email})>"


class ClassGroup(db.Model):
    """One class a teacher teaches — e.g. BS Computer Science - AI,
    Section B, Semester 5.

    v5: this is what fixes the "one big roster" problem. A teacher can
    have many classes (Section B and Section C, Semester 3 and 5,
    different departments). Every student belongs to exactly ONE class
    group, every timetable entry belongs to one class group, and when
    a class runs, attendance records are created ONLY for that class's
    students — the other classes' students are never marked absent
    for a class they are not in.

    Editing a class only fixes a typo. When a class moves to a new
    semester, the teacher creates a NEW class group (the old one stays
    until they explicitly delete it), so last semester's records stay
    intact. Deleting a class group deletes its students (and their
    attendance records) and its timetable entries — the delete button
    asks for confirmation first.
    """
    __tablename__ = 'class_groups'

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    course_name = db.Column(db.String(150), nullable=True)
    course_code = db.Column(db.String(50), nullable=True)
    department = db.Column(db.String(100), default='Computer Science')
    semester = db.Column(db.String(50), default='5th')
    section = db.Column(db.String(20), default='Section B')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    students = db.relationship('Student', backref='class_group', lazy=True,
                               cascade='all, delete-orphan')
    schedules = db.relationship('Schedule', backref='class_group', lazy=True,
                                cascade='all, delete-orphan')

    def display_name(self):
        """'BS Computer Science - AI · Section B · Semester 5'."""
        return f'{self.department} · {self.section} · Semester {self.semester}'

    def __repr__(self):
        return f"<ClassGroup {self.name}>"


def get_or_create_class_group(teacher_id, department, semester, section,
                              course_name=None, course_code=None, name=None):
    """Find the teacher's class group for (department, semester,
    section), or create it. Used by enrolment, CSV import and the
    timetable so the same class is never created twice."""
    department = (department or 'Computer Science').strip()
    semester = (semester or '5th').strip()
    section = (section or 'Section B').strip()
    group = ClassGroup.query.filter_by(
        teacher_id=teacher_id, department=department,
        semester=semester, section=section).first()
    if group is not None:
        return group
    group = ClassGroup(
        teacher_id=teacher_id,
        name=name or f'{department} · {section} · Semester {semester}',
        course_name=(course_name or '').strip() or None,
        course_code=(course_code or '').strip().upper() or None,
        department=department, semester=semester, section=section)
    db.session.add(group)
    db.session.flush()
    return group


class CameraSource(db.Model):
    """Registered video cameras (Built-in/USB Webcams or Mobile IP Webcam streams)."""
    __tablename__ = 'camera_sources'

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)  # e.g. "Front Wall IP Webcam"
    camera_type = db.Column(db.String(20), default='ip_webcam')  # 'browser' or 'ip_webcam'
    stream_url = db.Column(db.String(255), nullable=True)  # e.g. "http://192.168.1.15:8080/video"
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<CameraSource {self.name} ({self.camera_type})>"


class Schedule(db.Model):
    """Weekly recurrent class timetable configured by the teacher."""
    __tablename__ = 'schedules'

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=False)
    course_name = db.Column(db.String(150), nullable=False)  # e.g. "Machine Learning"
    course_code = db.Column(db.String(50), nullable=False)   # e.g. "CS-302"
    department = db.Column(db.String(100), default='Computer Science')
    semester = db.Column(db.String(50), default='5th')       # e.g. "BS-V"
    section = db.Column(db.String(20), default='Section B')  # e.g. "Section B"
    day_of_week = db.Column(db.String(20), nullable=False)   # e.g. "Tuesday"
    start_time = db.Column(db.Time, nullable=False)          # e.g. 09:00:00
    end_time = db.Column(db.Time, nullable=False)            # e.g. 10:40:00
    is_paused = db.Column(db.Boolean, default=False)         # Holiday / Cancelled toggle
    # v5: which class group this timetable entry is for. NULL = legacy
    # entries created before v5 (backfilled on startup from the
    # department/semester/section fields).
    class_group_id = db.Column(db.Integer, db.ForeignKey('class_groups.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    sessions = db.relationship('AttendanceSession', backref='schedule', lazy=True, cascade='all, delete-orphan')

    def duration_minutes(self):
        start_mins = self.start_time.hour * 60 + self.start_time.minute
        end_mins = self.end_time.hour * 60 + self.end_time.minute
        return max(0, end_mins - start_mins)

    def __repr__(self):
        return f"<Schedule {self.course_code} - {self.day_of_week} ({self.start_time}-{self.end_time})>"


class Student(db.Model):
    """Enrolled student details, metadata, and numerical face embeddings."""
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True)
    roll_number = db.Column(db.String(50), unique=True, nullable=False, index=True) # e.g. "K21-1234"
    name = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(100), default='Computer Science')
    semester = db.Column(db.String(50), default='5th')
    section = db.Column(db.String(20), default='Section B')
    email = db.Column(db.String(150), nullable=True)
    # v3: which teacher owns this student (nullable = legacy rows created
    # before v3; those are backfilled to the first teacher on startup).
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=True)
    # v5: which class group this student belongs to (one class per
    # student). NULL = legacy rows created before v5 (backfilled on
    # startup from the department/semester/section fields).
    class_group_id = db.Column(db.Integer, db.ForeignKey('class_groups.id'), nullable=True)
    # v3: bcrypt hash of the student's 4-digit portal PIN. UNUSED since
    # v4 (the CMS ID + password system replaced it). The column is kept
    # in the database untouched so old databases keep working.
    pin_hash = db.Column(db.String(256), nullable=True)
    # v4: student portal login. CMS ID is unique (CMS-0001, CMS-0002, ...).
    # portal_password_plain is stored ON PURPOSE: the teacher must be
    # able to see every student's password so a student who forgets it
    # can ask the teacher. It is shown ONLY to the owning teacher, never
    # to other students and never in logs.
    cms_id = db.Column(db.String(50), unique=True, nullable=True)
    portal_password_hash = db.Column(db.String(256), nullable=True)
    portal_password_plain = db.Column(db.String(50), nullable=True)

    # Serialized 128-D numerical face feature vector (JSON string)
    face_embedding_json = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    attendance_records = db.relationship('AttendanceRecord', backref='student', lazy=True, cascade='all, delete-orphan')

    def set_face_embedding(self, embedding_list):
        self.face_embedding_json = json.dumps(embedding_list)

    def get_face_embedding(self):
        if self.face_embedding_json:
            return json.loads(self.face_embedding_json)
        return None

    def set_portal_password(self, password):
        """Set the student's portal password.

        The bcrypt hash is what login checks; the plain copy is kept so
        the owning teacher can tell a forgetful student their password.
        """
        self.portal_password_plain = password
        self.portal_password_hash = bcrypt.generate_password_hash(
            password).decode('utf-8')

    def check_portal_password(self, password):
        """True when the given password matches this student's password."""
        if not password or not self.portal_password_hash:
            return False
        return bcrypt.check_password_hash(self.portal_password_hash,
                                          str(password))

    def __repr__(self):
        return f"<Student {self.roll_number} - {self.name}>"


def generate_student_password(length=8):
    """Random student portal password: letters + digits, at least one
    of each. 8 characters, e.g. 'Xy9k2mQa'."""
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = ''.join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isalpha() for c in pw) and any(c.isdigit() for c in pw):
            return pw


def next_cms_id():
    """Next free CMS ID (CMS-0001, CMS-0002, ...) — one above the
    highest number already in use. Safe to call before inserting."""
    highest = 0
    for (cid,) in db.session.query(Student.cms_id).all():
        m = re.fullmatch(r'CMS-(\d+)', cid or '')
        if m:
            highest = max(highest, int(m.group(1)))
    return f'CMS-{highest + 1:04d}'


class AttendanceSession(db.Model):
    """A single lecture session instance triggered by schedule or manual override."""
    __tablename__ = 'attendance_sessions'

    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedules.id'), nullable=True)
    # v3: which teacher owns this session (nullable = legacy rows).
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=True)
    # v5: which class group this session is for (set from the schedule
    # for auto-started classes, or from the teacher's choice for a
    # manual start). Finalising a session only creates records for
    # this class's students — never for other classes.
    class_group_id = db.Column(db.Integer, db.ForeignKey('class_groups.id'), nullable=True)
    course_name = db.Column(db.String(150), nullable=False)
    section = db.Column(db.String(20), default='Section B')
    session_date = db.Column(db.Date, default=datetime.utcnow().date, nullable=False, index=True)
    start_time = db.Column(db.DateTime, default=datetime.utcnow)
    end_time = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, index=True)
    total_duration_minutes = db.Column(db.Integer, default=0)

    # Relationships
    records = db.relationship('AttendanceRecord', backref='session', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f"<AttendanceSession #{self.id} - {self.course_name} on {self.session_date}>"


class AttendanceRecord(db.Model):
    """Individual student attendance and engagement record per session."""
    __tablename__ = 'attendance_records'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('attendance_sessions.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)

    first_seen = db.Column(db.DateTime, nullable=True)
    last_seen = db.Column(db.DateTime, nullable=True)
    minutes_present = db.Column(db.Float, default=0.0)
    attendance_percentage = db.Column(db.Float, default=0.0)

    # Status options: "Present", "Late", "Left Early", "Absent"
    status = db.Column(db.String(20), default='Absent')

    # Teacher manual override support
    manual_override = db.Column(db.Boolean, default=False)
    override_reason = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def calculate_status(self, total_class_minutes, threshold=75.0):
        """Calculates percentage and attendance status based on university guidelines.

        ``threshold`` is the exam-eligibility % (default 75) — the live
        value comes from the Settings table (see /settings).
        """
        if total_class_minutes <= 0:
            self.attendance_percentage = 0.0
            self.status = "Absent"
            return

        self.attendance_percentage = round(min(100.0, (self.minutes_present / total_class_minutes) * 100.0), 1)

        if self.attendance_percentage >= threshold:
            self.status = "Present"
        elif self.attendance_percentage >= 50.0:
            self.status = "Left Early"
        else:
            self.status = "Absent"

    def __repr__(self):
        return f"<AttendanceRecord Student:{self.student_id} Status:{self.status} ({self.attendance_percentage}%)>"


class Settings(db.Model):
    """Simple key/value settings (attendance threshold, late threshold, ...)."""
    __tablename__ = 'settings'

    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f"<Setting {self.key}={self.value}>"


class AuditLog(db.Model):
    """Audit trail: who did what, when. Shown on the Settings page."""
    __tablename__ = 'audit_log'

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=True)
    action = db.Column(db.String(80), nullable=False)
    detail = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teacher = db.relationship('Teacher', backref='audit_entries', lazy=True)

    def __repr__(self):
        return f"<AuditLog {self.action} by {self.teacher_id} at {self.created_at}>"
