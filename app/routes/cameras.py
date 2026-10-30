import urllib.request
import urllib.error
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from app import db
from app.models import CameraSource
from app.services.audit import log_action
from app.services.url_safety import validate_camera_url

cameras_bp = Blueprint('cameras', __name__, url_prefix='/cameras')


@cameras_bp.route('/')
@login_required
def index():
    cameras = CameraSource.query.filter_by(teacher_id=current_user.id).all()
    return render_template('cameras.html', cameras=cameras)


@cameras_bp.route('/add', methods=['POST'])
@login_required
def add():
    name = request.form.get('name', '').strip()
    camera_type = request.form.get('camera_type', 'browser')
    stream_url = request.form.get('stream_url', '').strip()

    if not name:
        flash("Camera name is required.", "danger")
        return redirect(url_for('cameras.index'))

    # If IP webcam, validate + normalize URL (SSRF-safe: no localhost,
    # no cloud-metadata address, ports 80/443/8080 only).
    if camera_type == 'ip_webcam':
        if not stream_url:
            flash("Stream URL is required for Mobile IP Webcam (e.g. http://192.168.1.15:8080/video).", "danger")
            return redirect(url_for('cameras.index'))

        ok, clean_url, error = validate_camera_url(stream_url)
        if not ok:
            flash(error, "danger")
            return redirect(url_for('cameras.index'))
        stream_url = clean_url

        # If user entered just http://ip:8080 or http://ip:8080/, append /video
        if stream_url.endswith(':8080') or stream_url.endswith(':8080/'):
            stream_url = stream_url.rstrip('/') + '/video'

    # If first camera, make it active by default
    existing_count = CameraSource.query.filter_by(teacher_id=current_user.id).count()
    is_active = (existing_count == 0)

    new_cam = CameraSource(
        teacher_id=current_user.id,
        name=name,
        camera_type=camera_type,
        stream_url=stream_url if camera_type == 'ip_webcam' else None,
        is_active=is_active
    )
    db.session.add(new_cam)
    db.session.commit()
    log_action('camera_add', f"Camera '{name}' registered.")
    flash(f"Camera '{name}' registered successfully!", "success")
    return redirect(url_for('cameras.index'))


@cameras_bp.route('/set-active/<int:camera_id>', methods=['POST'])
@login_required
def set_active(camera_id):
    # Set chosen camera as active and all others as inactive for this teacher
    CameraSource.query.filter_by(teacher_id=current_user.id).update({'is_active': False})
    cam = CameraSource.query.filter_by(id=camera_id, teacher_id=current_user.id).first_or_404()
    cam.is_active = True
    db.session.commit()
    flash(f"Camera '{cam.name}' is now the primary monitoring source.", "success")
    return redirect(url_for('cameras.index'))


@cameras_bp.route('/test-ping', methods=['POST'])
@login_required
def test_ping():
    """Pings a Mobile IP Webcam URL to verify Wi-Fi reachability from this computer.

    SSRF-safe: the URL is validated first (no localhost, no link-local /
    cloud-metadata addresses, ports 80/443/8080 only). Failures return a
    generic message; the real error is only logged server-side.
    """
    url = request.json.get('url', '').strip() if request.is_json else request.form.get('url', '').strip()
    if not url:
        return jsonify({'success': False, 'message': 'No URL provided'}), 400

    ok, clean_url, error = validate_camera_url(url)
    if not ok:
        return jsonify({'success': False, 'message': error}), 400

    try:
        # Try a quick HEAD or GET request with a 3-second timeout
        req = urllib.request.Request(clean_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            status = response.getcode()
            if status in [200, 301, 302]:
                return jsonify({'success': True, 'message': f'Connection successful! (HTTP {status})'})
            else:
                return jsonify({'success': False, 'message': f'Server returned HTTP {status}'})
    except Exception as exc:
        current_app.logger.warning(f'Camera test-ping failed for {clean_url}: {exc}')
        return jsonify({
            'success': False,
            'message': 'Unable to reach the camera. Ensure the phone & PC are on the same Wi-Fi.'
        })


@cameras_bp.route('/delete/<int:camera_id>', methods=['POST'])
@login_required
def delete(camera_id):
    cam = CameraSource.query.filter_by(id=camera_id, teacher_id=current_user.id).first_or_404()
    was_active = cam.is_active
    db.session.delete(cam)
    db.session.commit()

    # If deleted camera was active, set another camera as active if available
    if was_active:
        first_remaining = CameraSource.query.filter_by(teacher_id=current_user.id).first()
        if first_remaining:
            first_remaining.is_active = True
            db.session.commit()

    flash(f"Camera '{cam.name}' removed successfully.", "info")
    return redirect(url_for('cameras.index'))
