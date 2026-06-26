import csv
import io
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    Response,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    import matplotlib.pyplot as plt

    for font_path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        if os.path.exists(font_path):
            font_manager.fontManager.addfont(font_path)
            plt.rcParams["font.sans-serif"] = [font_manager.FontProperties(fname=font_path).get_name()]
            break
    plt.rcParams["axes.unicode_minus"] = False
except Exception:
    plt = None

try:
    import openpyxl
    from openpyxl.styles import Font
except Exception:
    openpyxl = None

try:
    import qrcode
except Exception:
    qrcode = None


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, "survey.db")
CHART_DIR = os.path.join(BASE_DIR, "static", "charts")

app = Flask(__name__)
app.config["SECRET_KEY"] = "survey-course-project-secret"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(CHART_DIR, exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS surveys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            is_public INTEGER NOT NULL DEFAULT 1,
            is_anonymous INTEGER NOT NULL DEFAULT 0,
            is_published INTEGER NOT NULL DEFAULT 0,
            slug TEXT UNIQUE,
            open_at TEXT,
            close_at TEXT,
            target_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            qtype TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            FOREIGN KEY(survey_id) REFERENCES surveys(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            jump_to_question_id INTEGER,
            FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
            FOREIGN KEY(jump_to_question_id) REFERENCES questions(id)
        );

        CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id INTEGER NOT NULL,
            user_id INTEGER,
            started_at TEXT,
            submitted_at TEXT NOT NULL,
            duration_seconds INTEGER DEFAULT 0,
            FOREIGN KEY(survey_id) REFERENCES surveys(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            response_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            text_answer TEXT,
            option_ids TEXT,
            FOREIGN KEY(response_id) REFERENCES responses(id) ON DELETE CASCADE,
            FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )
    conn.commit()
    conn.close()


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("T", " "))
    except ValueError:
        return None


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if "user_id" not in session:
            flash("请先登录。")
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_survey_or_404(survey_id):
    survey = get_db().execute(
        """
        SELECT s.*, u.username AS owner_name
        FROM surveys s
        JOIN users u ON u.id = s.owner_id
        WHERE s.id = ?
        """,
        (survey_id,),
    ).fetchone()
    if survey is None:
        return None
    return survey


def load_questions(survey_id):
    db = get_db()
    questions = db.execute(
        "SELECT * FROM questions WHERE survey_id = ? ORDER BY sort_order", (survey_id,)
    ).fetchall()
    result = []
    for question in questions:
        options = db.execute(
            "SELECT * FROM options WHERE question_id = ? ORDER BY sort_order", (question["id"],)
        ).fetchall()
        result.append({"question": question, "options": options})
    return result


def questions_to_config(questions):
    question_order = {bundle["question"]["id"]: bundle["question"]["sort_order"] for bundle in questions}
    data = []
    for bundle in questions:
        question = bundle["question"]
        data.append(
            {
                "content": question["content"],
                "qtype": question["qtype"],
                "options": [
                    {
                        "text": option["content"],
                        "jump_to": question_order.get(option["jump_to_question_id"], ""),
                    }
                    for option in bundle["options"]
                ],
            }
        )
    return data


def normalize_questions_config(questions_data):
    if not isinstance(questions_data, list):
        return []
    clean = []
    for item in questions_data:
        if not isinstance(item, dict):
            continue
        qtype = item.get("qtype", "single")
        if qtype not in {"single", "multiple", "text"}:
            qtype = "single"
        content = item.get("content", "").strip()
        if not content:
            continue
        options = []
        if qtype in {"single", "multiple"}:
            for option in item.get("options") or []:
                if not isinstance(option, dict):
                    continue
                option_text = option.get("text", "").strip()
                if option_text:
                    options.append(
                        {
                            "text": option_text,
                            "jump_to": str(option.get("jump_to", "")).strip(),
                        }
                    )
        clean.append({"content": content, "qtype": qtype, "options": options})
    return clean


