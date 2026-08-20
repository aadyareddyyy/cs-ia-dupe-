"""
CLAT Preparation Docket
------------------------
A simple Flask website for tracking CLAT exam preparation.

TECHNIQUES USED IN THIS FILE (for reference):
  - Flask web framework: routes, forms, sessions, redirects, flash messages
  - sqlite3: a small file-based database, accessed with plain SQL queries
  - matplotlib: draws one PNG chart (correct / incorrect / not attempted pie)
  - datetime: date math for the exam countdown and spaced revision dates
  - Plain Python: lists, dictionaries, loops, functions, f-strings

There is no advanced framework magic here on purpose - every route is a
plain function that reads the database, does some simple math, and shows
a page.
"""

import os
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, session, flash, g

import matplotlib
matplotlib.use("Agg")  # draw charts without needing a screen
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# Basic setup
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "clat_tracker.db")
CHARTS_DIR = os.path.join(BASE_DIR, "static", "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-this-in-production")


@app.errorhandler(500)
def handle_server_error(e):
    print("Server error:", e)
    return "Something went wrong on the server. Please go back and try again.", 500

# ---------------------------------------------------------------------------
# Syllabus data (kept as one simple Python list of dictionaries)
# Section max marks add up to 120, based on the CLAT question distribution.
# ---------------------------------------------------------------------------
SUBJECTS = [
    {
        "id": "english", "roman": "I", "name": "English Language", "max": 24,
        "topics": [
            "Reading Comprehension", "Vocabulary in Context", "Synonyms & Antonyms",
            "Grammar & Usage", "Tone & Author's Viewpoint", "Para Jumbles",
            "Summary & Main Idea", "Critical Reasoning in Passages",
            "Figures of Speech", "Inference-Based Questions",
        ],
    },
    {
        "id": "gk", "roman": "II", "name": "Current Affairs & GK", "max": 30,
        "topics": [
            "National Affairs", "International Affairs",
            "Legal & Constitutional Current Affairs", "Static GK",
            "Awards & Honours", "Sports & Miscellaneous",
            "Government Schemes & Policies", "Science & Technology News",
            "Books, Authors & Committees", "Person in the News",
        ],
    },
    {
        "id": "legal", "roman": "III", "name": "Legal Reasoning", "max": 30,
        "topics": [
            "Constitutional Law", "Law of Contracts", "Torts", "Criminal Law",
            "Legal Maxims & Principles", "Family Law Basics",
            "International Law Basics", "Intellectual Property Basics",
            "Jurisprudence & Legal Theory", "Recent Landmark Judgments",
        ],
    },
    {
        "id": "logical", "roman": "IV", "name": "Logical Reasoning", "max": 24,
        "topics": [
            "Critical Reasoning", "Analogies & Series", "Syllogisms",
            "Puzzles & Arrangements", "Statement-Assumption", "Blood Relations",
            "Coding-Decoding", "Cause & Effect", "Strengthen-Weaken Arguments",
            "Logical Sequences",
        ],
    },
    {
        "id": "quant", "roman": "V", "name": "Quantitative Techniques", "max": 12,
        "topics": [
            "Data Interpretation", "Ratio, Proportion & Averages",
            "Percentages & Profit-Loss", "Basic Algebra", "Graphs & Charts",
            "Time & Work", "Mensuration Basics", "Number Systems",
        ],
    },
]
SUBJECT_MAP = {s["id"]: s for s in SUBJECTS}

REVISION_INTERVALS = [3, 7, 15, 30]  # days, for spaced repetition of finished topics
MISTAKE_TYPES = [
    "Silly Mistake", "Conceptual Gap", "Time Management",
    "Misread Question", "Wrong Guess", "Calculation Error", "Other",
]

