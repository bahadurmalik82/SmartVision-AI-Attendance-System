"""Phase 7 tests: real analytics filters, Excel export, defaulter email,
student portal (login required where the app requires it)."""
import io
import unittest
from datetime import date, datetime, time, timedelta

from openpyxl import load_workbook

from app import create_app, db
from app.models import (AttendanceRecord, AttendanceSession, Schedule,
                        Student, Teacher)


def _xlsx_values(data):
    """Read cell values out of a .xlsx download (raw bytes are compressed)."""
    wb = load_workbook(filename=io.BytesIO(data))
    ws = wb.active
    return [[c.value for c in row] for row in ws.iter_rows()]


def _flat(values):
    return [v for row in values for v in row]


# The analytics page also lists every student in the filter dropdown
# and in the cluster member lists, so a filtered-out student must be
# checked by its table row, not by a plain substring search.
ROW_CELL = b'<td class="p-3.5 font-semibold text-slate-800">Ahmed Ali</td>'


class Phase7TestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        teacher = Teacher(name="Demo Teacher", email="demo@iba-suk.edu.pk",
                          department="Computer Science")
        teacher.set_password("Demo1234")
        db.session.add(teacher)
        db.session.commit()
        self.teacher = teacher

        self.schedule = Schedule(
            teacher_id=teacher.id, course_name="Machine Learning",
            course_code="CS-302", section="Section B",
            day_of_week="Monday", start_time=time(9, 0), end_time=time(10, 40))
        db.session.add(self.schedule)
        db.session.commit()

        # Two students, different sections / attendance levels.
        self.ali = self._add_student("K21-0145", "Ahmed Ali", "Section B", 96.0)
        self.usman = self._add_student("K21-1123", "Usman Tariq", "Section A", 45.0)

    def _add_student(self, roll, name, section, pct):
        student = Student(roll_number=roll, name=name, email=f"{roll}@iba-suk.edu.pk",
                          department="Computer Science", semester="5th", section=section,
                          cms_id='CMS-0001' if roll == 'K21-0145' else 'CMS-0002')
        student.set_portal_password('StudPass1')
        db.session.add(student)
        db.session.commit()
        session = AttendanceSession(
            schedule_id=self.schedule.id, course_name="Machine Learning",
            section=section, session_date=date.today(),
            start_time=datetime.combine(date.today(), time(9, 0)),
            end_time=datetime.combine(date.today(), time(10, 40)),
            is_active=False, total_duration_minutes=100)
        db.session.add(session)
        db.session.commit()
        record = AttendanceRecord(session_id=session.id, student_id=student.id,
                                  first_seen=datetime.combine(date.today(), time(9, 2)),
                                  last_seen=datetime.combine(date.today(), time(10, 30)),
                                  minutes_present=pct)
        record.calculate_status(100)
        record.attendance_percentage = pct
        db.session.add(record)
        db.session.commit()
        return student

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self):
        return self.client.post('/auth/login', data={
            'email': 'demo@iba-suk.edu.pk', 'password': 'Demo1234',
        }, follow_redirects=True)

    # --- Analytics filters ---
    def test_analytics_requires_login(self):
        resp = self.client.get('/analytics/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/login', resp.headers['Location'])

    def test_analytics_page_shows_students_and_clusters(self):
        self._login()
        resp = self.client.get('/analytics/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Ahmed Ali', resp.data)
        self.assertIn(b'Usman Tariq', resp.data)
        self.assertIn(b'K-Means', resp.data)
        self.assertIn(b'At-Risk', resp.data)

    def test_analytics_filter_by_section(self):
        self._login()
        resp = self.client.get('/analytics/?section=Section+A')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Usman Tariq', resp.data)
        self.assertNotIn(ROW_CELL, resp.data)

    def test_analytics_filter_by_student(self):
        self._login()
        resp = self.client.get(f'/analytics/?student_id={self.usman.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Usman Tariq', resp.data)
        self.assertNotIn(ROW_CELL, resp.data)

    def test_analytics_filter_by_date_no_match(self):
        self._login()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        resp = self.client.get(f'/analytics/?date={yesterday}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'No student records found', resp.data)

    # --- Excel export ---
    def test_export_excel_returns_real_xlsx(self):
        self._login()
        resp = self.client.get('/analytics/export-excel')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheetml', resp.headers['Content-Type'])
        self.assertTrue(resp.data[:2] == b'PK')  # xlsx is a zip file
        values = _flat(_xlsx_values(resp.data))
        self.assertIn('Ahmed Ali', values)
        self.assertIn('Usman Tariq', values)

    def test_export_excel_respects_filter(self):
        self._login()
        resp = self.client.get('/analytics/export-excel?section=Section+A')
        self.assertEqual(resp.status_code, 200)
        values = _flat(_xlsx_values(resp.data))
        self.assertIn('Usman Tariq', values)
        self.assertNotIn('Ahmed Ali', values)

    # --- Defaulters ---
    def test_defaulters_page_lists_only_below_75(self):
        self._login()
        resp = self.client.get('/analytics/defaulters')
        self.assertEqual(resp.status_code, 200)
        # v9.1: the page has TWO sections now — the at-risk table still
        # lists only below-threshold students, and eligible students
        # appear in the Non-defaulters section below it. That is the
        # deliberate Change 2(a): Ahmed Ali belongs in the second
        # section now, not nowhere.
        at_risk, _, non_defaulters = resp.data.partition(b'Non-defaulters')
        self.assertIn(b'Usman Tariq', at_risk)
        self.assertNotIn(b'Ahmed Ali', at_risk)
        self.assertIn(b'Ahmed Ali', non_defaulters)
        self.assertIn(b'Barred', resp.data)

    def test_defaulters_export_excel(self):
        self._login()
        resp = self.client.get('/analytics/defaulters/export-excel')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheetml', resp.headers['Content-Type'])
        self.assertTrue(resp.data[:2] == b'PK')
        values = _flat(_xlsx_values(resp.data))
        self.assertIn('Usman Tariq', values)
        self.assertNotIn('Ahmed Ali', values)

    def test_email_defaulter_without_smtp_never_500s(self):
        self._login()
        resp = self.client.post(
            f'/analytics/defaulters/email/{self.usman.id}', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Email is not configured', resp.data)

    # --- Student portal (v4: CMS ID + password login) ---
    def test_student_portal_lookup(self):
        # Ahmed Ali's credentials were set by _add_student in setUp.
        resp = self.client.post('/student/login', data={
            'cms_id': 'CMS-0001', 'password': 'StudPass1'},
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Ahmed Ali', resp.data)
        self.assertIn(b'96', resp.data)

    def test_student_portal_unknown_cms_id(self):
        resp = self.client.post('/student/login', data={
            'cms_id': 'CMS-9999', 'password': 'StudPass1'},
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Invalid CMS ID or password', resp.data)
        self.assertNotIn(b'Ahmed Ali', resp.data)

    def test_old_portal_url_redirects_to_student_login(self):
        resp = self.client.get('/portal/', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/student/login', resp.headers['Location'])


if __name__ == '__main__':
    unittest.main()