def save_questions_from_config(db, survey_id, questions_data):
    questions_data = normalize_questions_config(questions_data)
    question_ids = []
    option_updates = []

    for question_index, item in enumerate(questions_data, start=1):
        qtype = item.get("qtype", "single")
        if qtype not in {"single", "multiple", "text"}:
            qtype = "single"
        content = item.get("content", "").strip()
        if not content:
            continue
        question_id = db.execute(
            """
            INSERT INTO questions (survey_id, content, qtype, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (survey_id, content, qtype, question_index),
        ).lastrowid
        question_ids.append(question_id)
        if qtype in {"single", "multiple"}:
            options = item.get("options") or []
            for option_index, option in enumerate(options, start=1):
                option_text = option.get("text", "").strip()
                if not option_text:
                    continue
                option_id = db.execute(
                    """
                    INSERT INTO options (question_id, content, sort_order)
                    VALUES (?, ?, ?)
                    """,
                    (question_id, option_text, option_index),
                ).lastrowid
                jump_index = str(option.get("jump_to", "")).strip()
                if jump_index.isdigit():
                    option_updates.append((option_id, int(jump_index)))

    for option_id, jump_index in option_updates:
        if 1 <= jump_index <= len(question_ids):
            db.execute(
                "UPDATE options SET jump_to_question_id = ? WHERE id = ?",
                (question_ids[jump_index - 1], option_id),
            )

    return len(question_ids)


def can_manage(survey):
    return g.user is not None and survey["owner_id"] == g.user["id"]


def availability_message(survey):
    if not survey["is_published"]:
        return "问卷尚未发布。"
    now = datetime.now()
    open_at = parse_datetime(survey["open_at"])
    close_at = parse_datetime(survey["close_at"])
    if open_at and now < open_at:
        return f"问卷将在 {survey['open_at']} 开放。"
    if close_at and now > close_at:
        return f"问卷已在 {survey['close_at']} 截止。"
    return None


def create_survey_from_config(owner_id, form, questions_data, published=False):
    db = get_db()
    slug = uuid.uuid4().hex[:10] if published else None
    cursor = db.execute(
        """
        INSERT INTO surveys (
            owner_id, title, description, is_public, is_anonymous, is_published,
            slug, open_at, close_at, target_count, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            owner_id,
            form.get("title", "").strip(),
            form.get("description", "").strip(),
            1 if form.get("is_public") else 0,
            1 if form.get("is_anonymous") else 0,
            1 if published else 0,
            slug,
            form.get("open_at") or None,
            form.get("close_at") or None,
            int(form.get("target_count") or 0),
            now_text(),
        ),
    )
    survey_id = cursor.lastrowid
    save_questions_from_config(db, survey_id, questions_data)
    db.commit()
    return survey_id


def survey_to_template_config(survey_id):
    survey = get_survey_or_404(survey_id)
    questions = load_questions(survey_id)
    data = {
        "title": survey["title"],
        "description": survey["description"],
        "is_public": survey["is_public"],
        "is_anonymous": survey["is_anonymous"],
        "open_at": survey["open_at"],
        "close_at": survey["close_at"],
        "target_count": survey["target_count"],
        "questions": questions_to_config(questions),
    }
    return data


@app.route("/")
def index():
    if g.user:
        return redirect(url_for("surveys"))
    public_surveys = get_db().execute(
        """
        SELECT s.*, u.username AS owner_name
        FROM surveys s JOIN users u ON u.id = s.owner_id
        WHERE s.is_public = 1 AND s.is_published = 1
        ORDER BY s.created_at DESC
        LIMIT 12
        """
    ).fetchall()
    return render_template("index.html", public_surveys=public_surveys)


@app.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if not username or not password:
            flash("用户名和密码不能为空。")
        else:
            try:
                db = get_db()
                db.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), now_text()),
                )
                db.commit()
                flash("注册成功，请登录。")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("用户名已存在。")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("用户名或密码错误。")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash("登录成功。")
            return redirect(url_for("surveys"))
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录。")
    return redirect(url_for("index"))


