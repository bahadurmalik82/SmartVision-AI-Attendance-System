"""Backward compatibility (v4).

The old student portal lived at /portal/ (roll number + PIN). It is
replaced by the CMS ID + password student login at /student/login.
This blueprint only redirects the old URL so old bookmarks keep
working.
"""
from flask import Blueprint, redirect, url_for

student_portal_bp = Blueprint('student_portal', __name__, url_prefix='/portal')


@student_portal_bp.route('/')
def view():
    return redirect(url_for('student.login'))
