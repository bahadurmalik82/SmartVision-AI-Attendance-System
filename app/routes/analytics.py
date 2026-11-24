import io
import json
import os
from datetime import datetime

import numpy as np
from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, send_file, url_for)
from flask_login import current_user, login_required
from openpyxl import Workbook
from sqlalchemy import or_

from app import db
from app.ml_engine.classifier_benchmark import FaceClassifierBenchmark
from app.ml_engine.clustering import cluster_students
from app.ml_engine.defaulter_predictor import predict_defaulters
from app.models import AttendanceRecord, AttendanceSession, Schedule, Student
from app.services.audit import log_action
from app.services.email_service import send_defaulter_alert
from app.services.settings import get_late_threshold, get_threshold

analytics_bp = Blueprint('analytics', __name__, url_prefix='/analytics')

# Live threshold (from the Settings table, default 75). Kept as a
# module-level name for backwards compatibility; use get_threshold()
# inside request handlers so a changed setting takes effect at once.
THRESHOLD = 75.0


def _my_students_query():
    """My students, plus legacy rows with no owner yet."""
    return Student.query.filter(
        or_(Student.teacher_id == current_user.id,
            Student.teacher_id.is_(None)))


def _punctuality_minutes(record):
    """Minutes late for one record (0 if on time / unknown)."""
    if not record.first_seen or not record.session or not record.session.start_time:
        return 0.0
    try:
        delta = (record.first_seen - record.session.start_time).total_seconds() / 60.0
    except Exception:
        return 0.0
    return round(max(0.0, delta), 1)


def _record_matches(record, course=None, date=None):
    """True if a record belongs to the requested course / date filter."""
    if course and (not record.session or record.session.course_name != course):
        return False
    if date and (not record.session or record.session.session_date != date):
        return False
    return True


def get_student_aggregates(course=None, section=None, date=None, student_id=None):
    """Per-student attendance aggregates, honouring the active filters.

    Returns a list of dicts (one per matching student), each with:
    student, records (filtered), total_classes, avg_percentage,
    avg_punctuality_minutes, late_count.
    A student with no matching records is still listed with 0.0% when
    no course/date filter is active; with a course/date filter active
    they are omitted (they have no attendance in that slice).
    """
    query = _my_students_query().order_by(Student.roll_number)
    if section:
        query = query.filter(Student.section == section)
    if student_id:
        query = query.filter(Student.id == student_id)
    students = query.all()

    rows = []
    for student in students:
        records = [r for r in student.attendance_records
                   if _record_matches(r, course=course, date=date)]
        if (course or date) and not records:
            continue
        if records:
            avg_pct = round(sum(r.attendance_percentage for r in records) / len(records), 1)
            punct = [ _punctuality_minutes(r) for r in records ]
            avg_punct = round(sum(punct) / len(punct), 1)
            late_mins = get_late_threshold()
            late_count = sum(1 for r in records if r.status == 'Late'
                             or _punctuality_minutes(r) > late_mins)
        else:
            avg_pct, avg_punct, late_count = 0.0, 0.0, 0
        rows.append({
            'student': student,
            'records': records,
            'total_classes': len(records),
            'avg_percentage': avg_pct,
            'avg_punctuality_minutes': avg_punct,
            'late_count': late_count,
        })
    return rows


def _parse_filters():
    """Read the analytics filter form (GET params). Returns (course, section, date, student_id)."""
    course = request.args.get('course', '').strip() or None
    section = request.args.get('section', '').strip() or None
    date_str = request.args.get('date', '').strip() or None
    student_id = request.args.get('student_id', '').strip() or None
    parsed_date = None
    if date_str:
        try:
            parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            flash(f"Ignored invalid date '{date_str}' — use YYYY-MM-DD.", "warning")
            date_str = None
    parsed_student_id = None
    if student_id:
        try:
            parsed_student_id = int(student_id)
        except ValueError:
            flash(f"Ignored invalid student selection '{student_id}'.", "warning")
    return course, section, parsed_date, parsed_student_id, date_str