@app.route("/surveys")
@login_required
def surveys():
    db = get_db()
    mine = db.execute(
        """
        SELECT s.*, COUNT(r.id) AS response_count
        FROM surveys s
        LEFT JOIN responses r ON r.survey_id = s.id
        WHERE s.owner_id = ?
        GROUP BY s.id
        ORDER BY s.created_at DESC
        """,
        (g.user["id"],),
    ).fetchall()
    public_surveys = db.execute(
        """
        SELECT s.*, u.username AS owner_name, COUNT(r.id) AS response_count
        FROM surveys s
        JOIN users u ON u.id = s.owner_id
        LEFT JOIN responses r ON r.survey_id = s.id
        WHERE s.is_public = 1 AND s.is_published = 1
        GROUP BY s.id
        ORDER BY s.created_at DESC
        """
    ).fetchall()
    templates = db.execute(
        "SELECT * FROM templates WHERE user_id = ? ORDER BY created_at DESC", (g.user["id"],)
    ).fetchall()
    return render_template("surveys.html", mine=mine, public_surveys=public_surveys, templates=templates)


@app.route("/survey/new", methods=("GET", "POST"))
@login_required
def new_survey():
    initial = None
    if request.method == "POST":
        questions_json = request.form.get("questions_json", "[]")
        try:
            questions_data = json.loads(questions_json)
        except json.JSONDecodeError:
            questions_data = []
        questions_data = normalize_questions_config(questions_data)
        if not request.form.get("title", "").strip():
            flash("问卷标题不能为空。")
        elif not questions_data:
            flash("至少需要添加一个问题。")
        else:
            survey_id = create_survey_from_config(g.user["id"], request.form, questions_data)
            flash("问卷创建成功，可以继续发布或保存为模板。")
            return redirect(url_for("edit_survey", survey_id=survey_id))
    return render_template("edit_survey.html", survey=None, questions=[], initial=initial)


@app.route("/template/<int:template_id>/use")
@login_required
def use_template(template_id):
    template = get_db().execute(
        "SELECT * FROM templates WHERE id = ? AND user_id = ?", (template_id, g.user["id"])
    ).fetchone()
    if template is None:
        flash("模板不存在。")
        return redirect(url_for("surveys"))
    config = json.loads(template["config_json"])
    return render_template("edit_survey.html", survey=None, questions=[], initial=config)


@app.route("/survey/<int:survey_id>/edit", methods=("GET", "POST"))
@login_required
def edit_survey(survey_id):
    survey = get_survey_or_404(survey_id)
    if survey is None or not can_manage(survey):
        flash("无权操作该问卷。")
        return redirect(url_for("surveys"))
    questions = load_questions(survey_id)
    if request.method == "POST":
        questions_json = request.form.get("questions_json", "[]")
        try:
            questions_data = json.loads(questions_json)
        except json.JSONDecodeError:
            questions_data = []
        questions_data = normalize_questions_config(questions_data)
        response_count = get_db().execute(
            "SELECT COUNT(*) AS total FROM responses WHERE survey_id = ?", (survey_id,)
        ).fetchone()["total"]
        current_config = normalize_questions_config(questions_to_config(questions))
        questions_changed = questions_data != current_config
        if not request.form.get("title", "").strip():
            flash("问卷标题不能为空。")
            return redirect(url_for("edit_survey", survey_id=survey_id))
        if not questions_data:
            flash("至少需要保留一个问题。")
            return redirect(url_for("edit_survey", survey_id=survey_id))
        if response_count and questions_changed:
            flash("该问卷已有答卷，暂不能修改问题结构，避免已有答案错位。")
            return redirect(url_for("edit_survey", survey_id=survey_id))

        db = get_db()
        db.execute(
            """
            UPDATE surveys
            SET title = ?, description = ?, is_public = ?, is_anonymous = ?,
                open_at = ?, close_at = ?, target_count = ?
            WHERE id = ?
            """,
            (
                request.form.get("title", "").strip(),
                request.form.get("description", "").strip(),
                1 if request.form.get("is_public") else 0,
                1 if request.form.get("is_anonymous") else 0,
                request.form.get("open_at") or None,
                request.form.get("close_at") or None,
                int(request.form.get("target_count") or 0),
                survey_id,
            ),
        )
        if questions_changed:
            db.execute(
                "DELETE FROM options WHERE question_id IN (SELECT id FROM questions WHERE survey_id = ?)",
                (survey_id,),
            )
            db.execute("DELETE FROM questions WHERE survey_id = ?", (survey_id,))
            save_questions_from_config(db, survey_id, questions_data)
        db.commit()
        flash("问卷设置和问题结构已保存。")
        return redirect(url_for("edit_survey", survey_id=survey_id))
    return render_template(
        "edit_survey.html",
        survey=survey,
        questions=questions,
        question_config=questions_to_config(questions),
        initial=None,
    )


