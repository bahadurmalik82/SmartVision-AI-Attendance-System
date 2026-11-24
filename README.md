# Attenova — AI-Powered Smart University Attendance & Duration Tracking System

**Attenova** is a full-stack, automated classroom attendance and
student-engagement monitoring web application, built for Sukkur IBA
University (CS-302, Machine Learning course outline).

Teachers register, log in, configure a weekly timetable, and connect any
classroom camera (laptop webcam, external USB camera, or a mobile phone
running the free **IP Webcam** app over Wi-Fi). An automated scheduler
(APScheduler) starts monitoring exactly when class starts and stops when
it ends, tracking active student presence in 10-minute blocks. Face
recognition (128-D HOG embeddings) identifies students, and the syllabus
ML models — **SVM, KNN, Random Forest, Logistic Regression** — are
benchmarked with 5-fold cross-validation, confusion matrices, precision,
recall and F1-score. **K-Means** clusters students into Star Attendees /
Inconsistent / At-Risk, and a Logistic Regression defaulter predictor
flags students at risk of falling below the **75% exam-eligibility**
threshold.

## Quick start (Windows)

```powershell
# 1. Python 3.10+ must be installed (tick "Add Python to PATH" during setup)
python --version

# 2. From this project folder, create and activate a virtual environment
python -m venv venv
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
python run.py
```

Open **http://127.0.0.1:5000** in your browser — you will see the
Attenova landing page. Choose **I am a Student** or **I am a Teacher**.

**First run:** there is no demo account any more. Register a new
teacher account on the site (open registration — every teacher is the
only owner of their own classes), then create a class and enrol
students into it (one by one, or by CSV on the Students page). Every
student gets a unique CMS ID (CMS-0001, CMS-0002, ...) and a random
password when enrolled or imported. The teacher can see both on the
Students page.

> ⚠️ **Student passwords are stored in plain text on purpose** (in
> the `portal_password_plain` column) so the teacher can tell a
> student who forgot their password what it is. They are shown ONLY
> to the student's own teacher, never to other students and never in
> logs. If you ever publish this project publicly, change this design
> first.
>
> There is no admin account any more — every teacher is the only
> owner of their own classes.

## What's new in v3

- **Usability (Floto audit):** one shared button system (5 styles),
  a clear gold-highlighted active sidebar item, no text smaller than
  12px, correct heading order, passive status badges, and a bigger
  "Manage" link.
- **Security:** CSRF protection on every form, no hard-coded secret
  key, login lockout after 5 failed attempts, per-teacher
  student/session ownership, student-portal PIN, safe login redirects,
  upload size/type checks, SSRF-safe camera test, security headers,
  password rules, change-password page, and a `.gitignore`.
- **Reliability:** file logging (`logs/attenova.log`), an audit trail
  on the Settings page, friendly 404/500 pages, stuck sessions from a
  crash are closed on startup, Waitress in production, and a
  one-click database backup on the Settings page.
- **Features:** manual attendance correction on Analytics, CSV bulk
  import + sample CSV, search + pagination on Students, student CSV
  export, a Settings page (threshold + late rule), a print report
  button, and a camera-disconnected alert after 3 failed frames.

## What's new in v4

- **No admin, no HOD.** Registration is open again — any teacher
  registers and is the only owner of their classes. The Admin page
  is deleted. (The unused `is_admin` database column is left in
  place so old databases keep working; nothing reads it.)
- **Landing page** (`/`) — animated navy/gold home page with
  "I am a Student" and "I am a Teacher" doors, features, how it
  works, and honest system facts. Logged-in teachers go straight to
  `/dashboard`.
- **Student CMS ID + password system** (replaces the v3 PIN portal):
  every student gets a unique CMS ID and a random password on
  enrolment or CSV import. Students log in at `/student/login` and
  see a full dashboard — overall %, per-subject cards with progress
  bars, how many classes to attend to reach the threshold (or how
  many they can miss and stay safe), every absence with date and
  class time, full history, and their weekly timetable. Students can
  change their own password (old password required); teachers can
  reset any student's password in one click and see every CMS ID +
  password on the Students page.
- **Migration (v3 → v4):** `students` gains `cms_id`,
  `portal_password_hash`, `portal_password_plain`. On first startup
  every existing student is backfilled — next free CMS-xxxx in
  student order, and a fresh random password — and the NULL counts
  are checked (must be zero) before the app serves requests.

## First run (no camera needed)

1. Open the landing page and click **I am a Teacher**, then register
   a teacher account. (There is no demo account — registration is open.)
2. **Dashboard** (`/dashboard`) — see your schedule and totals.
3. **Timetable** (`/timetable/`) — add your weekly classes.
4. **Students** (`/students/`) — create a class first, then enrol
   students into it (one by one or by CSV). Each student gets a CMS ID
   and password, shown on their class page.