# Chart colours - simple black/white/grey palette to match the website
INK = "#111111"
GREY = "#9a9a9a"
LIGHT_GREY = "#e5e5e5"
PAPER = "#ffffff"

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    """Open one database connection per request and reuse it."""
    if "db" not in g:
        # timeout=10 makes SQLite retry for up to 10s instead of failing
        # instantly with "database is locked" if another request is
        # writing at the same moment.
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        # WAL mode lets reads and writes happen concurrently instead of
        # blocking each other, which is what actually causes "database is
        # locked" errors under gunicorn.
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA busy_timeout = 10000")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            exam_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS topic_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id),
            subject_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            percent INTEGER NOT NULL DEFAULT 0,
            UNIQUE(profile_id, subject_id, topic)
        );

        CREATE TABLE IF NOT EXISTS mock_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id),
            date TEXT NOT NULL,
            english_correct INTEGER, english_incorrect INTEGER, english_unattempted INTEGER,
            gk_correct INTEGER, gk_incorrect INTEGER, gk_unattempted INTEGER,
            legal_correct INTEGER, legal_incorrect INTEGER, legal_unattempted INTEGER,
            logical_correct INTEGER, logical_incorrect INTEGER, logical_unattempted INTEGER,
            quant_correct INTEGER, quant_incorrect INTEGER, quant_unattempted INTEGER
        );

        CREATE TABLE IF NOT EXISTS study_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id),
            date TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            hours REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS revision_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id),
            topic_key TEXT NOT NULL,
            stage INTEGER NOT NULL DEFAULT 0,
            next_date TEXT NOT NULL,
            retired INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS mistakes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id),
            date TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            mistake_type TEXT NOT NULL,
            note TEXT,
            reviewed INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.commit()

    # If an older version of the database is on disk (with different mock
    # test columns), add the new columns instead of crashing. Old columns
    # are simply left unused.
    existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(mock_tests)")]
    for s in SUBJECTS:
        for suffix in ("correct", "incorrect", "unattempted"):
            col = f"{s['id']}_{suffix}"
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE mock_tests ADD COLUMN {col} INTEGER")
    conn.commit()
    conn.close()


def get_or_create_profile(name):
    db = get_db()
    row = db.execute("SELECT * FROM profiles WHERE name = ?", (name,)).fetchone()
    if row:
        return row
    db.execute("INSERT INTO profiles (name) VALUES (?)", (name,))
    db.commit()
    profile_id = db.execute("SELECT id FROM profiles WHERE name = ?", (name,)).fetchone()["id"]
    rows = [(profile_id, s["id"], t) for s in SUBJECTS for t in s["topics"]]
    db.executemany(
        "INSERT OR IGNORE INTO topic_status (profile_id, subject_id, topic) VALUES (?, ?, ?)",
        rows,
    )
    db.commit()
    return db.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()


def today_iso():
    return datetime.now().strftime("%Y-%m-%d")


def add_days(date_str, n):
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def fmt_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d %b %Y")


def roll_number(profile_id):
    year = datetime.now().year + 1
    return f"CLT{year}{profile_id:05d}"


def section_score(correct, incorrect):
    """CLAT marking: +1 for each correct answer, -0.25 for each incorrect answer."""
    correct = correct or 0
    incorrect = incorrect or 0
    return correct * 1 - incorrect * 0.25


def mock_total_score(row):
    """Add up the computed score of all five sections for one mock test row."""
    return sum(section_score(row[f"{s['id']}_correct"], row[f"{s['id']}_incorrect"]) for s in SUBJECTS)


def mock_total_questions(row):
    """Total number of questions attempted (correct + incorrect) across all sections."""
    total = 0
    for s in SUBJECTS:
        total += (row[f"{s['id']}_correct"] or 0) + (row[f"{s['id']}_incorrect"] or 0)
    return total


