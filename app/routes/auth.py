"""Teacher login / registration / password change (v4).

There is no admin, no HOD and no one above a teacher: every teacher
registers their own account and is the only owner of their classes.

Security rules enforced here:
  * Registration is OPEN to any teacher (name, email, department,
    password). A second, third, ... teacher registers the same way.
  * Registration is verified by email (v8.3): the teacher's account
    is created ONLY after they enter the 6-digit code sent to their
    email. The code expires in 2 minutes, can be re-sent after a
    30-second wait (max 5 resends), and 5 wrong tries invalidate it.
    The code itself is never stored or logged — only its HMAC-SHA256
    (keyed with the app SECRET_KEY) lives in PendingRegistration.
  * Passwords must be at least 8 characters with at least one letter and
    one digit (register + change-password).
  * Login is rate-limited by hand (no extra dependency): 5 failed
    attempts per email+IP in 15 minutes, then a lockout message.
    Failed attempts are logged.
  * The ?next= redirect after login only accepts relative paths
    starting with a single "/" — never an external URL.
"""
import hashlib
import hmac
import secrets
import string
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from app import bcrypt, db
from app.models import PendingRegistration, Teacher
from app.services.audit import log_action
from app.services.email_service import is_configured, send_verification_code

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# --- Email verification codes (v8.3) --------------------------------------
OTP_LENGTH = 6
OTP_EXPIRY = timedelta(minutes=2)
OTP_RESEND_COOLDOWN = timedelta(seconds=30)
OTP_MAX_ATTEMPTS = 5
OTP_MAX_RESENDS = 5


def _generate_otp_code():
    """A random 6-digit code, never 000000. Patched to a fixed value
    in tests (see tests/test_phase15.py)."""
    while True:
        code = ''.join(secrets.choice(string.digits)
                       for _ in range(OTP_LENGTH))
        if code != '0' * OTP_LENGTH:
            return code


def _hash_otp(code):
    """HMAC-SHA256 of a code, keyed with the app SECRET_KEY. The plain
    code is never stored — only this hash."""
    return hmac.new(
        current_app.config['SECRET_KEY'].encode(),
        str(code).encode(),
        hashlib.sha256).hexdigest()


def _otp_matches(code, otp_hash):
    """Constant-time check of an entered code against a stored hash."""
    if not code or not otp_hash:
        return False
    return hmac.compare_digest(_hash_otp(code), otp_hash)


def _otp_seconds_left(pending, now=None):
    """Seconds until a pending code expires (0 when already expired)."""
    now = now or datetime.now()
    return max(0, int((pending.expires_at - now).total_seconds()))


def _otp_resend_in(pending, now=None):
    """Seconds until a new code may be requested (0 when allowed)."""
    now = now or datetime.now()
    return max(0, int((pending.last_sent_at + OTP_RESEND_COOLDOWN
                       - now).total_seconds()))

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

        # v8.3: no account is created yet. The teacher must first prove
        # the email is theirs by entering the 6-digit code we send.
        if not is_configured():
            flash("Email is not set up yet, so the verification code "
                  "cannot be sent. Set up Gmail in .env first — "
                  "Settings, Email alerts — then try again.", "warning")
            return render_template('login.html', is_signup=True)

        code = _generate_otp_code()
        now = datetime.now()
        password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
        pending = PendingRegistration.query.filter_by(email=email).first()
        if pending is None:
            pending = PendingRegistration(
                email=email, name=name, department=department,
                password_hash=password_hash, otp_hash=_hash_otp(code),
                attempts=0, resend_count=0,
                last_sent_at=now, expires_at=now + OTP_EXPIRY)
            db.session.add(pending)
        else:
            # Re-registering replaces the old code (and its counters).
            pending.name = name
            pending.department = department
            pending.password_hash = password_hash
            pending.otp_hash = _hash_otp(code)
            pending.attempts = 0
            pending.resend_count = 0
            pending.last_sent_at = now
            pending.expires_at = now + OTP_EXPIRY
        db.session.commit()

        sent, message = send_verification_code(email, code)
        if not sent:
            flash(message, "warning")
            return render_template('login.html', is_signup=True)
        return render_template('login.html', is_signup=True,
                               show_otp=True, otp_email=email,
                               otp_expires_in=int(OTP_EXPIRY.total_seconds()),
                               otp_resend_in=int(
                                   OTP_RESEND_COOLDOWN.total_seconds()))

    return render_template('login.html', is_signup=True)