5. **Start Class Now** on the Dashboard, then **Stop Class** — a session is
   created and finalised (with no camera, everyone is marked Absent for
   that session, which is expected — see Known limits).
6. **Analytics** (`/analytics/`) — filter by course / section / date /
   student, and see the **K-Means clustering** section (Star / Inconsistent /
   At-Risk). **Export Excel Report** downloads a real `.xlsx`.
7. **Defaulters** (`/analytics/defaulters`) — students below 75% with their
   cluster and ML risk label. **Export Defaulter List (Excel)** downloads
   the list. **Send Alert Email** works only if SMTP is configured in `.env`
   (otherwise it tells you so — it never crashes).
8. **ML Models** (`/analytics/benchmark`) — train the 4 syllabus classifiers
   from enrolled students, or from the `Celebrity Images` dataset folder
   if you add one.
9. **Student Portal** — click **I am a Student** on the landing
   page (or go to `/student/login`) and log in with a student's
   CMS ID + password (shown on their class page after enrolment).
   The old `/portal/` URL redirects to the new login. Students can
   also change their password from their dashboard.

## Live camera demo (optional)

- **Laptop / USB webcam:** open the Dashboard in the browser and allow
  camera access; frames are sent to `/process-frame`.
- **Mobile IP Webcam:** install the free IP Webcam app on your phone,
  connect phone and laptop to the **same Wi-Fi**, start the app, add the
  camera in **Cameras** (`/cameras/`) with the URL shown by the app,
  e.g. `http://192.168.1.15:8080/video`.
- A hosted server (e.g. Hostinger) cannot reach a USB camera plugged into
  your laptop — that is why the browser / IP-webcam modes exist.

## Project structure

```
run.py / wsgi.py            Entry points (local / Hostinger Passenger)
app/
  __init__.py               Flask app factory (starts the scheduler)
  config.py                 dev / testing / production config
  models.py                 Teacher, CameraSource, Schedule, Student,
                            AttendanceSession, AttendanceRecord
  routes/
    auth.py                 Login / register / logout
    dashboard.py            Dashboard, start/stop class, process-frame
    timetable.py            Weekly schedule CRUD
    cameras.py              Camera sources, IP-webcam test ping
    students.py             Enrollment, 3-angle webcam capture, blur check
    analytics.py            Analytics filters, K-Means clusters, defaulters,
                            Excel export, defaulter email, ML benchmark
    student_portal.py       Public roll-number self-check portal
  ml_engine/
    feature_extractor.py    128-D HOG embeddings, blur / lighting check
    classifier_benchmark.py SVM / KNN / Random Forest / Logistic Regression,
                            5-fold CV, confusion matrix
    duration_tracker.py     10-minute sampling buffer, Late / Present / Absent
    clustering.py           K-Means (k=3) student profiling + elbow method
    defaulter_predictor.py  Logistic Regression defaulter risk (+ rule fallback)
    liveness_detector.py    Anti-spoofing / blink check
  services/
    scheduler_service.py    APScheduler timetable daemon
    camera_streamer.py      IP-webcam + browser frame processing
    email_service.py        SMTP defaulter alerts (graceful if unset)
  templates/                Jinja2 + Tailwind pages
tests/
  test_phase1.py … test_phase8.py   Unit + route tests (see TEST-REPORT.md)
```

## ML models used (Sukkur IBA CS-302 outline)

| Task | Model |
|---|---|
| Face recognition | 128-D HOG embeddings + SVM (RBF/Linear), KNN, Random Forest, Logistic Regression |
| Model evaluation (Week 7) | Confusion matrix, precision, recall, F1, 5-fold cross-validation |
| Student profiling (Weeks 9–10) | K-Means clustering, k=3, elbow method |
| Early defaulter prediction (Weeks 5 & 13) | Logistic Regression on attendance aggregates; rule-based fallback (<70% barred, 70–74% warning) when fewer than 10 students are available |

Face recognition is **HOG-based, not dlib** — it runs on CPU with no GPU
and no heavy model downloads, which is what makes it deployable on
shared hosting.

## Running the tests

```powershell
venv\Scripts\activate
python -m unittest discover -s tests -v
```

## Known limits

- The IP-webcam phone and the laptop must be on the **same Wi-Fi**;
  a cloud server cannot see a classroom USB camera directly.
- Face recognition is HOG-based, so it works best with frontal, well-lit
  photos; it is a classroom demo, not a biometric security system.
- The defaulter predictor uses Logistic Regression only when 10+ students
  with both outcomes exist; with fewer students it uses the transparent
  75% rule and says so on the page.
- Email alerts need SMTP settings in `.env`; without them the button
  reports "email not configured" instead of sending.
- The scheduler runs inside the Flask process (APScheduler background
  thread); on multi-worker hosting it should be run as a single worker.
