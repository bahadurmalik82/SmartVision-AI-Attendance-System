# UI Changes — ML Project UI v2 (popups, sidebar, animations ONLY)

Date: Saturday 21 November 2026. Based on ML-Project-Complete (52/52 tests).
Nothing else was changed: no routes, no ML code, no models, no tests, no data.

## 1. Themed popups — no native browser popup anywhere

**New reusable modal in `app/templates/base.html`:**
- Navy header (`#0A192F` → `#003366` gradient) with gold icon, white body,
  gold/navy buttons, blurred backdrop, scale + fade-in animation.
- Close with the X, ESC key, or clicking the backdrop. Focus goes to the
  primary button.
- Helpers: `showIbaAlert(title, message, type, onClose)` and
  `showIbaConfirm(title, message, confirmText, onConfirm)`.
  Types: `info` / `success` / `warning` / `error` (different icon).
- Safety net: `window.alert` and `window.confirm` are overridden to route
  through the themed modal, so a default browser popup can never appear.
  The confirm replacement returns `false`, so no guarded action runs
  unconfirmed.

**Every native call site replaced (7 total):**
| File | Was | Now |
|---|---|---|
| `app/templates/enrollment.html` (~line 295) | `alert(data.message)` after enrolling 3 photos | `showIbaAlert("Enrollment Successful", …, "success", …)` — OK closes the capture modal and reloads |
| `app/templates/enrollment.html` (~line 196) | `alert("Camera access denied…")` | `showIbaAlert("Camera Access Denied", …, "error")` |
| `app/templates/dashboard.html` (~line 202) | `alert("Camera permission denied…")` | `showIbaAlert("Camera Access Denied", …, "error")` |
| `app/templates/cameras.html` (delete camera) | `onsubmit="return confirm('Remove camera?')"` | `data-iba-confirm` themed confirm; submits only on Confirm |
| `app/templates/dashboard.html` (Stop Class) | `onsubmit="return confirm('Stop monitoring…')"` | `data-iba-confirm` themed confirm; submits only on Confirm |
| `app/templates/enrollment.html` (delete student) | `onsubmit="return confirm('Remove student?')"` | `data-iba-confirm` themed confirm; submits only on Confirm |
| `app/templates/timetable.html` (delete class) | `onsubmit="return confirm('Remove this class?')"` | `data-iba-confirm` themed confirm; submits only on Confirm |

Confirm forms use `form[data-iba-confirm]` + a submit handler in `base.html`:
cancel does nothing; Confirm calls the native `form.submit()` once.

**Flash banners:** text and logic unchanged — only a slide-in animation
was added, plus auto-dismiss after 6 seconds (fade, then removed).

## 2. Sidebar replaces the top bar

**Deleted:** the old top `<nav>` bar in `app/templates/base.html`
(the cramped Dashboard / Timetable / Cameras / … row).

**New fixed left sidebar** (IBA theme, modelled on the reference design):
- 264px wide, navy gradient (`#0A192F` → `#003366`), gold active indicator.
- Header: logo slot + brand. **Brand placeholder — name/logo NOT changed:**
  still "Sukkur IBA University / Smart AI Attendance". Logo slot is
  `<img src="/static/img/logo.png">` and falls back to the graduation-cap
  icon if the file is missing. When a name/logo is picked, change only the
  brand text in `base.html` and drop the file at `app/static/img/logo.png`.
- Menu, in order, same URLs/icons as the old top bar: Dashboard
  (`fa-gauge`), Timetable (`fa-calendar-days`), Cameras (`fa-video`),
  Students (`fa-user-plus`), Analytics (`fa-chart-pie`), ML Models
  (`fa-brain`), <75% Defaulters (`fa-triangle-exclamation`, red/gold accent),
  divider, Student Portal (`fa-magnifying-glass`), Logout
  (`fa-right-from-bracket`, red). Logged out: Student Portal + Teacher Login.
- Bottom: teacher name chip when logged in.
- **Active page:** gold left border + lighter navy background, detected
  from `request.endpoint` in Jinja — nothing is hardcoded per page.
- **Open/close:** the button at the top of the sidebar toggles expanded
  (264px, icons + labels) ↔ collapsed (76px, icons only, `title` tooltips),
  smooth 300ms width transition; the main content margin shifts with it.
  State is remembered in `localStorage` on desktop only.
- **Mobile (<768px):** sidebar is hidden off-canvas and slides in over a
  dim backdrop when the floating hamburger (top-left of the content) is
  tapped; it slides out via the backdrop or the X. A slim mobile top strip
  with the hamburger replaces the old top bar on small screens.
- Only the navigation shell in `base.html` changed — no page content,
  forms, tables, or dashboard cards were touched.

## 3. Professional animations (site-wide, subtle)

**New shared stylesheet:** `app/static/css/animations.css` (linked from
`base.html`) + minimal JS in `base.html`. No colour changes to existing
elements, no bouncy/cartoon effects.
- Page content fades/slides in on load (280ms).
- Content cards lift 2px with a soft shadow on hover.
- Buttons lift 1px on hover; sidebar links get a gold underline sweep.
- Modal scales/fades in; flash banners slide in.
- `prefers-reduced-motion` is respected — all animations are disabled then.

## Files changed

- `app/templates/base.html` — sidebar, themed modal + JS, animations link
  (rewritten navigation shell only; page content untouched).
- `app/templates/enrollment.html` — 3 popup call sites replaced.
- `app/templates/dashboard.html` — 2 popup call sites replaced.
- `app/templates/cameras.html` — 1 confirm form replaced.
- `app/templates/timetable.html` — 1 confirm form replaced.
- `app/static/css/animations.css` — NEW (sidebar + modal + animation CSS).
- `UI-CHANGES.md` — NEW (this file).

## Verify

- `grep -rn "alert(\|confirm(" app/templates/` → no matches
  (only the modal definitions/overrides in `base.html` remain).
- Test suite: `python -m unittest discover -s tests` → 52/52 pass.
- Smoke test: `/auth/login` 200, `/` logged out 302, `/portal/` 200,
  after login `/analytics/` and `/students/` 200.

## Run it (Windows)

```powershell
python --version
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Open http://127.0.0.1:5000 — register a teacher account on the site (there is no demo account any more).