@app.route("/survey/<int:survey_id>/publish", methods=("POST",))
@login_required
def publish_survey(survey_id):
    survey = get_survey_or_404(survey_id)
    if survey is None or not can_manage(survey):
        flash("无权操作该问卷。")
        return redirect(url_for("surveys"))
    slug = survey["slug"] or uuid.uuid4().hex[:10]
    get_db().execute(
        "UPDATE surveys SET is_published = 1, slug = ? WHERE id = ?", (slug, survey_id)
    )
    get_db().commit()
    flash("问卷已发布。")
    return redirect(url_for("edit_survey", survey_id=survey_id))


@app.route("/survey/<int:survey_id>/delete", methods=("POST",))
@login_required
def delete_survey(survey_id):
    survey = get_survey_or_404(survey_id)
    if survey is None or not can_manage(survey):
        flash("无权操作该问卷。")
        return redirect(url_for("surveys"))
    db = get_db()
    db.execute(
        """
        DELETE FROM answers
        WHERE response_id IN (SELECT id FROM responses WHERE survey_id = ?)
           OR question_id IN (SELECT id FROM questions WHERE survey_id = ?)
        """,
        (survey_id, survey_id),
    )
    db.execute("DELETE FROM responses WHERE survey_id = ?", (survey_id,))
    db.execute(
        "DELETE FROM options WHERE question_id IN (SELECT id FROM questions WHERE survey_id = ?)",
        (survey_id,),
    )
    db.execute("DELETE FROM questions WHERE survey_id = ?", (survey_id,))
    db.execute("DELETE FROM surveys WHERE id = ?", (survey_id,))
    db.commit()
    flash("问卷已删除。")
    return redirect(url_for("surveys"))


@app.route("/survey/<int:survey_id>/template", methods=("POST",))
@login_required
def save_template(survey_id):
    survey = get_survey_or_404(survey_id)
    if survey is None or not can_manage(survey):
        flash("无权操作该问卷。")
        return redirect(url_for("surveys"))
    config = survey_to_template_config(survey_id)
    name = request.form.get("template_name", "").strip() or survey["title"]
    get_db().execute(
        "INSERT INTO templates (user_id, name, config_json, created_at) VALUES (?, ?, ?, ?)",
        (g.user["id"], name, json.dumps(config, ensure_ascii=False), now_text()),
    )
    get_db().commit()
    flash("已保存为模板。")
    return redirect(url_for("surveys"))


@app.route("/template/<int:template_id>/delete", methods=("POST",))
@login_required
def delete_template(template_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM templates WHERE id = ? AND user_id = ?", (template_id, g.user["id"])
    )
    db.commit()
    flash("模板已删除。" if cursor.rowcount else "模板不存在。")
    return redirect(url_for("surveys"))


