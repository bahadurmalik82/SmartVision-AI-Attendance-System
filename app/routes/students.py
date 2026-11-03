"""Student enrolment, face photos, CSV import/export (v3, classes in v5).

Ownership: a teacher only sees their own students. Rows created
before v3 have no teacher (teacher_id NULL) — those are treated as
legacy rows and stay visible to every teacher until the startup
backfill assigns them (see app/__init__.py).

v5 — classes: every student belongs to ONE class group
(department + semester + section). The Students page lists the
teacher's classes, not one giant mixed roster. Opening a class shows
only that class's students, on a page whose student list scrolls on
its own. Enrolling (manual or CSV) always picks a class first, so a
Section B class can never accidentally absorb Section C students —
and when a class runs, only its own students get attendance records
(see scheduler_service._session_students).

Upload safety: only real images (JPEG/PNG/WebP, checked with Pillow),
at most ~5 MB decoded per photo; errors shown to the user are generic
and the real exception is logged server-side.

Student portal credentials (v4): every student gets a unique CMS ID
(CMS-0001, CMS-0002, ...) and a random 8-character password when they
are enrolled or imported. The teacher can see both on this page (and
in the CSV export) so a student who forgets their password can ask
the teacher. The password is also stored as a bcrypt hash for login.
"""
import base64
import csv
import io
import os
import zipfile

from flask import (Blueprint, Response, current_app, flash, jsonify,
                   redirect, render_template, request, session as flask_session,
                   url_for)
from flask_login import current_user, login_required
from PIL import Image
from sqlalchemy import or_

from app import db
from app.models import (ClassGroup, Student, generate_student_password,
                        get_or_create_class_group, next_cms_id)
from app.ml_engine.feature_extractor import FaceFeatureExtractor
from app.services.audit import log_action

students_bp = Blueprint('students', __name__, url_prefix='/students')

ALLOWED_IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP'}
MAX_PHOTO_BYTES = 5 * 1024 * 1024  # ~5 MB decoded per photo
MAX_BULK_ZIP_BYTES = 50 * 1024 * 1024  # uncompressed ZIP total for bulk photos
PAGE_SIZE = 20


def _my_students_query():
    """Students owned by me, plus legacy rows with no owner yet."""
    return Student.query.filter(
        or_(Student.teacher_id == current_user.id,
            Student.teacher_id.is_(None)))


def _my_classes_query():
    return ClassGroup.query.filter_by(teacher_id=current_user.id)


def _get_my_class(class_id):
    return _my_classes_query().filter_by(id=class_id).first_or_404()


def _get_my_student(student_id):
    return _my_students_query().filter_by(id=student_id).first_or_404()


def _safe_return_to(default):
    """Only allow a return to a /students/... page, never elsewhere."""
    target = (request.form.get('return_to')
              or request.args.get('return_to') or '')
    if target.startswith('/students'):
        return target
    return default


def _open_valid_image(img_bytes, label):
    """Decode bytes into a PIL image, rejecting non-images.

    Returns (pil_image, error_message_or_None).
    """
    if len(img_bytes) > MAX_PHOTO_BYTES:
        return None, f'{label} is too large (max 5 MB).'
    try:
        pil_img = Image.open(io.BytesIO(img_bytes))
        if pil_img.format not in ALLOWED_IMAGE_FORMATS:
            return None, (f'{label} must be a JPG, PNG or WebP image '
                          f'(got {pil_img.format or "unknown"}).')
        pil_img.load()  # forces a full decode — catches corrupt files
        return pil_img.convert('RGB'), None
    except Exception as exc:
        current_app.logger.warning(f'Image validation failed for {label}: {exc}')
        return None, f'{label} is not a valid image file.'


def _class_for_enrolment(form):
    """The class a new student should go into.

    Preferred: the class the teacher picked in the form. Fallback
    (old posts / CSV rows without a class): find or create a class
    from the department / semester / section fields, so enrolment
    never leaves a student class-less.
    """
    raw_id = (form.get('class_group_id') or '').strip()
    if raw_id:
        try:
            group = ClassGroup.query.filter_by(
                id=int(raw_id), teacher_id=current_user.id).first()
        except ValueError:
            group = None
        if group is not None:
            return group
    department = (form.get('department') or 'Computer Science').strip()
    semester = (form.get('semester') or '5th').strip()
    section = (form.get('section') or 'Section B').strip()
    return get_or_create_class_group(
        current_user.id, department, semester, section)


