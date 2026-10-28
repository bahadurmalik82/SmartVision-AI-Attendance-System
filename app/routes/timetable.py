from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from app.models import Schedule, ClassGroup, get_or_create_class_group

timetable_bp = Blueprint('timetable', __name__, url_prefix='/timetable')

# All 7 days — Sunday included (v5). Some universities hold classes on
# Sunday, and a teacher may reschedule a class to Sunday.
DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
                'Saturday', 'Sunday']


@timetable_bp.route('/')
@login_required
def index():
    schedules = Schedule.query.filter_by(teacher_id=current_user.id).order_by(Schedule.start_time).all()
    classes = ClassGroup.query.filter_by(teacher_id=current_user.id).order_by(ClassGroup.name).all()
    return render_template('timetable.html', schedules=schedules,
                           classes=classes, days=DAYS_OF_WEEK)


@timetable_bp.route('/add', methods=['POST'])
@login_required
def add():
    course_name = request.form.get('course_name', '').strip()
    course_code = request.form.get('course_code', '').strip().upper()
    department = request.form.get('department', 'Computer Science').strip()
    semester = request.form.get('semester', '5th').strip()
    section = request.form.get('section', 'Section B').strip()
    day_of_week = request.form.get('day_of_week')
    start_str = request.form.get('start_time')
    end_str = request.form.get('end_time')

    # v5: the day must be one of the 7 real days (Sunday is valid now).
    if day_of_week not in DAYS_OF_WEEK:
        flash("Please choose a day of the week (Monday to Sunday).", "danger")
        return redirect(url_for('timetable.index'))

    try:
        start_time = datetime.strptime(start_str, '%H:%M').time()
        end_time = datetime.strptime(end_str, '%H:%M').time()
    except (ValueError, TypeError):
        flash("Invalid time format. Please use HH:MM.", "danger")
        return redirect(url_for('timetable.index'))
    if end_time <= start_time:
        flash("End time must be after start time.", "danger")
        return redirect(url_for('timetable.index'))

    # v5: link the entry to a class group. If the teacher picked a
    # class, its department / semester / section win; otherwise a
    # class group is found (or created) from the form fields, so the
    # entry is never left without a class.
    class_group = None
    class_group_id = request.form.get('class_group_id', '').strip()
    if class_group_id:
        try:
            class_group = ClassGroup.query.filter_by(
                id=int(class_group_id), teacher_id=current_user.id).first()
        except ValueError:
            class_group = None
        if class_group is None:
            flash("That class was not found. Please choose a class again.", "danger")
            return redirect(url_for('timetable.index'))
        department, semester, section = (class_group.department,
                                         class_group.semester,
                                         class_group.section)
    if class_group is None:
        class_group = get_or_create_class_group(
            current_user.id, department, semester, section,
            course_name=course_name, course_code=course_code)

    new_sched = Schedule(
        teacher_id=current_user.id,
        course_name=course_name,
        course_code=course_code,
        department=department,
        semester=semester,
        section=section,
        day_of_week=day_of_week,
        start_time=start_time,
        end_time=end_time,
        class_group_id=class_group.id,
    )
    db.session.add(new_sched)
    db.session.commit()
    flash(f"Class '{course_code} ({day_of_week})' added to weekly timetable for {class_group.display_name()}!", "success")
    return redirect(url_for('timetable.index'))


@timetable_bp.route('/toggle-pause/<int:schedule_id>', methods=['POST'])
@login_required
def toggle_pause(schedule_id):
    sched = Schedule.query.filter_by(id=schedule_id, teacher_id=current_user.id).first_or_404()
    sched.is_paused = not sched.is_paused
    db.session.commit()
    state = "paused (Holiday/Cancelled)" if sched.is_paused else "resumed (Active)"
    flash(f"Class '{sched.course_code}' is now {state}.", "info")
    return redirect(url_for('timetable.index'))


@timetable_bp.route('/delete/<int:schedule_id>', methods=['POST'])
@login_required
def delete(schedule_id):
    sched = Schedule.query.filter_by(id=schedule_id, teacher_id=current_user.id).first_or_404()
    db.session.delete(sched)
    db.session.commit()
    flash("Schedule entry deleted successfully.", "success")
    return redirect(url_for('timetable.index'))