def mock_section_breakdown(row):
    """Build a per-section breakdown list, plus an overall row, for one mock test."""
    breakdown = []
    overall_correct = overall_incorrect = overall_unattempted = overall_max = 0
    for s in SUBJECTS:
        correct = row[f"{s['id']}_correct"] or 0
        incorrect = row[f"{s['id']}_incorrect"] or 0
        unattempted = row[f"{s['id']}_unattempted"] or 0
        marks_correct = correct * 1
        marks_incorrect = incorrect * 0.25
        attempted = correct + incorrect
        accuracy_pct = round(correct / attempted * 100, 1) if attempted else 0
        actual_score = round(marks_correct - marks_incorrect, 2)
        # Score as a percentage of that section's own max marks. This is
        # the fair way to compare sections against each other, since raw
        # score is meaningless across sections with different question
        # counts (e.g. GK has 30 questions, Quant only has 12 - a raw
        # score of 20 means very different things in each).
        score_pct = round(actual_score / s["max"] * 100, 1) if s["max"] else 0
        breakdown.append({
            "name": s["name"],
            "attempted": attempted,
            "unattempted": unattempted,
            "max": s["max"],
            "correct": correct,
            "incorrect": incorrect,
            "marks_correct": marks_correct,
            "marks_incorrect": -marks_incorrect,
            "actual_score": actual_score,
            "accuracy_pct": accuracy_pct,
            "score_pct": score_pct,
        })
        overall_correct += correct
        overall_incorrect += incorrect
        overall_unattempted += unattempted
        overall_max += s["max"]

    overall_attempted = overall_correct + overall_incorrect
    overall_accuracy = round(overall_correct / overall_attempted * 100, 1) if overall_attempted else 0
    overall_score = round(overall_correct * 1 - overall_incorrect * 0.25, 2)
    overall_score_pct = round(overall_score / overall_max * 100, 1) if overall_max else 0
    breakdown.append({
        "name": "Overall",
        "attempted": overall_attempted,
        "unattempted": overall_unattempted,
        "max": overall_max,
        "correct": overall_correct,
        "incorrect": overall_incorrect,
        "marks_correct": overall_correct * 1,
        "marks_incorrect": -(overall_incorrect * 0.25),
        "actual_score": overall_score,
        "accuracy_pct": overall_accuracy,
        "score_pct": overall_score_pct,
    })
    return breakdown


def mock_sections_ranked(row):
    """Sections for one mock test, ranked strongest to weakest by score_pct.

    Sorting by score_pct (score as % of that section's own max marks)
    instead of raw actual_score is what makes this a fair ranking - it
    puts a 12-question section (Quant) on the same footing as a
    30-question section (GK) instead of raw marks favouring the bigger
    sections every time. Excludes the combined "Overall" row.
    """
    breakdown = mock_section_breakdown(row)
    sections_only = [r for r in breakdown if r["name"] != "Overall"]
    return sorted(sections_only, key=lambda r: r["score_pct"], reverse=True)


def mock_overall_percentage(row):
    """Score as a percentage of the full 120 marks."""
    full_marks = sum(s["max"] for s in SUBJECTS)
    return round(mock_total_score(row) / full_marks * 100, 1)


# ---------------------------------------------------------------------------
# Login guard - every page except /login needs a profile in the session
# ---------------------------------------------------------------------------
@app.before_request
def require_login():
    open_endpoints = {"login", "static"}
    if request.endpoint not in open_endpoints and "profile_id" not in session:
        return redirect(url_for("login"))


@app.context_processor
def inject_profile():
    if "profile_id" in session:
        return {
            "current_profile_name": session.get("profile_name"),
            "current_roll_no": roll_number(session["profile_id"]),
        }
    return {}


