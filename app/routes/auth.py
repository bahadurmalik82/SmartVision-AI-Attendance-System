"""Teacher login / registration / password change (v4).

There is no admin, no HOD and no one above a teacher: every teacher
registers their own account and is the only owner of their classes.

Security rules enforced here:
  * Registration is OPEN to any teacher (name, email, department,
    password). A second, third, ... teacher registers the same way.
  * Passwords must be at least 8 characters with at least one letter and
    one digit (register + change-password).
  * Login is rate-limited by hand (no extra dependency): 5 failed
    attempts per email+IP in 15 minutes, then a lockout message.
    Failed attempts are logged.
  * The ?next= redirect after login only accepts relative paths
    starting with a single "/" — never an external URL.
"""
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from app import db
from app.models import Teacher
from app.services.audit import log_action

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# --- Hand-rolled login lockout -------------------------------------------
# key (email, ip) -> list of failed-attempt datetimes (last 15 minutes)
_FAILED_ATTEMPTS = {}
MAX_FAILED = 5
LOCKOUT_WINDOW = timedelta(minutes=15)


def reset_login_attempts():
    """Test helper: clear all recorded failed logins."""
    _FAILED_ATTEMPTS.clear()


def _failed_recently(key):
    now = datetime.now()
    attempts = [t for t in _FAILED_ATTEMPTS.get(key, [])
                if now - t < LOCKOUT_WINDOW]
    _FAILED_ATTEMPTS[key] = attempts
    return attempts


def _is_locked_out(email, ip):
    return len(_failed_recently((email, ip))) >= MAX_FAILED


def _record_failed(email, ip):
    _FAILED_ATTEMPTS.setdefault((email, ip), []).append(datetime.now())


def _clear_failed(email, ip):
    _FAILED_ATTEMPTS.pop((email, ip), None)


def _safe_next_url(default):
    """Only allow a relative in-app redirect ("/dashboard/..."), never
    an external URL such as "//evil.com" or "https://evil.com"."""
    target = request.args.get('next') or ''
    if target.startswith('/') and not target.startswith('//'):
        parsed = urlparse(target)
        if not parsed.scheme and not parsed.netloc:
            return target
    return default


def validate_password(password):
    """Password rule: min 8 chars, at least one letter and one digit.

    Returns an error string, or None when the password is acceptable.
    """
    password = password or ''
    if len(password) < 8:
        return 'Password must be at least 8 characters long.'
    if not any(c.isalpha() for c in password):
        return 'Password must contain at least one letter.'
    if not any(c.isdigit() for c in password):
        return 'Password must contain at least one digit.'
    return None


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = True if request.form.get('remember') else False
        ip = request.remote_addr or 'unknown'

        if _is_locked_out(email, ip):
            current_app.logger.warning(
                f'Login locked out for {email} from {ip} (too many failures).')
            flash('Too many failed login attempts. Please try again in '
                  '15 minutes.', 'danger')
            return render_template('login.html', is_signup=False)

        teacher = Teacher.query.filter_by(email=email).first()
        if teacher and teacher.check_password(password):
            _clear_failed(email, ip)
            login_user(teacher, remember=remember)
            log_action('login', f'{teacher.email} logged in.')
            flash(f"Welcome back, Prof. {teacher.name}!", "success")
            return redirect(_safe_next_url(url_for('dashboard.index')))

        _record_failed(email, ip)
        current_app.logger.warning(
            f'Failed login for {email} from {ip}.')
        flash("Invalid email or password. Please try again.", "danger")

    return render_template('login.html', is_signup=False)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        department = request.form.get('department', 'Computer Science').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not name or not email:
            flash('Full name and email are required.', 'danger')
            return render_template('login.html', is_signup=True)

        if password != confirm_password:
            flash("Passwords do not match!", "danger")
            return render_template('login.html', is_signup=True)

        pw_error = validate_password(password)
        if pw_error:
            flash(pw_error, "danger")
            return render_template('login.html', is_signup=True)

        existing = Teacher.query.filter_by(email=email).first()
        if existing:
            flash("An account with this email already exists.", "warning")
            return redirect(url_for('auth.login'))

        new_teacher = Teacher(name=name, email=email, department=department,
                              is_active=True)
        new_teacher.set_password(password)
        db.session.add(new_teacher)
        db.session.commit()

        login_user(new_teacher)
        log_action('register', f'Teacher registered: {email}.')
        flash("Account created successfully! Welcome to Attenova. "
              "You are the owner of your classes — add your timetable, "
              "cameras and students from the Dashboard.", "success")
        return redirect(url_for('dashboard.index'))

    return render_template('login.html', is_signup=True)


@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')

        if not current_user.check_password(current):
            flash('Your current password is incorrect.', 'danger')
        elif new != confirm:
            flash('New passwords do not match.', 'danger')
        else:
            pw_error = validate_password(new)
            if pw_error:
                flash(pw_error, 'danger')
            else:
                current_user.set_password(new)
                db.session.commit()
                log_action('change_password',
                           f'{current_user.email} changed their password.')
                flash('Password changed successfully.', 'success')
                return redirect(url_for('dashboard.index'))
    return render_template('change_password.html')


@auth_bp.route('/logout')
@login_required
def logout():
    log_action('logout', f'{current_user.email} logged out.')
    logout_user()
    flash("You have been securely logged out.", "info")
    return redirect(url_for('auth.login'))
