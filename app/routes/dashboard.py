import base64
import io
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, current_app, session as flask_session)
from flask_login import login_required, current_user
from PIL import Image
from sqlalchemy import or_
from app import db
from app.models import Schedule, Student, AttendanceSession, CameraSource, ClassGroup
from app.services.scheduler_service import AutomatedSchedulerService
from app.services.camera_streamer import CameraStreamProcessor
from app.services.audit import log_action

dashboard_bp = Blueprint('dashboard', __name__)
stream_processor = CameraStreamProcessor(duration_tracker=AutomatedSchedulerService.get_duration_tracker())

# A base64 frame is ~4/3 the size of the decoded image; 7 MB of base64
# is roughly a 5 MB photo — anything bigger is rejected, not decoded.
MAX_FRAME_B64_CHARS = 7_000_000


def _my_session_query():
    """Active sessions owned by me (or legacy sessions with no owner)."""
    return AttendanceSession.query.filter(
        or_(AttendanceSession.teacher_id == current_user.id,
            AttendanceSession.teacher_id.is_(None)))


@dashboard_bp.route('/dashboard')
@login_required
def index():
    schedules = Schedule.query.filter_by(teacher_id=current_user.id).all()
    total_students = Student.query.filter(
        or_(Student.teacher_id == current_user.id,
            Student.teacher_id.is_(None))).count()
    active_session = _my_session_query().filter_by(is_active=True).first()
    cameras = CameraSource.query.filter_by(teacher_id=current_user.id).all()
    classes = ClassGroup.query.filter_by(teacher_id=current_user.id).order_by(ClassGroup.name).all()
    active_camera = next((c for c in cameras if c.is_active), None)
    # v9.2: what the teacher is VIEWING on the dashboard right now —
    # picked from the dropdown below the camera screen. It is only a
    # viewing choice: it never changes the saved Primary Active
    # camera. A stale or foreign id falls back to the primary.
    view_camera = active_camera
    view_id = flask_session.get('dashboard_camera_id')
    if view_id is not None:
        chosen = next((c for c in cameras if c.id == view_id), None)
        if chosen is not None:
            view_camera = chosen

    return render_template(
        'dashboard.html',
        schedules=schedules,
        total_students=total_students,
        active_session=active_session,
        cameras=cameras,
        classes=classes,
        active_camera=active_camera,
        view_camera=view_camera,
    )


@dashboard_bp.route('/dashboard/camera/select', methods=['POST'])
@login_required
def select_camera():
    """Switch which camera the dashboard shows right now.

    Only a viewing choice — the saved Primary Active camera is
    untouched. The camera must belong to this teacher (404
    otherwise), and the choice is kept in the session until the
    teacher picks another camera or deletes this one.
    """
    raw_id = (request.form.get('camera_id') or '').strip()
    try:
        camera_id = int(raw_id)
    except ValueError:
        flash('Please choose a camera first.', 'warning')
        return redirect(url_for('dashboard.index'))
    cam = CameraSource.query.filter_by(
        id=camera_id, teacher_id=current_user.id).first_or_404()
    flask_session['dashboard_camera_id'] = cam.id
    flash(f"Now viewing: {cam.name}.", "success")
    return redirect(url_for('dashboard.index'))


@dashboard_bp.route('/dashboard/session-status')
@login_required
def session_status():
    """Lightweight JSON poll for the dashboard (v5 camera auto-start).

    Returns whether a class is live right now, so the page can start
    the camera on its own when the timetable starts a class — the
    teacher does not have to click anything at class time.
    """
    active_session = _my_session_query().filter_by(is_active=True).first()
    if not active_session:
        return jsonify({'active': False})
    return jsonify({
        'active': True,
        'course_name': active_session.course_name,
        'section': active_session.section,
        'started_at': active_session.start_time.isoformat() if active_session.start_time else None,
    })


@dashboard_bp.route('/start-class-manual', methods=['POST'])
@login_required
def start_class_manual():
    """Manual override button to immediately begin attendance monitoring."""
    course_name = request.form.get('course_name', 'Machine Learning')
    section = request.form.get('section', 'Section B')
    try:
        duration = int(request.form.get('duration', 100))
    except ValueError:
        duration = 100
    # v5: the teacher picks which class this manual class is for, so
    # only that class's students get attendance records.
    class_group_id = None
    raw_group = request.form.get('class_group_id', '').strip()
    if raw_group:
        try:
            group = ClassGroup.query.filter_by(
                id=int(raw_group), teacher_id=current_user.id).first()
            if group is not None:
                class_group_id = group.id
        except ValueError:
            class_group_id = None

    session = AutomatedSchedulerService.start_manual_session(
        course_name=course_name,
        section=section,
        duration_minutes=duration,
        teacher_id=current_user.id,
        class_group_id=class_group_id,
    )
    log_action('session_start',
               f'Manual class started: {course_name} ({section}).')
    flash(f"Class '{session.course_name} ({session.section})' started! Live monitoring is active.", "success")
    return redirect(url_for('dashboard.index'))


@dashboard_bp.route('/stop-class-manual', methods=['POST'])
@login_required
def stop_class_manual():
    """Manual override button to immediately finalize and close class attendance."""
    active_session = _my_session_query().filter_by(is_active=True).first()
    if active_session:
        AutomatedSchedulerService.finalize_session(active_session)
        log_action('session_stop',
                   f"Class '{active_session.course_name}' closed.")
        flash(f"Class '{active_session.course_name}' closed. Attendance report generated!", "info")
    else:
        flash("No active class is currently running.", "warning")
    return redirect(url_for('dashboard.index'))


@dashboard_bp.route('/process-frame', methods=['POST'])
@login_required
def process_frame():
    """Receives a base64 video frame, runs liveness & face recognition, and logs duration."""
    data = request.get_json(silent=True)
    if not data or 'image' not in data:
        return jsonify({'success': False, 'message': 'No frame image received'}), 400

    b64_str = data['image']
    if ',' in b64_str:
        b64_str = b64_str.split(',')[1]
    if len(b64_str) > MAX_FRAME_B64_CHARS:
        return jsonify({'success': False,
                        'message': 'Frame is too large. Please use a smaller camera resolution.'}), 400

    # Only process frames for one of MY active sessions.
    if _my_session_query().filter_by(is_active=True).first() is None:
        return jsonify({'success': False,
                        'message': 'No active class session. Start a class first.'}), 400

    try:
        img_bytes = base64.b64decode(b64_str, validate=True)
        if len(img_bytes) > 5 * 1024 * 1024:
            return jsonify({'success': False,
                            'message': 'Frame is too large.'}), 400
        pil_img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
        result = stream_processor.process_frame(pil_img)
        return jsonify(result)
    except Exception as exc:
        current_app.logger.warning(f'Frame processing failed: {exc}')
        return jsonify({'success': False,
                        'message': 'Could not process this frame. '
                                   'Please try again.'}), 400
