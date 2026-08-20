# CLAT Preparation Docket

A simple, server-rendered Flask website for tracking CLAT prep: syllabus
completion by percentage, mock test scores (with matplotlib charts), study
hours, a mistakes log, and a spaced-repetition revision queue. Data is
stored in a local SQLite database. Look and feel is deliberately plain:
black text on white, Arial font, no boxes/borders/cards - just headings,
plain text, and simple forms.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`. The database (`clat_tracker.db`) and the
`static/charts/` chart images are created automatically the first time you
run it.

## Project structure

```
app.py                 Flask app: routes, database logic, chart drawing
templates/              Jinja2 HTML pages (one per route, plus base.html)
static/style.css        Styling (white background, black text/accents)
static/charts/          Auto-generated PNG chart (correct/incorrect/not attempted pie)
clat_tracker.db         SQLite database (auto-created)
requirements.txt        Python packages needed
Procfile                Tells Render how to start the app
```

## What changed from the previous version

- **Subjects**: each topic now tracks a percentage complete (0-100) instead
  of a not-started/in-progress/done status, and every subject has more
  topics (researched from the current CLAT syllabus).
- **Mock tests**: for every section you now enter *correct*, *incorrect*,
  and *unattempted* answer counts. The three must add up to that section's
  fixed number of questions (English 24, GK 30, Legal 30, Logical 24,
  Quant 12 - which always add up to 120), so there is no way for the
  numbers to "not add up." If they don't match, the mock is not saved and
  you get a message explaining exactly which section is off. The score is
  calculated for you using CLAT's marking scheme (+1 per correct answer,
  -0.25 per incorrect answer) - no manual score entry at all. Saving a
  mock shows a breakdown table (per section: attempted, unattempted,
  correct, incorrect, marks from correct, marks from incorrect, actual
  score, accuracy), an overall percentage, and a pie chart of correct /
  incorrect / not attempted with percentages. The latest mock score and
  percentage also show on the Dashboard.
- **Mistakes**: a new page to log the type of mistake you made on a topic
  (silly mistake, conceptual gap, misread question, etc.) and a second
  list on the same page to revise/clear those mistakes.
- **Look**: plain white background, black text, Arial font, no boxes or
  borders anywhere - just headings and plain text/forms.

## Notes

- Profiles are name-only, no passwords - data is separated by profile name
  in SQLite, not securely authenticated. Fine for personal or small shared
  use, not for public deployment as-is.
- Charts are redrawn as PNG files each time you log a mock test, saved per
  profile in `static/charts/`.
- Set a real `SECRET_KEY` environment variable before deploying anywhere
  public:
  ```bash
  export SECRET_KEY="something-long-and-random"
  ```

## A note on the earlier "Internal Server Error"

That was a real bug: one line drawing the charts (`ax.spines[["top",
"right"]]`) only works on newer versions of matplotlib. On an older
matplotlib install it throws an error, and because that happened *after*
the mock test was already written to the database, the page would crash
with a 500 error even though the save had actually gone through - which
made it look like saving was broken. This version replaces that line with
plain, version-safe matplotlib calls, and wraps each chart in its own
try/except so that even if chart drawing fails for any reason, the mock
test still saves and the page still loads normally.

## Deploying on Render

1. Push this folder to a GitHub repository.
2. On Render, create a new **Web Service** from that repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app` (already set in the included `Procfile`)
5. Add an environment variable `SECRET_KEY` with a random value.
6. Deploy.

Note: SQLite and the local chart PNGs live on the container's disk, which
does **not** persist across redeploys or restarts on Render's free tier
unless you attach a persistent disk. That's fine for trying it out; for
long-term real use, attach a Render Disk mounted at the project folder, or
move to a hosted database later.

## Techniques used (for your reference)

**Flask**
- Route decorators (GET/POST)
- Dynamic URL parameters (`<int:item_id>`)
- Form data parsing with type coercion (`request.form.get(..., type=float)`)
- Session-based authentication (cookie sessions, no passwords)
- Flash messaging
- `url_for()` redirects instead of hardcoded URLs
- `before_request` hook used as a login guard
- `context_processor` to inject variables into every template
- `teardown_appcontext` for closing the database connection

**SQLite (sqlite3, no ORM)**
- Parameterized queries (`?` placeholders) to avoid SQL injection
- Row factory (`sqlite3.Row`) for dict-like row access
- `executescript()` for schema creation
- `executemany()` for bulk inserts (seeding topics for a new profile)
- Foreign key constraints
- `INSERT OR IGNORE` for idempotent inserts
- One connection per request, opened lazily and closed automatically

**Data & logic**
- Plain Python lists/dicts as static config data (subjects, topics, mistake types)
- Percentage-based progress tracking with simple math (averages)
- Simple form validation with plain `if` checks and flash-messaged errors
  (mock test question counts vs. section/overall limits)
- Spaced-repetition scheduling using a fixed list of day intervals
- Composite key encode/decode (`f"{subject_id}::{topic}"`)
- `datetime` / `timedelta` for date math (countdown, revision due dates)

**Matplotlib**
- Non-interactive `Agg` backend for server-side chart rendering
- Pie chart (correct / incorrect / not attempted)
- Saving figures to PNG files, closing figures to free memory

**Python idioms**
- List comprehensions and dict comprehensions
- Generator expressions with `sum()`
- f-strings
- `os.path` / `os.makedirs` for file handling
- Environment variable fallback (`os.environ.get`)
- `__name__ == "__main__"` guard