# ----------------------------------------------------------------------
# Classes: list / create / edit / delete
# ----------------------------------------------------------------------
@students_bp.route('/')
@login_required
def index():
    classes = _my_classes_query().order_by(ClassGroup.name).all()
    stats = {}
    for group in classes:
        students = Student.query.filter_by(class_group_id=group.id).all()
        stats[group.id] = {
            'total': len(students),
            'faces': sum(1 for s in students if s.face_embedding_json),
        }
    unassigned = _my_students_query().filter(
        Student.class_group_id.is_(None)).count()
    return render_template('enrollment.html', classes=classes, stats=stats,
                           unassigned=unassigned)


@students_bp.route('/classes/create', methods=['POST'])
@login_required
def create_class():
    name = request.form.get('name', '').strip()
    department = request.form.get('department', '').strip()
    semester = request.form.get('semester', '').strip()
    section = request.form.get('section', '').strip()
    course_name = request.form.get('course_name', '').strip()
    course_code = request.form.get('course_code', '').strip().upper()

    if not department or not semester or not section:
        flash('Department, Semester and Section are all required to create a class.', 'danger')
        return redirect(url_for('students.index'))

    existing = ClassGroup.query.filter_by(
        teacher_id=current_user.id, department=department,
        semester=semester, section=section).first()
    if existing:
        flash(f'That class already exists: {existing.display_name()}. '
              f'Enrol students into it instead of creating a duplicate.', 'warning')
        return redirect(url_for('students.index'))

    group = ClassGroup(
        teacher_id=current_user.id,
        name=name or f'{department} · {section} · Semester {semester}',
        course_name=course_name or None,
        course_code=course_code or None,
        department=department, semester=semester, section=section)
    db.session.add(group)
    db.session.commit()
    log_action('class_create', f'Class created: {group.display_name()}.')
    flash(f'Class created: {group.display_name()}. Now enrol students into it '
          f'using the Register form or the CSV import below.', 'success')
    return redirect(url_for('students.index'))


@students_bp.route('/classes/<int:class_id>/edit', methods=['POST'])
@login_required
def edit_class(class_id):
    group = _get_my_class(class_id)
    name = request.form.get('name', '').strip()
    department = request.form.get('department', '').strip()
    semester = request.form.get('semester', '').strip()
    section = request.form.get('section', '').strip()
    course_name = request.form.get('course_name', '').strip()
    course_code = request.form.get('course_code', '').strip().upper()
    if not department or not semester or not section:
        flash('Department, Semester and Section are all required.', 'danger')
        return redirect(url_for('students.class_detail', class_id=class_id))

    # Editing fixes a typo in THIS class. If the class has moved to a
    # new semester, the teacher should create a NEW class instead —
    # the class page says so — so last semester's history stays intact.
    group.name = name or f'{department} · {section} · Semester {semester}'
    group.department, group.semester, group.section = department, semester, section
    group.course_name = course_name or None
    group.course_code = course_code or None
    # Keep the students' stored fields in step with their class.
    for student in group.students:
        student.department, student.semester, student.section = department, semester, section
    db.session.commit()
    log_action('class_edit', f'Class updated: {group.display_name()}.')
    flash(f'Class updated: {group.display_name()}.', 'success')
    return redirect(url_for('students.class_detail', class_id=class_id))


@students_bp.route('/classes/<int:class_id>/delete', methods=['POST'])
@login_required
def delete_class(class_id):
    group = _get_my_class(class_id)
    total = len(group.students)
    name = group.display_name()
    # Cascade (see models.ClassGroup): deleting the class deletes its
    # students, their attendance records, and its timetable entries.
    db.session.delete(group)
    db.session.commit()
    log_action('class_delete', f'Class deleted: {name} ({total} students).')
    flash(f'Class {name} deleted completely, including its {total} student(s) '
          f'and their attendance records.', 'info')
    return redirect(url_for('students.index'))