# ---------------------------------------------------------------------------
# Routes - login / logout
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name", "").strip().lower()
        if not name:
            flash("Enter a profile name to continue.")
            return redirect(url_for("login"))
        profile = get_or_create_profile(name)
        session["profile_id"] = profile["id"]
        session["profile_name"] = profile["name"]
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Routes - dashboard
# ---------------------------------------------------------------------------
@app.route("/dashboard")
def dashboard():
    db = get_db()
    profile_id = session["profile_id"]
    profile = db.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()

    percents = [r["percent"] for r in db.execute(
        "SELECT percent FROM topic_status WHERE profile_id = ?", (profile_id,)
    ).fetchall()]
    overall_pct = round(sum(percents) / len(percents)) if percents else 0

    latest_mock_row = db.execute(
        "SELECT * FROM mock_tests WHERE profile_id = ? ORDER BY date DESC, id DESC LIMIT 1",
        (profile_id,),
    ).fetchone()
    latest_mock_score = round(mock_total_score(latest_mock_row), 2) if latest_mock_row else None
    latest_mock_percentage = mock_overall_percentage(latest_mock_row) if latest_mock_row else None

    today_hours_row = db.execute(
        "SELECT COALESCE(SUM(hours), 0) total FROM study_logs WHERE profile_id = ? AND date = ?",
        (profile_id, today_iso()),
    ).fetchone()
    today_hours = today_hours_row["total"]

    due_revisions = db.execute(
        "SELECT COUNT(*) c FROM revision_items WHERE profile_id = ? AND retired = 0 AND next_date <= ?",
        (profile_id, today_iso()),
    ).fetchone()["c"]

    open_mistakes = db.execute(
        "SELECT COUNT(*) c FROM mistakes WHERE profile_id = ? AND reviewed = 0",
        (profile_id,),
    ).fetchone()["c"]

    days_left = None
    if profile["exam_date"]:
        days_left = (datetime.strptime(profile["exam_date"], "%Y-%m-%d") - datetime.now()).days

    return render_template(
        "dashboard.html",
        overall_pct=overall_pct,
        latest_mock=latest_mock_score,
        latest_mock_percentage=latest_mock_percentage,
        today_hours=today_hours,
        due_revisions=due_revisions,
        open_mistakes=open_mistakes,
        exam_date=profile["exam_date"],
        days_left=days_left,
        fmt_date=fmt_date,
    )


@app.route("/set-exam-date", methods=["POST"])
def set_exam_date():
    db = get_db()
    exam_date = request.form.get("exam_date") or None
    db.execute("UPDATE profiles SET exam_date = ? WHERE id = ?", (exam_date, session["profile_id"]))
    db.commit()
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Routes - subjects (percent complete per topic)
# ---------------------------------------------------------------------------
@app.route("/subjects")
def subjects():
    db = get_db()
    profile_id = session["profile_id"]

    subject_view = []
    for s in SUBJECTS:
        topic_rows = [
            dict(db.execute(
                "SELECT * FROM topic_status WHERE profile_id = ? AND subject_id = ? AND topic = ?",
                (profile_id, s["id"], t),
            ).fetchone())
            for t in s["topics"]
        ]
        avg_pct = round(sum(t["percent"] for t in topic_rows) / len(topic_rows)) if topic_rows else 0
        subject_view.append({**s, "topic_rows": topic_rows, "avg_pct": avg_pct})

    return render_template("subjects.html", subject_view=subject_view)


@app.route("/subjects/update", methods=["POST"])
def update_topic():
    db = get_db()
    profile_id = session["profile_id"]
    subject_id = request.form["subject_id"]
    topic = request.form["topic"]
    percent = request.form.get("percent", type=int) or 0
    percent = max(0, min(100, percent))  # keep it inside 0-100

    db.execute(
        "UPDATE topic_status SET percent = ? WHERE profile_id = ? AND subject_id = ? AND topic = ?",
        (percent, profile_id, subject_id, topic),
    )

    if percent == 100:
        key = f"{subject_id}::{topic}"
        existing = db.execute(
            "SELECT id FROM revision_items WHERE profile_id = ? AND topic_key = ? AND retired = 0",
            (profile_id, key),
        ).fetchone()
        if not existing:
            db.execute(
                "INSERT INTO revision_items (profile_id, topic_key, stage, next_date, retired) VALUES (?, ?, 0, ?, 0)",
                (profile_id, key, add_days(today_iso(), REVISION_INTERVALS[0])),
            )
    db.commit()
    return redirect(url_for("subjects"))