def _clusters_for(rows):
    """Run K-Means clustering on the aggregate rows (never crashes the page)."""
    try:
        return cluster_students([{
            'student_id': r['student'].id,
            'roll_number': r['student'].roll_number,
            'name': r['student'].name,
            'attendance_percentage': r['avg_percentage'],
            'avg_punctuality_minutes': r['avg_punctuality_minutes'],
        } for r in rows])
    except Exception as exc:
        current_app.logger.warning(f"Clustering failed: {exc}")
        return {'method': 'rule-based', 'note': f"Clustering unavailable: {exc}",
                'assignments': [], 'clusters': {}, 'scatter': [], 'elbow': [],
                'max_lateness': 10.0}


def _analytics_context():
    course, section, parsed_date, parsed_student_id, date_str = _parse_filters()
    rows = get_student_aggregates(course=course, section=section,
                                  date=parsed_date, student_id=parsed_student_id)
    courses = [c[0] for c in Schedule.query.filter_by(teacher_id=current_user.id).with_entities(Schedule.course_name).distinct().all()]
    # v8: section options come from the teacher's STUDENTS as well as
    # from schedules, so the filter values always match the roster.
    schedule_sections = {s[0] for s in Schedule.query.filter_by(teacher_id=current_user.id).with_entities(Schedule.section).distinct().all()}
    students = _my_students_query().order_by(Student.roll_number).all()
    sections = sorted({s.section for s in students if s.section} | {s for s in schedule_sections if s})
    clusters = _clusters_for(rows)
    # v8: never silently drop students. `rows` is the filtered slice;
    # `hidden_students` is everyone on the full roster who is NOT in
    # that slice, each with the reason they are hidden. Without a
    # course/date filter a student is only hidden by the section /
    # student filter; with a course/date filter they have no record
    # in that slice.
    shown_ids = {r['student'].id for r in rows}
    hidden_students = []
    for st in students:
        if st.id in shown_ids:
            continue
        if section and st.section != section:
            reason = 'hidden by the section filter'
        elif parsed_student_id and st.id != parsed_student_id:
            reason = 'hidden by the student filter'
        else:
            reason = 'no record for this course/date'
        hidden_students.append({'student': st, 'reason': reason})
    # Recent individual records (for manual correction) — my students only.
    my_ids = [st.id for st in students]
    recent_records = []
    if my_ids:
        recent_records = (AttendanceRecord.query
                          .filter(AttendanceRecord.student_id.in_(my_ids))
                          .order_by(AttendanceRecord.id.desc())
                          .limit(50).all())
    return {
        'rows': rows,
        'students': students,
        'total_students': len(students),
        'hidden_students': hidden_students,
        'filter_hides_records': bool(course or parsed_date),
        'courses': courses,
        'sections': sections,
        'clusters': clusters,
        'recent_records': recent_records,
        'threshold': get_threshold(),
        'selected_course': course or '',
        'selected_section': section or '',
        'selected_date': date_str or '',
        'selected_student_id': str(parsed_student_id) if parsed_student_id else '',
    }


@analytics_bp.route('/')
@login_required
def index():
    return render_template('analytics.html', **_analytics_context())


@analytics_bp.route('/clusters')
@login_required
def clusters():
    """Same analytics page, scrolled to the K-Means clustering section."""
    return render_template('analytics.html', **_analytics_context())


def _build_defaulters():
    """Defaulter list enriched with the ML predictor + cluster labels."""
    rows = get_student_aggregates()
    prediction = predict_defaulters([{
        'student_id': r['student'].id,
        'roll_number': r['student'].roll_number,
        'name': r['student'].name,
        'avg_percentage': r['avg_percentage'],
        'total_classes': r['total_classes'],
        'avg_punctuality_minutes': r['avg_punctuality_minutes'],
        'late_count': r['late_count'],
    } for r in rows])
    pred_by_id = {p['student_id']: p for p in prediction['predictions']}
    clusters = _clusters_for(rows)
    cluster_by_id = {a['student_id']: a for a in clusters['assignments']}

    threshold = get_threshold()
    warn_from = max(0.0, threshold - 5.0)
    defaulter_list = []
    for r in rows:
        if r['avg_percentage'] >= threshold:
            continue
        pred = pred_by_id.get(r['student'].id, {})
        cluster = cluster_by_id.get(r['student'].id, {})
        defaulter_list.append({
            'student': r['student'],
            'avg_percentage': r['avg_percentage'],
            'total_classes': r['total_classes'],
            'status': ('Barred from Exam' if r['avg_percentage'] < warn_from
                       else f'Warning ({warn_from:.0f}-{threshold:.0f}%)'),
            'risk_probability': pred.get('risk_probability'),
            'risk_label': pred.get('risk_label', ''),
            'cluster_label': cluster.get('cluster_label', 'At-Risk Defaulters'),
            'prediction_method': prediction['method'],
        })
    defaulter_list.sort(key=lambda d: d['avg_percentage'])
    return defaulter_list, prediction