# ----------------------------------------------------------------------
# One class, its own student list (v5)
# ----------------------------------------------------------------------
@students_bp.route('/class/<int:class_id>')
@login_required
def class_detail(class_id):
    group = _get_my_class(class_id)
    q = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    query = Student.query.filter_by(class_group_id=group.id).order_by(Student.roll_number)
    if q:
        like = f'%{q}%'
        query = query.filter(or_(Student.roll_number.ilike(like),
                                 Student.name.ilike(like)))
    pagination = query.paginate(page=page, per_page=PAGE_SIZE, error_out=False)
    # Bulk face-enrolment results, if the teacher just uploaded a ZIP.
    bulk_results = flask_session.pop('bulk_photos_results', None)
    if bulk_results and bulk_results.get('class_id') != group.id:
        bulk_results = None
    return render_template('class_detail.html', group=group,
                           students=pagination.items,
                           pagination=pagination, q=q,
                           bulk_results=bulk_results)


# ----------------------------------------------------------------------
# Enrolment (manual + CSV) — always into a specific class
# ----------------------------------------------------------------------
@students_bp.route('/enroll', methods=['POST'])
@login_required
def enroll():
    roll_number = request.form.get('roll_number', '').strip().upper()
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()

    if not roll_number or not name:
        flash("Roll Number and Full Name are required.", "danger")
        return redirect(_safe_return_to(url_for('students.index')))

    existing = Student.query.filter_by(roll_number=roll_number).first()
    if existing:
        flash(f"Student with Roll No '{roll_number}' is already registered.", "warning")
        return redirect(_safe_return_to(url_for('students.index')))

    group = _class_for_enrolment(request.form)
    password = generate_student_password()
    new_student = Student(
        roll_number=roll_number,
        name=name,
        department=group.department,
        semester=group.semester,
        section=group.section,
        email=email,
        teacher_id=current_user.id,
        class_group_id=group.id,
        cms_id=next_cms_id(),
    )
    new_student.set_portal_password(password)
    db.session.add(new_student)
    db.session.commit()
    log_action('student_add', f'{roll_number} — {name} ({new_student.cms_id}) into {group.display_name()}.')
    flash(f"Student '{name} ({roll_number})' registered into {group.display_name()}! "
          f"CMS ID: {new_student.cms_id} — Password: {password}. Share these with "
          f"the student so they can log in on the Student Portal "
          f"(shown here and on the Students page). Now capture their "
          f"face photos below.", "success")
    return redirect(_safe_return_to(url_for('students.index')))


@students_bp.route('/reset-password/<int:student_id>', methods=['POST'])
@login_required
def reset_password(student_id):
    """Teacher resets a student's portal password (e.g. they forgot it).

    Generates a new random password, shows it once in the flash message,
    and logs the reset. The student uses the new password immediately;
    the old one stops working.
    """
    student = _get_my_student(student_id)
    new_password = generate_student_password()
    student.set_portal_password(new_password)
    db.session.commit()
    log_action('student_password_reset',
               f'Password reset for {student.roll_number} '
               f'({student.cms_id}).')
    flash(f"New password for {student.name} ({student.cms_id}): "
          f"{new_password}. Share it with the student — the old password "
          f"no longer works.", "success")
    return redirect(_safe_return_to(url_for('students.index')))