def _render_otp_popup(pending, otp_error=None, otp_note=None):
    """Re-render the signup page with the OTP popup open."""
    now = datetime.now()
    return render_template('login.html', is_signup=True,
                           show_otp=True, otp_email=pending.email,
                           otp_expires_in=_otp_seconds_left(pending, now),
                           otp_resend_in=_otp_resend_in(pending, now),
                           otp_error=otp_error, otp_note=otp_note)


@auth_bp.route('/verify-otp', methods=['POST'])
def verify_otp():
    """Check the 6-digit code; on success create the Teacher account."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    email = request.form.get('email', '').strip().lower()
    code = request.form.get('code', '').strip()
    pending = PendingRegistration.query.filter_by(email=email).first()
    now = datetime.now()

    if pending is None:
        flash("No verification code was sent to this email. "
              "Register again.", "danger")
        return redirect(url_for('auth.register'))

    if now > pending.expires_at:
        db.session.delete(pending)
        db.session.commit()
        flash("That code expired (codes last 2 minutes). "
              "Ask for a new one.", "warning")
        return redirect(url_for('auth.register'))

    if pending.attempts >= OTP_MAX_ATTEMPTS:
        db.session.delete(pending)
        db.session.commit()
        flash("Too many wrong tries. Register again to get a fresh "
              "code.", "danger")
        return redirect(url_for('auth.register'))

    if (len(code) != OTP_LENGTH or not code.isdigit()
            or not _otp_matches(code, pending.otp_hash)):
        pending.attempts += 1
        db.session.commit()
        if pending.attempts >= OTP_MAX_ATTEMPTS:
            db.session.delete(pending)
            db.session.commit()
            flash("Too many wrong tries. Register again to get a fresh "
                  "code.", "danger")
            return redirect(url_for('auth.register'))
        return _render_otp_popup(
            pending, otp_error="Wrong code. Try again.")

    teacher = Teacher(name=pending.name, email=pending.email,
                      department=pending.department, is_active=True)
    teacher.password_hash = pending.password_hash
    db.session.add(teacher)
    db.session.delete(pending)
    db.session.commit()

    login_user(teacher)
    log_action('register', f'Teacher registered: {email}.')
    flash("Account created successfully! Welcome to Attenova. "
          "You are the owner of your classes — add your timetable, "
          "cameras and students from the Dashboard.", "success")
    return redirect(url_for('dashboard.index'))


@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    """Send a fresh code for a pending registration (30s cooldown,
    max 5 resends)."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    email = request.form.get('email', '').strip().lower()
    pending = PendingRegistration.query.filter_by(email=email).first()
    if pending is None:
        return redirect(url_for('auth.register'))

    now = datetime.now()
    wait = _otp_resend_in(pending, now)
    if wait > 0:
        return _render_otp_popup(
            pending,
            otp_error=f"Wait {wait} seconds before asking for a new code.")

    if pending.resend_count >= OTP_MAX_RESENDS:
        db.session.delete(pending)
        db.session.commit()
        flash("Too many resends. Register again to get a fresh code.",
              "warning")
        return redirect(url_for('auth.register'))

    code = _generate_otp_code()
    pending.otp_hash = _hash_otp(code)
    pending.expires_at = now + OTP_EXPIRY
    pending.last_sent_at = now
    pending.resend_count += 1
    db.session.commit()

    sent, message = send_verification_code(email, code)
    if not sent:
        flash(message, "warning")
        return _render_otp_popup(pending)
    return _render_otp_popup(
        pending, otp_note=f"A new code was sent to {email}.")


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
