"""Public landing page (v4).

The root URL '/' is the Attenova landing page — no login needed.
A teacher who is already logged in is sent straight to /dashboard
so the landing page never traps them.
"""
from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user

landing_bp = Blueprint('landing', __name__)


@landing_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    return render_template('landing.html')