@students_bp.route('/upload-photos/<int:student_id>', methods=['POST'])
@login_required
def upload_photos(student_id):
    """
    Receives face photos via Base64 (from webcam) or file upload.
    Validates sharpness/blur and computes 128-D face embedding vector.
    """
    student = _get_my_student(student_id)
    embeddings = []
    processed_count = 0

    # 1. Check for Base64 frames from interactive webcam (e.g. angle1, angle2, angle3)
    data = request.get_json(silent=True)
    if data and 'images' in data:
        base64_list = data['images']  # Array of base64 data URLs
        for idx, b64_str in enumerate(base64_list):
            try:
                # Remove header like 'data:image/jpeg;base64,'
                if ',' in b64_str:
                    b64_str = b64_str.split(',')[1]
                if len(b64_str) > 7_000_000:  # ~5 MB decoded
                    return jsonify({'success': False,
                                    'message': f'Frame {idx + 1} is too large (max 5 MB).'}), 400
                img_bytes = base64.b64decode(b64_str, validate=True)
                pil_img, err = _open_valid_image(img_bytes, f'Frame {idx + 1}')
                if err:
                    return jsonify({'success': False, 'message': err}), 400

                # Quality / Blur check
                is_valid, score, msg = FaceFeatureExtractor.check_image_quality(pil_img)
                if not is_valid:
                    return jsonify({'success': False, 'message': f"Frame {idx+1}: {msg}"}), 400

                emb = FaceFeatureExtractor.extract_face_embedding(pil_img)
                embeddings.append(emb)

                # Save the primary front picture as reference
                if idx == 0:
                    photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{student.roll_number}.jpg")
                    pil_img.save(photo_path, "JPEG", quality=90)

                processed_count += 1
            except Exception as exc:
                current_app.logger.warning(
                    f'Photo upload failed for student {student.roll_number}: {exc}')
                return jsonify({'success': False,
                                'message': f'Could not process frame {idx + 1}. '
                                           'Please retake the photo.'}), 400

    # 2. Check for traditional multipart file uploads
    elif 'photos' in request.files:
        files = request.files.getlist('photos')
        for idx, file in enumerate(files):
            if file and file.filename:
                try:
                    img_bytes = file.read()
                    pil_img, err = _open_valid_image(img_bytes, f"File '{file.filename}'")
                    if err:
                        flash(err, "danger")
                        return redirect(_safe_return_to(url_for('students.index')))

                    is_valid, score, msg = FaceFeatureExtractor.check_image_quality(pil_img)
                    if not is_valid:
                        flash(f"File '{file.filename}': {msg}", "danger")
                        return redirect(_safe_return_to(url_for('students.index')))

                    emb = FaceFeatureExtractor.extract_face_embedding(pil_img)
                    embeddings.append(emb)

                    if idx == 0:
                        photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{student.roll_number}.jpg")
                        pil_img.save(photo_path, "JPEG", quality=90)

                    processed_count += 1
                except Exception as exc:
                    current_app.logger.warning(
                        f'Photo upload failed for student {student.roll_number}: {exc}')
                    flash("Could not read that image. Please upload a JPG, "
                          "PNG or WebP photo.", "danger")
                    return redirect(_safe_return_to(url_for('students.index')))

    if not embeddings:
        if request.is_json:
            return jsonify({'success': False, 'message': 'No valid face photos received.'}), 400
        flash("No valid photos uploaded.", "warning")
        return redirect(_safe_return_to(url_for('students.index')))

    # Average embeddings into master profile and save to DB
    master_embedding = FaceFeatureExtractor.average_embeddings(embeddings)
    student.set_face_embedding(master_embedding)
    db.session.commit()

    if request.is_json:
        return jsonify({
            'success': True,
            'message': f"Successfully enrolled {processed_count} photo angles for {student.name}!",
            'roll_number': student.roll_number
        })

    flash(f"Successfully generated 128-D face embedding from {processed_count} photos for {student.name}!", "success")
    return redirect(_safe_return_to(url_for('students.index')))


@students_bp.route('/delete/<int:student_id>', methods=['POST'])
@login_required
def delete(student_id):
    student = _get_my_student(student_id)
    # Remove saved photo if exists
    photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{student.roll_number}.jpg")
    if os.path.exists(photo_path):
        try:
            os.remove(photo_path)
        except OSError:
            pass

    db.session.delete(student)
    db.session.commit()
    log_action('student_delete', f'{student.roll_number} — {student.name}.')
    flash(f"Student record '{student.roll_number}' removed successfully.", "info")
    return redirect(_safe_return_to(url_for('students.index')))


