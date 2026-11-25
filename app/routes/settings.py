"""Settings page + backup download (v3).

/settings — change the live attendance threshold and late threshold
(stored in the Settings table and used by Analytics + status
calculation), link to change-password, and download a backup.
/settings/backup — downloads a zip of attendance.db plus a README
noting that photos/embeddings live separately in data/.
"""
import io
import os
import zipfile
from datetime import datetime

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, send_file, url_for)
from flask_login import current_user, login_required

from app import db
from app.models import AuditLog, Feedback
from app.services.audit import log_action
from app.services.email_service import email_status, send_test_email
from app.services.settings import (get_late_threshold, get_threshold,
                                   set_setting)

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')


@settings_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        try:
            threshold = float(request.form.get('attendance_threshold', '75'))
            late = float(request.form.get('late_threshold_minutes', '15'))
        except ValueError:
            flash('Thresholds must be numbers.', 'danger')
            return redirect(url_for('settings.index'))
        if not (0 < threshold <= 100):
            flash('Attendance threshold must be between 1 and 100.', 'danger')
            return redirect(url_for('settings.index'))
        if not (0 <= late <= 120):
            flash('Late threshold must be between 0 and 120 minutes.', 'danger')
            return redirect(url_for('settings.index'))
        set_setting('attendance_threshold', str(threshold))
        set_setting('late_threshold_minutes', str(late))
        log_action('settings_change',
                   f'{current_user.email} set threshold={threshold}%, '
                   f'late={late} min.')
        flash('Settings saved. The new threshold is used by Analytics, '
              'Defaulters and new attendance records.', 'success')
        return redirect(url_for('settings.index'))
    # v9.3: the "My recent activity" card is gone — the audit log
    # itself still exists (log_action is still called everywhere),
    # only the card was removed. The Feedback list lives here now.
    feedback_entries = (Feedback.query
                        .order_by(Feedback.created_at.desc()).all())
    return render_template('settings.html',
                           threshold=get_threshold(),
                           late_threshold=get_late_threshold(),
                           feedback_entries=feedback_entries,
                           email_status=email_status())


@settings_bp.route('/personal-details', methods=['POST'])
@login_required
def personal_details():
    """Update the teacher's own details (v9.3).

    Name is required; Qualification and Department are optional.
    The email is shown read-only on the page — changing the login
    email would need re-verification, which is out of scope.
    The name is read live from the DB everywhere it is shown
    (topbar, dashboard welcome, Your account card, and the emails
    sent to students), so saving it here updates all of them.
    """
    name = request.form.get('name', '').strip()
    qualification = request.form.get('qualification', '').strip()
    department = request.form.get('department', '').strip()
    if not name:
        flash('Name is required — please enter your name.', 'danger')
        return redirect(url_for('settings.index'))
    current_user.name = name
    current_user.qualification = qualification or None
    current_user.department = department or None
    db.session.commit()
    log_action('profile_update',
               f'{current_user.email} updated personal details.')
    flash('Personal details saved — your name is now shown as '
          f'"{name}" everywhere, including in emails to students.',
          'success')
    return redirect(url_for('settings.index'))


@settings_bp.route('/feedback', methods=['POST'])
@login_required
def feedback():
    """Save a feedback message from the teacher (v9.3).

    Name and email are prefilled from the logged-in teacher; the
    message is required. All feedback is listed below the form,
    newest first, so the owner can read what was sent.
    """
    name = request.form.get('name', '').strip() or current_user.name
    email = request.form.get('email', '').strip() or current_user.email
    message = request.form.get('message', '').strip()
    if not message:
        flash('Please write your feedback message first.', 'danger')
        return redirect(url_for('settings.index'))
    entry = Feedback(teacher_id=current_user.id, name=name,
                     email=email, message=message)
    db.session.add(entry)
    db.session.commit()
    log_action('feedback', f'{email} sent feedback.')
    flash('Thank you — your feedback has been saved.', 'success')
    return redirect(url_for('settings.index'))


@settings_bp.route('/test-email', methods=['POST'])
@login_required
def test_email():
    """Send a test email to the teacher's own inbox (SMTP_USER), so the
    email setup can be checked without warning a real student."""
    sent, message = send_test_email()
    log_action('test_email',
               f'{current_user.email} sent a test email: '
               f'{"sent" if sent else "failed"}.')
    flash(message, 'success' if sent else 'warning')
    return redirect(url_for('settings.index'))


@settings_bp.route('/backup')
@login_required
def backup():
    """Download a zip containing attendance.db + a restore note."""
    db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    db_path = None
    if db_uri.startswith('sqlite:///') and 'memory' not in db_uri:
        db_path = db_uri.replace('sqlite:///', '')
    if not db_path or not os.path.exists(db_path):
        flash('No database file to back up yet (the app is using an '
              'in-memory database).', 'warning')
        return redirect(url_for('settings.index'))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_path, arcname='attendance.db')
        zf.writestr('README-backup.txt',
                    'Attenova backup — created '
                    f'{datetime.now().strftime("%Y-%m-%d %H:%M")}\n\n'
                    'This zip contains attendance.db only.\n'
                    'Student photos (data/student_photos/) and face\n'
                    'embeddings (data/embeddings/) are NOT in this zip —\n'
                    'copy those folders separately if you need them.\n\n'
                    'RESTORE: stop the app, replace data/attendance.db\n'
                    'with the file in this zip, then start the app again.\n')
    buf.seek(0)
    log_action('backup_download',
               f'{current_user.email} downloaded a database backup.')
    return send_file(
        buf, mimetype='application/zip', as_attachment=True,
        download_name=f"attenova-backup-{datetime.now().strftime('%Y%m%d-%H%M')}.zip")