# ---------------------------------------------------------------------------
# Routes - mock tests
# ---------------------------------------------------------------------------
@app.route("/mocks", methods=["GET", "POST"])
def mocks():
    db = get_db()
    profile_id = session["profile_id"]

    if request.method == "POST":
        date = request.form.get("date") or today_iso()

        # Read correct / incorrect / unattempted counts for every section
        # (blank box on the form counts as 0).
        counts = {}
        for s in SUBJECTS:
            correct = request.form.get(f"{s['id']}_correct", type=int) or 0
            incorrect = request.form.get(f"{s['id']}_incorrect", type=int) or 0
            unattempted = request.form.get(f"{s['id']}_unattempted", type=int) or 0
            counts[s["id"]] = (correct, incorrect, unattempted)

        # Validate: correct + incorrect + unattempted must equal that
        # section's fixed number of questions. Since the five section
        # totals already add up to 120, this also guarantees the whole
        # mock test adds up to 120 questions.
        errors = []
        for s in SUBJECTS:
            correct, incorrect, unattempted = counts[s["id"]]
            entered_total = correct + incorrect + unattempted
            if entered_total != s["max"]:
                errors.append(
                    f"{s['name']}: correct + incorrect + unattempted = {entered_total}, "
                    f"but this section has exactly {s['max']} questions."
                )

        if errors:
            for e in errors:
                flash(e)
            return redirect(url_for("mocks"))

        values = [profile_id, date]
        for s in SUBJECTS:
            correct, incorrect, unattempted = counts[s["id"]]
            values.append(correct)
            values.append(incorrect)
            values.append(unattempted)

        # The insert+commit is the one step that actually has to succeed
        # for a mock test to count as "saved". Wrap it explicitly so any
        # database error (e.g. a locked file under concurrent requests)
        # shows up as a clear flash message instead of a generic 500 page
        # that leaves you guessing whether it saved or not.
        try:
            db.execute(
                """INSERT INTO mock_tests
                   (profile_id, date,
                    english_correct, english_incorrect, english_unattempted,
                    gk_correct, gk_incorrect, gk_unattempted,
                    legal_correct, legal_incorrect, legal_unattempted,
                    logical_correct, logical_incorrect, logical_unattempted,
                    quant_correct, quant_incorrect, quant_unattempted)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            db.commit()
        except sqlite3.Error as db_error:
            db.rollback()
            print("Mock test save failed:", db_error)
            flash(f"Could not save the mock test (database error: {db_error}). Please try again.")
            return redirect(url_for("mocks"))

        # Chart drawing should never be able to break the save. If it fails
        # for any reason, the mock test is still safely in the database.
        try:
            generate_charts(profile_id)
        except Exception as chart_error:
            print("Chart generation failed:", chart_error)
            flash("Mock test saved. (Charts could not be redrawn this time.)")
            return redirect(url_for("mocks"))

        flash("Mock test saved.")
        return redirect(url_for("mocks"))

    rows = db.execute(
        "SELECT * FROM mock_tests WHERE profile_id = ? ORDER BY date DESC, id DESC", (profile_id,)
    ).fetchall()
    mock_view = [
        {
            "date": r["date"],
            "total_questions": mock_total_questions(r),
            "total_score": round(mock_total_score(r), 2),
            "percentage": mock_overall_percentage(r),
        }
        for r in rows
    ]

    latest_breakdown = mock_section_breakdown(rows[0]) if rows else None
    latest_percentage = mock_overall_percentage(rows[0]) if rows else None
    latest_ranked_sections = mock_sections_ranked(rows[0]) if rows else None

    pie_chart = f"charts/pie_{profile_id}.png"

    def chart_url(rel_path):
        """Full static URL with a cache-busting ?v= timestamp, or None if
        the chart doesn't exist yet. Without this the browser keeps
        showing the chart from the previous mock test instead of the
        freshly redrawn one."""
        full_path = os.path.join(BASE_DIR, "static", rel_path)
        if not os.path.exists(full_path):
            return None
        return url_for("static", filename=rel_path) + f"?v={int(os.path.getmtime(full_path))}"

    return render_template(
        "mocks.html", mocks=mock_view, subjects=SUBJECTS, fmt_date=fmt_date, today=today_iso(),
        latest_breakdown=latest_breakdown, latest_percentage=latest_percentage,
        latest_ranked_sections=latest_ranked_sections,
        pie_chart_url=chart_url(pie_chart),
    )


def generate_charts(profile_id):
    """Redraw the pie chart PNG for a profile.

    Wrapped in its own try/except so a chart failing to draw does not
    stop the mock test save from working.
    """
    db = get_db()
    rows = db.execute(
        "SELECT * FROM mock_tests WHERE profile_id = ? ORDER BY date ASC, id ASC", (profile_id,)
    ).fetchall()
    if not rows:
        return

    # Liberation Sans is metrically identical to Arial and is actually
    # installed on Linux servers (Render included) - plain "sans-serif"
    # falls back to DejaVu Sans, which looks visibly different from the
    # Arial used across the rest of the site.
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["Liberation Sans", "Arial", "DejaVu Sans"]

    # --- Latest mock pie chart: correct vs incorrect vs not attempted ---
    try:
        latest = rows[-1]
        total_correct = sum(latest[f"{s['id']}_correct"] or 0 for s in SUBJECTS)
        total_incorrect = sum(latest[f"{s['id']}_incorrect"] or 0 for s in SUBJECTS)
        total_unattempted = sum(latest[f"{s['id']}_unattempted"] or 0 for s in SUBJECTS)

        fig3, ax3 = plt.subplots(figsize=(4.2, 4.2), dpi=150)
        fig3.patch.set_facecolor(PAPER)
        pie_values = [total_correct, total_incorrect, total_unattempted]
        pie_labels = [
            f"Correct ({total_correct})",
            f"Incorrect ({total_incorrect})",
            f"Not attempted ({total_unattempted})",
        ]
        pie_colors = [INK, GREY, LIGHT_GREY]
        # Drop any zero-value slices so the pie chart doesn't show empty labels.
        filtered = [(v, l, c) for v, l, c in zip(pie_values, pie_labels, pie_colors) if v > 0]
        if filtered:
            vals, labs, cols = zip(*filtered)
            wedges, texts, autotexts = ax3.pie(
                vals, labels=labs, colors=cols, autopct="%1.0f%%",
                textprops={"color": INK, "fontsize": 9},
                wedgeprops={"edgecolor": INK, "linewidth": 1.2},
                startangle=90,
            )
            # The correct-answers slice is solid black (INK) to match the
            # site's theme, so its percentage label needs to be white or
            # it's invisible against its own wedge.
            for autotext, col in zip(autotexts, cols):
                autotext.set_color(PAPER if col == INK else INK)
                autotext.set_fontweight("bold")
        ax3.set_title("Correct / Incorrect / Not Attempted", color=INK, fontsize=10, fontweight="bold", pad=12)
        fig3.subplots_adjust(top=0.85)
        fig3.tight_layout()
        fig3.savefig(os.path.join(CHARTS_DIR, f"pie_{profile_id}.png"), facecolor=PAPER)
        plt.close(fig3)
    except Exception as e:
        print("Pie chart failed:", e)


# ---------------------------------------------------------------------------
# Routes - study log
# ---------------------------------------------------------------------------
@app.route("/study", methods=["GET", "POST"])
def study():
    db = get_db()
    profile_id = session["profile_id"]

    if request.method == "POST":
        date = request.form.get("date") or today_iso()
        subject_id = request.form.get("subject_id")
        hours = request.form.get("hours", type=float)
        if subject_id and hours:
            db.execute(
                "INSERT INTO study_logs (profile_id, date, subject_id, hours) VALUES (?, ?, ?, ?)",
                (profile_id, date, subject_id, hours),
            )
            db.commit()
        return redirect(url_for("study"))

    totals = db.execute(
        """SELECT subject_id, COALESCE(SUM(hours), 0) total
           FROM study_logs WHERE profile_id = ? GROUP BY subject_id""",
        (profile_id,),
    ).fetchall()
    totals_map = {r["subject_id"]: r["total"] for r in totals}
    subject_totals = [{**s, "total": totals_map.get(s["id"], 0)} for s in SUBJECTS]

    return render_template("study.html", subjects=SUBJECTS, subject_totals=subject_totals, today=today_iso())


# ---------------------------------------------------------------------------
# Routes - revision queue (spaced repetition for finished topics)
# ---------------------------------------------------------------------------
@app.route("/revision")
def revision():
    db = get_db()
    profile_id = session["profile_id"]
    rows = db.execute(
        "SELECT * FROM revision_items WHERE profile_id = ? AND retired = 0 ORDER BY next_date ASC",
        (profile_id,),
    ).fetchall()

    items = []
    for r in rows:
        subject_id, topic = r["topic_key"].split("::", 1)
        subject_name = SUBJECT_MAP[subject_id]["name"]
        items.append({
            "id": r["id"], "label": f"{subject_name} - {topic}",
            "next_date": r["next_date"], "due": r["next_date"] <= today_iso(),
        })

    return render_template("revision.html", items=items, fmt_date=fmt_date)


@app.route("/revision/mark/<int:item_id>", methods=["POST"])
def mark_revised(item_id):
    db = get_db()
    profile_id = session["profile_id"]
    row = db.execute(
        "SELECT * FROM revision_items WHERE id = ? AND profile_id = ?", (item_id, profile_id)
    ).fetchone()
    if row:
        # Bug fix: this used to compare the OLD stage to the max and only
        # retire on the *next* click after reaching the last interval, so
        # marking revised on the final stage looked like it did nothing.
        # Now it retires immediately once you've cleared the last stage.
        if row["stage"] >= len(REVISION_INTERVALS) - 1:
            db.execute("UPDATE revision_items SET retired = 1 WHERE id = ?", (item_id,))
        else:
            next_stage = row["stage"] + 1
            next_date = add_days(today_iso(), REVISION_INTERVALS[next_stage])
            db.execute(
                "UPDATE revision_items SET stage = ?, next_date = ?, retired = 0 WHERE id = ?",
                (next_stage, next_date, item_id),
            )
        db.commit()
    return redirect(url_for("revision"))


# ---------------------------------------------------------------------------
# Routes - mistakes log + revise mistakes
# ---------------------------------------------------------------------------
def group_mistakes_by_type(mistake_view):
    """Group mistakes into buckets by mistake_type, with a count for each.

    This is the "grouping / aggregation" technique - instead of a flat
    list, walk it once and build a dictionary keyed by category, using
    dict.setdefault() to create each bucket the first time we see it.
    It's the hand-rolled version of what pandas.groupby() or SQL's
    GROUP BY do automatically. Sorted by count (most common mistake
    type first) so the biggest pattern jumps out immediately.
    """
    groups = {}
    for m in mistake_view:
        bucket = groups.setdefault(m["mistake_type"], {"count": 0, "items": []})
        bucket["count"] += 1
        bucket["items"].append(m)

    grouped_list = [
        {"mistake_type": mtype, "count": data["count"], "items": data["items"]}
        for mtype, data in groups.items()
    ]
    return sorted(grouped_list, key=lambda g: g["count"], reverse=True)


@app.route("/mistakes", methods=["GET", "POST"])
def mistakes():
    db = get_db()
    profile_id = session["profile_id"]

    if request.method == "POST":
        date = request.form.get("date") or today_iso()
        subject_id = request.form.get("subject_id")
        topic = request.form.get("topic", "").strip()
        mistake_type = request.form.get("mistake_type")
        note = request.form.get("note", "").strip()
        if subject_id and topic and mistake_type:
            db.execute(
                """INSERT INTO mistakes (profile_id, date, subject_id, topic, mistake_type, note, reviewed)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (profile_id, date, subject_id, topic, mistake_type, note),
            )
            db.commit()
        return redirect(url_for("mistakes"))

    rows = db.execute(
        "SELECT * FROM mistakes WHERE profile_id = ? ORDER BY reviewed ASC, date DESC", (profile_id,)
    ).fetchall()
    mistake_view = [
        {**dict(r), "subject_name": SUBJECT_MAP[r["subject_id"]]["name"]}
        for r in rows
    ]
    to_revise = [m for m in mistake_view if not m["reviewed"]]
    reviewed = [m for m in mistake_view if m["reviewed"]]

    return render_template(
        "mistakes.html", subjects=SUBJECTS, mistake_types=MISTAKE_TYPES,
        to_revise=to_revise, reviewed=reviewed, fmt_date=fmt_date, today=today_iso(),
    )


@app.route("/mistakes/mark/<int:mistake_id>", methods=["POST"])
def mark_mistake_reviewed(mistake_id):
    db = get_db()
    profile_id = session["profile_id"]
    db.execute(
        "UPDATE mistakes SET reviewed = 1 WHERE id = ? AND profile_id = ?",
        (mistake_id, profile_id),
    )
    db.commit()
    return redirect(url_for("mistakes"))


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True)
else:
    init_db()