@students_bp.route('/import/sample')
@login_required
def import_sample():
    """Download a blank CSV template for bulk import."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['roll_number', 'name', 'department', 'semester',
                     'section', 'email', 'password'])
    writer.writerow(['K21-0145', 'Ahmed Ali', 'Computer Science', '5th',
                     'Section B', 'ahmed.ali@university.edu', ''])
    writer.writerow(['K21-0231', 'Sara Ahmed', 'Computer Science', '5th',
                     'Section B', 'sara.ahmed@university.edu', ''])
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':
                             'attachment; filename=students-sample.csv'})


@students_bp.route('/import', methods=['POST'])
@login_required
def import_csv():
    """Bulk-import students from a CSV file, into one chosen class.

    Columns: roll_number, name, department, semester, section, email
    (+ optional password — blank means a random password is generated).
    Every imported student gets a unique CMS ID + password. Duplicates
    (roll number already exists) are skipped and counted in the summary.
    When the teacher picked a class on the Students page, every row
    goes into that class (the row's department / semester / section
    are ignored); without a class, each row's own fields decide the
    class, as before.
    """
    file = request.files.get('csv_file')
    if not file or not file.filename:
        flash('Please choose a CSV file to import.', 'warning')
        return redirect(_safe_return_to(url_for('students.index')))
    if not file.filename.lower().endswith('.csv'):
        flash('Only .csv files can be imported.', 'danger')
        return redirect(_safe_return_to(url_for('students.index')))

    chosen_group = None
    raw_id = (request.form.get('class_group_id') or '').strip()
    if raw_id:
        try:
            chosen_group = ClassGroup.query.filter_by(
                id=int(raw_id), teacher_id=current_user.id).first()
        except ValueError:
            chosen_group = None
        if chosen_group is None:
            flash('That class was not found. Please choose a class again.', 'danger')
            return redirect(_safe_return_to(url_for('students.index')))

    try:
        text = io.TextIOWrapper(file.stream, encoding='utf-8-sig')
        reader = csv.DictReader(text)
        if not reader.fieldnames or 'roll_number' not in reader.fieldnames \
                or 'name' not in reader.fieldnames:
            flash('CSV must have at least roll_number and name columns. '
                  'Download the sample CSV to see the format.', 'danger')
            return redirect(_safe_return_to(url_for('students.index')))

        imported, skipped = 0, 0
        credentials = []
        for row in reader:
            roll = (row.get('roll_number') or '').strip().upper()
            name = (row.get('name') or '').strip()
            if not roll or not name:
                skipped += 1
                continue
            if Student.query.filter_by(roll_number=roll).first():
                skipped += 1
                continue
            password = (row.get('password') or '').strip()
            if len(password) < 4:
                password = generate_student_password()
            if chosen_group is not None:
                group = chosen_group
            else:
                group = get_or_create_class_group(
                    current_user.id,
                    (row.get('department') or 'Computer Science').strip(),
                    (row.get('semester') or '5th').strip(),
                    (row.get('section') or 'Section B').strip())
            cms_id = next_cms_id()
            student = Student(
                roll_number=roll, name=name,
                department=group.department,
                semester=group.semester,
                section=group.section,
                email=(row.get('email') or '').strip().lower() or None,
                teacher_id=current_user.id,
                class_group_id=group.id,
                cms_id=cms_id)
            student.set_portal_password(password)
            db.session.add(student)
            db.session.flush()  # make cms_id visible to next_cms_id()
            credentials.append((roll, cms_id, password))
            imported += 1
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning(f'CSV import failed: {exc}')
        flash('Could not read that CSV file. Check the format against '
              'the sample CSV.', 'danger')
        return redirect(_safe_return_to(url_for('students.index')))

    log_action('students_import',
               f'{imported} imported, {skipped} skipped by {current_user.email}.')
    if credentials:
        creds_text = '; '.join(f'{r} — {c} / {p}'
                               for r, c, p in credentials)
        flash(f'Import finished: {imported} student(s) imported, {skipped} '
              f'skipped (already exist or missing roll number / name). '
              f'New portal credentials — {creds_text}. '
              f'CMS IDs and passwords are also shown on the Students page.',
              'success')
    else:
        flash(f'Import finished: {imported} student(s) imported, {skipped} '
              'skipped (already exist or missing roll number / name).',
              'warning')
    return redirect(_safe_return_to(url_for('students.index')))


@students_bp.route('/export')
@login_required
def export_csv():
    """Download my students as a CSV file (one class, or all)."""
    class_id = request.args.get('class_id', '').strip()
    query = _my_students_query()
    if class_id:
        try:
            group = _get_my_class(int(class_id))
            query = query.filter(Student.class_group_id == group.id)
        except ValueError:
            pass
    students = query.order_by(Student.roll_number).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['roll_number', 'name', 'department', 'semester',
                     'section', 'email', 'cms_id', 'password'])
    for s in students:
        writer.writerow([s.roll_number, s.name, s.department, s.semester,
                         s.section, s.email or '', s.cms_id or '',
                         s.portal_password_plain or ''])
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':
                             'attachment; filename=students.csv'})


# ----------------------------------------------------------------------
# Bulk face enrolment (v5): one ZIP with photos.csv + 3 photos per
# student. A student is only enrolled when all 3 angles are present,
# valid and sharp enough. Any problem is reported per student, with
# what to do about it — and the other students still get enrolled.
# ----------------------------------------------------------------------
@students_bp.route('/class/<int:class_id>/bulk-photos/sample')
@login_required
def bulk_photos_sample(class_id):
    """Download photos.csv for this class, pre-filled with its students
    and the photo filenames to use (e.g. 023-24-0001_front.jpg)."""
    group = _get_my_class(class_id)
    students = Student.query.filter_by(class_group_id=group.id).order_by(Student.roll_number).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['roll_number', 'front', 'left', 'right'])
    for s in students:
        writer.writerow([s.roll_number,
                         f'{s.roll_number}_front.jpg',
                         f'{s.roll_number}_left.jpg',
                         f'{s.roll_number}_right.jpg'])
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':
                             'attachment; filename=photos.csv'})


def _bulk_photo_failure(roll, name, issue, solution):
    return {'roll_number': roll, 'name': name or '',
            'issue': issue, 'solution': solution}


@students_bp.route('/class/<int:class_id>/bulk-photos', methods=['POST'])
@login_required
def bulk_photos(class_id):
    group = _get_my_class(class_id)
    file = request.files.get('zip_file')
    if not file or not file.filename:
        flash('Please choose a ZIP file to upload.', 'warning')
        return redirect(url_for('students.class_detail', class_id=class_id))
    if not file.filename.lower().endswith('.zip'):
        flash('Only .zip files can be used for bulk face enrolment.', 'danger')
        return redirect(url_for('students.class_detail', class_id=class_id))

    try:
        zip_bytes = file.read()
    except Exception:
        flash('Could not read that ZIP file.', 'danger')
        return redirect(url_for('students.class_detail', class_id=class_id))

    enrolled, failed = [], []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            # Safety: no absolute paths, no "..", and a sane total size.
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if sum(i.file_size for i in infos) > MAX_BULK_ZIP_BYTES:
                flash('That ZIP is too large (over 50 MB uncompressed). '
                      'Use smaller photos, or upload in two batches.', 'danger')
                return redirect(url_for('students.class_detail', class_id=class_id))
            by_name = {}
            for info in infos:
                name = info.filename.replace('\\', '/').split('/')[-1]
                if not name or name.startswith('.'):
                    continue
                if '..' in info.filename or info.filename.startswith('/'):
                    continue
                by_name.setdefault(name.lower(), info)

            csv_info = next((i for n, i in by_name.items()
                             if n.endswith('.csv')), None)
            if csv_info is None:
                flash('No photos.csv found inside the ZIP. Download the '
                      'sample photos.csv from this page, fill it in, and '
                      'put it in the ZIP with the photos.', 'danger')
                return redirect(url_for('students.class_detail', class_id=class_id))
            try:
                csv_text = zf.read(csv_info).decode('utf-8-sig')
            except Exception:
                flash('photos.csv inside the ZIP could not be read. '
                      'Save it as UTF-8 CSV and try again.', 'danger')
                return redirect(url_for('students.class_detail', class_id=class_id))
            reader = csv.DictReader(io.StringIO(csv_text))
            if not reader.fieldnames or 'roll_number' not in reader.fieldnames \
                    or not {'front', 'left', 'right'} <= set(reader.fieldnames):
                flash('photos.csv must have roll_number, front, left and '
                      'right columns. Download the sample to see the format.',
                      'danger')
                return redirect(url_for('students.class_detail', class_id=class_id))

            seen_rolls = set()
            for row in reader:
                roll = (row.get('roll_number') or '').strip().upper()
                if not roll:
                    failed.append(_bulk_photo_failure(
                        '—', '',
                        'A row in photos.csv has no roll number.',
                        'Fill in the roll number for that row and re-upload.'))
                    continue
                if roll in seen_rolls:
                    failed.append(_bulk_photo_failure(
                        roll, '',
                        'This roll number appears twice in photos.csv.',
                        'Keep only one row for this student and re-upload.'))
                    continue
                seen_rolls.add(roll)
                student = Student.query.filter_by(roll_number=roll).first()
                if student is None or student.teacher_id not in (current_user.id, None):
                    failed.append(_bulk_photo_failure(
                        roll, '',
                        'This student is not enrolled yet.',
                        'Enrol the student first (Register form or CSV import on the Students page), then re-upload this ZIP.'))
                    continue
                if student.class_group_id != group.id:
                    failed.append(_bulk_photo_failure(
                        roll, student.name,
                        f'This student is in a different class ({student.department} · {student.section} · Semester {student.semester}), not this one.',
                        'Upload their photos from their own class page, or move the student to this class first.'))
                    continue
                # The 3 photo filenames for this student.
                wanted = {}
                missing = []
                for angle in ('front', 'left', 'right'):
                    fname = (row.get(angle) or '').strip()
                    if not fname:
                        missing.append(f'{angle} (no filename in photos.csv)')
                    elif fname.lower() not in by_name:
                        missing.append(f'{angle}: {fname} (not in the ZIP)')
                    else:
                        wanted[angle] = fname.lower()
                if missing:
                    failed.append(_bulk_photo_failure(
                        roll, student.name,
                        'Missing photo(s): ' + '; '.join(missing) + '.',
                        'Add the missing photo file(s) to the ZIP with exactly the names in photos.csv, then re-upload.'))
                    continue
                # Validate + extract all 3 angles.
                embeddings = []
                bad = None
                for angle in ('front', 'left', 'right'):
                    try:
                        img_bytes = zf.read(by_name[wanted[angle]])
                    except Exception:
                        bad = f'Could not read {wanted[angle]} from the ZIP.'
                        break
                    pil_img, err = _open_valid_image(img_bytes, wanted[angle])
                    if err:
                        bad = err
                        break
                    is_valid, _score, msg = FaceFeatureExtractor.check_image_quality(pil_img)
                    if not is_valid:
                        bad = f'{angle} photo: {msg}'
                        break
                    embeddings.append(FaceFeatureExtractor.extract_face_embedding(pil_img))
                    if angle == 'front':
                        try:
                            photo_path = os.path.join(
                                current_app.config['UPLOAD_FOLDER'],
                                f"{student.roll_number}.jpg")
                            pil_img.save(photo_path, "JPEG", quality=90)
                        except Exception:
                            pass
                if bad:
                    failed.append(_bulk_photo_failure(
                        roll, student.name, bad,
                        'Retake that photo (good light, hold still, face inside the frame) and re-upload.'))
                    continue
                master = FaceFeatureExtractor.average_embeddings(embeddings)
                student.set_face_embedding(master)
                enrolled.append({'roll_number': roll, 'name': student.name})
        db.session.commit()
    except zipfile.BadZipFile:
        flash('That file is not a valid ZIP. Please zip the photos.csv and '
              'photos together and try again.', 'danger')
        return redirect(url_for('students.class_detail', class_id=class_id))
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning(f'Bulk face enrolment failed: {exc}')
        flash('Could not process that ZIP. Check the format against the '
              'sample photos.csv and try again.', 'danger')
        return redirect(url_for('students.class_detail', class_id=class_id))

    flask_session['bulk_photos_results'] = {
        'class_id': group.id,
        'enrolled': enrolled,
        'failed': failed,
        'total': len(enrolled) + len(failed),
    }
    log_action('bulk_photos',
               f'{len(enrolled)} face-enrolled, {len(failed)} failed in '
               f'{group.display_name()}.')
    if failed:
        flash(f'Bulk face enrolment finished: {len(enrolled)} student(s) '
              f'enrolled, {len(failed)} had a problem — see the list below '
              f'for each one and how to fix it.', 'warning')
    else:
        flash(f'Bulk face enrolment finished: {len(enrolled)} student(s) '
              f'enrolled with all 3 angles.', 'success')
    return redirect(url_for('students.class_detail', class_id=class_id))