@analytics_bp.route('/defaulters')
@login_required
def defaulters():
    defaulter_list, prediction = _build_defaulters()
    return render_template('defaulters.html', defaulters=defaulter_list,
                           prediction_method=prediction['method'],
                           prediction_note=prediction['note'],
                           threshold=get_threshold())


def _excel_response(filename, headers, data_rows):
    """Build a real .xlsx in memory and return it as a download."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(headers)
    for row in data_rows:
        ws.append(row)
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(width + 2, 40)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename,
    )


@analytics_bp.route('/export-excel')
@login_required
def export_excel():
    """Download the (filtered) attendance report as .xlsx."""
    course, section, parsed_date, parsed_student_id, _ = _parse_filters()
    rows = get_student_aggregates(course=course, section=section,
                                  date=parsed_date, student_id=parsed_student_id)
    data = [[r['student'].roll_number, r['student'].name, r['student'].section,
             r['student'].department, r['total_classes'], r['avg_percentage'],
             'Present' if r['avg_percentage'] >= get_threshold() else
             ('Warning' if r['avg_percentage'] >= max(0.0, get_threshold() - 5.0)
              else 'Barred from Exam')]
            for r in rows]
    return _excel_response(
        'attendance_report.xlsx',
        ['Roll No', 'Student Name', 'Section', 'Department',
         'Total Lectures', 'Aggregate Attendance %', 'Status'],
        data,
    )


@analytics_bp.route('/defaulters/export-excel')
@login_required
def export_defaulters_excel():
    """Download the <75% defaulter list as .xlsx."""
    defaulter_list, _ = _build_defaulters()
    data = [[d['student'].roll_number, d['student'].name, d['student'].department,
             d['total_classes'], d['avg_percentage'], d['status'],
             d.get('cluster_label', ''), d.get('risk_label', '')]
            for d in defaulter_list]
    return _excel_response(
        'defaulters_list.xlsx',
        ['Roll No', 'Student Name', 'Department', 'Total Classes',
         'Current Attendance %', 'Eligibility Status', 'Cluster', 'ML Risk'],
        data,
    )


@analytics_bp.route('/defaulters/email/<int:student_id>', methods=['POST'])
@login_required
def email_defaulter(student_id):
    """Send a warning email to one defaulter. Never 500s if email is unset."""
    student = _my_students_query().filter_by(id=student_id).first_or_404()
    rows = get_student_aggregates(student_id=student_id)
    avg_pct = rows[0]['avg_percentage'] if rows else 0.0
    try:
        sent, message = send_defaulter_alert(
            student.email, student.name, avg_pct,
            threshold=get_threshold())
    except Exception as exc:  # email must never break the dashboard
        current_app.logger.warning(f'Defaulter email failed: {exc}')
        sent, message = False, "Could not send email. Please try again later."
    flash(message, "success" if sent else "info")
    return redirect(url_for('analytics.defaulters'))


@analytics_bp.route('/correct/<int:record_id>', methods=['POST'])
@login_required
def correct_record(record_id):
    """Manually correct one attendance record.

    The teacher changes the status and minutes present and MUST give a
    reason. The record is marked as a manual override and the change is
    written to the audit log.
    """
    record = AttendanceRecord.query.get_or_404(record_id)
    # The record must belong to one of my students.
    my_ids = {s.id for s in _my_students_query().all()}
    if record.student_id not in my_ids:
        flash('That record does not belong to your students.', 'danger')
        return redirect(url_for('analytics.index'))

    status = request.form.get('status', '').strip()
    reason = request.form.get('reason', '').strip()
    try:
        minutes = float(request.form.get('minutes_present', ''))
    except (TypeError, ValueError):
        minutes = None

    if status not in ('Present', 'Late', 'Left Early', 'Absent'):
        flash('Please choose a valid status.', 'danger')
        return redirect(url_for('analytics.index'))
    if minutes is None or minutes < 0:
        flash('Minutes present must be a number (0 or more).', 'danger')
        return redirect(url_for('analytics.index'))
    if not reason:
        flash('A reason is required for a manual correction.', 'danger')
        return redirect(url_for('analytics.index'))

    total = record.session.total_duration_minutes if record.session else 0
    if not total:
        total = 100
    record.minutes_present = minutes
    record.status = status
    record.calculate_status(total, threshold=get_threshold())
    # The teacher's chosen status wins over the recalculated one.
    record.status = status
    record.manual_override = True
    record.override_reason = reason
    db.session.commit()
    log_action('attendance_correct',
               f'{record.student.roll_number} record #{record.id}: '
               f'{status}, {minutes} min — {reason}')
    flash(f"Record corrected for {record.student.name} "
          f"({record.student.roll_number}): {status}, {minutes} min.",
          'success')
    return redirect(url_for('analytics.index'))


@analytics_bp.route('/benchmark')
@login_required
def benchmark():
    """Displays the Course Outline Machine Learning Model Benchmark and Confusion Matrix."""
    report_path = os.path.join(current_app.config['SAVED_MODELS_FOLDER'], 'benchmark_report.json')
    report = None
    if os.path.exists(report_path):
        try:
            with open(report_path, 'r') as f:
                report = json.load(f)
        except Exception:
            report = None

    enrolled_count = _my_students_query().filter(Student.face_embedding_json.isnot(None)).count()
    return render_template('benchmark.html', report=report, enrolled_count=enrolled_count)


@analytics_bp.route('/train-model', methods=['POST'])
@login_required
def train_model():
    """Trains and benchmarks the 4 syllabus classifiers (SVM, KNN, RF, LR)."""
    source = request.form.get('source', 'dataset')  # 'enrolled' or 'dataset'
    benchmark_engine = FaceClassifierBenchmark(models_dir=current_app.config['SAVED_MODELS_FOLDER'])

    try:
        if source == 'enrolled':
            # Train from enrolled database students
            students = _my_students_query().filter(Student.face_embedding_json.isnot(None)).all()
            if len(students) < 2:
                flash("At least 2 students with enrolled face photos are required to train.", "warning")
                return redirect(url_for('analytics.benchmark'))

            X, y = [], []
            for s in students:
                emb = s.get_face_embedding()
                if emb:
                    X.append(emb)
                    y.append(f"{s.name} ({s.roll_number})")
            if len(set(y)) < 2:
                flash("At least 2 different students with enrolled face photos are required to train.", "warning")
                return redirect(url_for('analytics.benchmark'))
            X = np.array(X)
            y = np.array(y)
            results = benchmark_engine.train_and_benchmark(X, y)
        else:
            # Train from benchmark classroom dataset (Celebrity Images)
            dataset_path = os.path.join(current_app.root_path, '..', 'Celebrity Images')
            if not os.path.exists(dataset_path):
                dataset_path = 'Celebrity Images'
            if not os.path.isdir(dataset_path):
                flash(
                    "Dataset folder 'Celebrity Images' was not found in the project folder, "
                    "so the benchmark could not run. Enrol at least 2 students with face "
                    "photos and use 'Train from Enrolled Students' instead.",
                    "warning",
                )
                return redirect(url_for('analytics.benchmark'))
            X, y = benchmark_engine.load_dataset_from_folder(dataset_path, max_per_class=15)
            if len(X) == 0 or len(set(y.tolist())) < 2:
                flash("The 'Celebrity Images' folder has no usable images (need 2+ classes).", "warning")
                return redirect(url_for('analytics.benchmark'))
            results = benchmark_engine.train_and_benchmark(X, y)

        flash(f"ML Model training complete! Best Classifier: {results['best_model']}", "success")
    except Exception as exc:
        current_app.logger.warning(f'Model training failed: {exc}')
        flash("Training failed. Please check the dataset and try again.", "danger")

    return redirect(url_for('analytics.benchmark'))