@app.route("/s/<slug>", methods=("GET", "POST"))
def fill_survey(slug):
    survey = get_db().execute(
        """
        SELECT s.*, u.username AS owner_name
        FROM surveys s JOIN users u ON u.id = s.owner_id
        WHERE s.slug = ?
        """,
        (slug,),
    ).fetchone()
    if survey is None:
        flash("问卷不存在。")
        return redirect(url_for("index"))
    message = availability_message(survey)
    questions = load_questions(survey["id"])
    if message:
        return render_template("fill.html", survey=survey, questions=questions, message=message)

    if request.method == "POST":
        started_at = request.form.get("started_at") or now_text()
        started_time = parse_datetime(started_at)
        duration = int(time.time() - started_time.timestamp()) if started_time else 0
        user_id = None if survey["is_anonymous"] else session.get("user_id")
        db = get_db()
        response_id = db.execute(
            """
            INSERT INTO responses (survey_id, user_id, started_at, submitted_at, duration_seconds)
            VALUES (?, ?, ?, ?, ?)
            """,
            (survey["id"], user_id, started_at, now_text(), max(duration, 0)),
        ).lastrowid

        for bundle in questions:
            question = bundle["question"]
            field = f"q_{question['id']}"
            if question["qtype"] == "text":
                db.execute(
                    "INSERT INTO answers (response_id, question_id, text_answer, option_ids) VALUES (?, ?, ?, ?)",
                    (response_id, question["id"], request.form.get(field, "").strip(), None),
                )
            elif question["qtype"] == "single":
                option_id = request.form.get(field)
                option_ids = json.dumps([int(option_id)]) if option_id else json.dumps([])
                db.execute(
                    "INSERT INTO answers (response_id, question_id, text_answer, option_ids) VALUES (?, ?, ?, ?)",
                    (response_id, question["id"], None, option_ids),
                )
            else:
                option_ids = [int(value) for value in request.form.getlist(field) if value.isdigit()]
                db.execute(
                    "INSERT INTO answers (response_id, question_id, text_answer, option_ids) VALUES (?, ?, ?, ?)",
                    (response_id, question["id"], None, json.dumps(option_ids)),
                )
        db.commit()
        return render_template("thanks.html", survey=survey)

    return render_template("fill.html", survey=survey, questions=questions, message=None, started_at=now_text())


def collect_stats(survey_id):
    db = get_db()
    questions = load_questions(survey_id)
    stats = []
    for bundle in questions:
        question = bundle["question"]
        options = bundle["options"]
        item = {
            "question": question,
            "options": [],
            "texts": [],
            "chart_data": [],
            "chart_type": None,
            "total": 0,
        }
        if question["qtype"] == "text":
            answers = db.execute(
                """
                SELECT text_answer FROM answers
                WHERE question_id = ? AND text_answer IS NOT NULL AND text_answer != ''
                ORDER BY id DESC
                """,
                (question["id"],),
            ).fetchall()
            item["texts"] = [row["text_answer"] for row in answers]
        else:
            counts = {option["id"]: 0 for option in options}
            answers = db.execute(
                "SELECT option_ids FROM answers WHERE question_id = ?", (question["id"],)
            ).fetchall()
            for answer in answers:
                try:
                    selected = json.loads(answer["option_ids"] or "[]")
                except json.JSONDecodeError:
                    selected = []
                for option_id in selected:
                    if option_id in counts:
                        counts[option_id] += 1
            item["options"] = [{"option": option, "count": counts[option["id"]]} for option in options]
            item["chart_data"] = [
                {"label": option["content"], "count": counts[option["id"]]}
                for option in options
            ]
            item["chart_type"] = "donut" if question["qtype"] == "single" else "bar"
            item["total"] = sum(counts.values())
        stats.append(item)
    return stats


def build_chart(survey_id, question_id, option_counts, qtype):
    if plt is None or not option_counts:
        return None
    labels = [row["option"]["content"] for row in option_counts]
    values = [row["count"] for row in option_counts]
    if sum(values) == 0:
        return None
    filename = f"survey_{survey_id}_q_{question_id}.png"
    path = os.path.join(CHART_DIR, filename)
    plt.figure(figsize=(6, 4))
    if qtype == "single":
        plt.pie(values, labels=labels, autopct="%1.0f%%")
        plt.title("单选题占比")
    else:
        plt.bar(labels, values, color="#2563eb")
        plt.title("多选题票数")
        plt.ylabel("票数")
        plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(path, dpi=130)
    plt.close()
    return f"charts/{filename}"


