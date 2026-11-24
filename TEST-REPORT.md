# TEST REPORT — ML Project Complete (Phases 6–8)

Date: Saturday 21 November 2026
Tested in: `~/workspace/ml-attendance/venv-test` (Python 3.12, fresh venv,
`pip install -r requirements.txt`), plus a live Flask server smoke test.

## Result summary

| Suite | Tests | Passed | Failed |
|---|---|---|---|
| Phase 1 (DB models, hashing, status calc) | 6 | 6 | 0 |
| Phase 2 (auth, timetable, cameras) | 7 | 7 | 0 |
| Phase 3 (enrollment, HOG embeddings, blur) | 6 | 6 | 0 |
| Phase 4 (SVM/KNN/RF/LR benchmark, 5-fold CV) | 5 | 5 | 0 |
| Phase 5 (duration tracker, scheduler, liveness) | 6 | 6 | 0 |
| Phase 6 (K-Means clustering, defaulter predictor) | 8 | 8 | 0 |
| Phase 7 (filters, Excel export, email, portal) | 12 | 12 | 0 |
| Phase 8 (scheduler guard, packaging, smoke test) | 12 | 12 | 0 |
| **Total** | **52** | **52** | **0** |

Command: `python -m unittest discover -s tests -v` → `Ran 52 tests — OK`
(about 58 seconds; most of the time is the Phase 4 benchmark training.)

## What Phase 6–8 added (and verified by the tests)

- **K-Means clustering** (`app/ml_engine/clustering.py`): k=3 on
  [attendance %, avg punctuality minutes], scaled, clusters re-labelled by
  mean attendance → Cluster 0 Star Attendees, 1 Inconsistent,
  2 At-Risk Defaulters. Elbow-method helper included. With <3 students or
  <3 distinct patterns it falls back to the 90%/75% rule and says so.
- **Defaulter predictor** (`app/ml_engine/defaulter_predictor.py`):
  Logistic Regression on [avg %, total classes, avg lateness, late count].
  With <10 students it falls back to the rule (<70% barred, 70–74%
  warning) and says so on the Defaulters page.
- **Analytics filters are real now**: course / section / date / student
  are a GET form and actually filter the table and the Excel export.
  Selected values stay selected after filtering.
- **Excel export is real**: `/analytics/export-excel` and
  `/analytics/defaulters/export-excel` download genuine `.xlsx` files
  (openpyxl, verified by reading the cells back in the tests).
- **Send Alert Email is real**: POST `/analytics/defaulters/email/<id>`
  uses `app/services/email_service.py` (SMTP from `.env`). With no SMTP
  configured it flashes "Email is not configured" and never 500s.
- **Student portal** (`/portal/`) verified end-to-end with seeded data.
- **Bugs fixed**: (1) `train_model()` used `np` without importing numpy —
  fixed, and a missing `Celebrity Images` folder now shows a clear
  message instead of crashing; (2) the scheduler is now started in
  `create_app()` but only when `TESTING` is False; (3) `scipy>=1.11.0`
  added to `requirements.txt` (it was imported but missing);
  (4) `email_service.py` created; (5) `finalize_session` with a
  0-minute session no longer divides by zero for detected students,
  and the scheduler tick is wrapped so an empty / not-ready DB only
  logs a warning.

## Live server smoke test (v8: no demo account any more)

```
python run.py           # FLASK_ENV=production, port 5001
/auth/login              -> 200
/ (logged out)           -> 302 to login
/portal/                 -> 200
login (registered teacher account) -> 200, dashboard shows Machine Learning
/analytics/              -> 200, shows Cluster 0 Star / 1 Inconsistent / 2 At-Risk
/analytics/defaulters    -> 200
/analytics/export-excel  -> 200, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, real .xlsx
```

Seeded data verified in SQLite: 1 teacher, 6 students, 1 schedule,
5 sessions, 30 records. Average attendance: K21-0145 95.6%, K21-0231
87.6%, K21-0456 81.6%, K21-0789 71.6%, K21-0901 63.6%, K21-1123 44.6%
(targets were 96/88/82/72/64/45%; the ±2% session jitter moves the
average by 0.4%).

## Notes / honest limits

- **Phase 4 test needed a dataset.** The original project had no
  `Celebrity Images` folder, so the pre-existing Phase 4 web test
  (`test_analytics_benchmark_web_routes`) could never pass — training
  raised "folder does not exist". A small **synthetic** sample dataset
  (3 classes × 10 generated pattern images, 240 KB) was added in
  `Celebrity Images/` with a README saying exactly that, so the
  benchmark runs out of the box. Replace it with real classroom photos
  (one subfolder per student) for a real demo.
- **Scheduler tick warning during tests.** The old Phase 1–5 tests
  create the app with the development config, so the scheduler starts
  and one tick fires against the in-memory test DB ("no such table").
  It is caught and logged, the app keeps running, and the Phase 8
  guard test proves a `testing` app never starts the scheduler.
  Harmless, but it is a warning in the test log, not a failure.
- **Live IP webcam was not tested in this Linux VM** (no camera, no
  phone on the same Wi-Fi here). The code path is the original Phase 5
  code and is unchanged; the demo in this report uses seeded data
  instead. Test the IP-webcam path on the laptop with phone + laptop
  on the same Wi-Fi before the viva demo.
- **Face recognition is HOG-based, not dlib** — CPU-only, works best
  with frontal, well-lit photos; a classroom demo, not a biometric
  security system.
- **Email needs SMTP settings in `.env`** (see `.env.example`); without
  them the button reports "not configured" instead of sending.
- **sklearn deprecation warnings** appear in the test log
  (`SVC(probability=True)` deprecated in sklearn 1.9, `datetime.utcnow()`
  deprecated). Warnings only — nothing fails.
- The shipped folder contains **no** `venv/`, `__pycache__`,
  `.env`, `attendance.db`, or trained `.pkl` model. After unzipping,
  register a teacher account on the site (there is no demo account).
