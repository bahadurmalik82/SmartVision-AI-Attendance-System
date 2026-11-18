"""Admin page (v3): manage teachers + see the audit trail.

Only the admin (the first registered teacher) can open /admin/teachers.
Admins can disable/enable a teacher and delete a teacher (never
themselves, and never the last admin). Disabled teachers cannot log in.
"""
from functools import wraps

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import db
from app.models import AuditLog, Student, Teacher
from app.services.audit import log_action

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@admin_bp.route('/teachers')
@admin_required
def teachers():
    all_teachers = Teacher.query.order_by(Teacher.id).all()
    entries = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(50).all()
    return render_template('admin_teachers.html',
                           teachers=all_teachers, audit_entries=entries)


@admin_bp.route('/teachers/create', methods=['POST'])
@admin_required
def create_teacher():
    """Admin creates a teacher account (this is how a second admin is
    made, so the demo account can later be deleted)."""
    from app.routes.auth import validate_password

    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    department = request.form.get('department', 'Computer Science').strip()
    password = request.form.get('password', '')
    make_admin = request.form.get('is_admin') == 'on'

    if not name or not email:
        flash('Name and email are required.', 'danger')
        return redirect(url_for('admin.teachers'))
    pw_error = validate_password(password)
    if pw_error:
        flash(pw_error, 'danger')
        return redirect(url_for('admin.teachers'))
    if Teacher.query.filter_by(email=email).first():
        flash('An account with this email already exists.', 'warning')
        return redirect(url_for('admin.teachers'))

    teacher = Teacher(name=name, email=email, department=department,
                      is_admin=make_admin, is_active=True)
    teacher.set_password(password)
    db.session.add(teacher)
    db.session.commit()
    log_action('admin_create_teacher',
               f'{email} created by {current_user.email} '
               f'(admin={make_admin}).')
    flash(f'Teacher account created for {name} ({email})'
          + (' as admin.' if make_admin else '.'), 'success')
    return redirect(url_for('admin.teachers'))


@admin_bp.route('/teachers/<int:teacher_id>/toggle-active', methods=['POST'])
@admin_required
def toggle_active(teacher_id):
    teacher = Teacher.query.get_or_404(teacher_id)
    if teacher.id == current_user.id:
        flash('You cannot disable your own account.', 'danger')
        return redirect(url_for('admin.teachers'))
    teacher.is_active = not teacher.is_active
    db.session.commit()
    state = 'enabled' if teacher.is_active else 'disabled'
    log_action('admin_toggle_teacher',
               f'{teacher.email} {state} by {current_user.email}.')
    flash(f'Teacher {teacher.name} ({teacher.email}) is now {state}.',
          'success' if teacher.is_active else 'warning')
    return redirect(url_for('admin.teachers'))


@admin_bp.route('/teachers/<int:teacher_id>/delete', methods=['POST'])
@admin_required
def delete_teacher(teacher_id):
    teacher = Teacher.query.get_or_404(teacher_id)
    if teacher.id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin.teachers'))
    if teacher.is_admin and Teacher.query.filter(
            Teacher.is_admin.is_(True),
            Teacher.id != teacher.id).count() == 0:
        flash('This is the only admin account. Make another teacher an '
              'admin first (create one below), then delete this one.',
              'danger')
        return redirect(url_for('admin.teachers'))
    # Their students stay in the system, reassigned to the admin.
    Student.query.filter_by(teacher_id=teacher.id).update(
        {'teacher_id': current_user.id})
    db.session.delete(teacher)
    db.session.commit()
    log_action('admin_delete_teacher',
               f'{teacher.email} deleted by {current_user.email}; '
               'their students moved to the admin.')
    flash(f'Teacher {teacher.name} ({teacher.email}) deleted. Their '
          'students were moved to your account.', 'success')
    return redirect(url_for('admin.teachers'))