@app.route("/survey/<int:survey_id>/results")
@login_required
def results(survey_id):
    survey = get_survey_or_404(survey_id)
    if survey is None or not can_manage(survey):
        flash("无权查看该问卷结果。")
        return redirect(url_for("surveys"))
    db = get_db()
    response_count = db.execute(
        "SELECT COUNT(*) AS total FROM responses WHERE survey_id = ?", (survey_id,)
    ).fetchone()["total"]
    avg_duration = db.execute(
        "SELECT AVG(duration_seconds) AS avg_time FROM responses WHERE survey_id = ?", (survey_id,)
    ).fetchone()["avg_time"]
    recovery_rate = None
    if survey["target_count"]:
        recovery_rate = round(response_count * 100 / survey["target_count"], 2)
    stats = collect_stats(survey_id)
    return render_template(
        "results.html",
        survey=survey,
        stats=stats,
        response_count=response_count,
        avg_duration=round(avg_duration or 0, 1),
        recovery_rate=recovery_rate,
    )


def export_rows(survey_id):
    survey = get_survey_or_404(survey_id)
    questions = load_questions(survey_id)
    db = get_db()
    responses = db.execute(
        """
        SELECT r.*, u.username
        FROM responses r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.survey_id = ?
        ORDER BY r.id
        """,
        (survey_id,),
    ).fetchall()
    rows = []
    for response in responses:
        base = {
            "response_id": response["id"],
            "user": "匿名" if survey["is_anonymous"] else (response["username"] or "访客"),
            "submitted_at": response["submitted_at"],
            "duration_seconds": response["duration_seconds"],
        }
        for bundle in questions:
            question = bundle["question"]
            answer = db.execute(
                "SELECT * FROM answers WHERE response_id = ? AND question_id = ?",
                (response["id"], question["id"]),
            ).fetchone()
            value = ""
            if answer:
                if question["qtype"] == "text":
                    value = answer["text_answer"] or ""
                else:
                    selected_ids = json.loads(answer["option_ids"] or "[]")
                    option_map = {option["id"]: option["content"] for option in bundle["options"]}
                    value = "；".join(option_map.get(option_id, "") for option_id in selected_ids)
            base[f"Q{question['sort_order']} {question['content']}"] = value
        rows.append(base)
    return rows


@app.route("/survey/<int:survey_id>/export.csv")
@login_required
def export_csv_file(survey_id):
    survey = get_survey_or_404(survey_id)
    if survey is None or not can_manage(survey):
        flash("无权导出该问卷。")
        return redirect(url_for("surveys"))
    rows = export_rows(survey_id)
    output = io.StringIO()
    headers = list(rows[0].keys()) if rows else ["response_id", "user", "submitted_at", "duration_seconds"]
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    data = "\ufeff" + output.getvalue()
    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=survey_{survey_id}.csv"},
    )


@app.route("/survey/<int:survey_id>/export.xlsx")
@login_required
def export_xlsx_file(survey_id):
    survey = get_survey_or_404(survey_id)
    if survey is None or not can_manage(survey):
        flash("无权导出该问卷。")
        return redirect(url_for("surveys"))
    if openpyxl is None:
        flash("openpyxl 未安装，无法导出 Excel。")
        return redirect(url_for("results", survey_id=survey_id))
    rows = export_rows(survey_id)
    headers = list(rows[0].keys()) if rows else ["response_id", "user", "submitted_at", "duration_seconds"]
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "问卷结果"
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    for column in sheet.columns:
        width = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 12), 40)
    file_obj = io.BytesIO()
    workbook.save(file_obj)
    file_obj.seek(0)
    return send_file(
        file_obj,
        as_attachment=True,
        download_name=f"survey_{survey_id}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/s/<slug>/qr.png")
def qr_code(slug):
    survey = get_db().execute("SELECT * FROM surveys WHERE slug = ?", (slug,)).fetchone()
    if survey is None:
        flash("问卷不存在。")
        return redirect(url_for("index"))
    if qrcode is None:
        return Response(
            "当前环境未安装 qrcode，请执行 pip install qrcode[pil] 后刷新二维码。",
            mimetype="text/plain; charset=utf-8",
        )
    link = url_for("fill_survey", slug=slug, _external=True)
    image = qrcode.make(link)
    file_obj = io.BytesIO()
    image.save(file_obj, format="PNG")
    file_obj.seek(0)
    return send_file(file_obj, mimetype="image/png")


@app.context_processor
def inject_helpers():
    return {"availability_message": availability_message}


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
