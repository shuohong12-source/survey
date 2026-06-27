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

# 这些第三方库只用于增强功能；即使缺少它们，基础问卷功能也要正常运行。
try:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager as FontManager
    import matplotlib.pyplot as plt

    for FontPath in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        if os.path.exists(FontPath):
            FontManager.fontManager.addfont(FontPath)
            plt.rcParams["font.sans-serif"] = [FontManager.FontProperties(fname=FontPath).get_name()]
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


BaseDir = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BaseDir, "survey.db")
ChartDir = os.path.join(BaseDir, "static", "charts")

app = Flask(__name__)
app.config["SECRET_KEY"] = "survey-course-project-secret"


# 数据库连接：每个请求复用 g.db，请求结束后统一关闭，避免到处手动管理连接。
def GetDb():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def CloseDb(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# 初始化数据库表结构：包含用户、问卷、题目、选项、答卷、答案和模板。
# 外键的 ON DELETE CASCADE 用来保证删除问卷时相关题目、答案能跟着清理。
def InitDb():
    os.makedirs(ChartDir, exist_ok=True)
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


# 时间统一用字符串保存，页面表单提交的 datetime-local 会在这里转换成 Python 时间对象。
def NowText():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ParseDatetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("T", " "))
    except ValueError:
        return None


# 登录保护装饰器：需要登录后才能进入问卷管理、编辑、统计和导出功能。
def LoginRequired(view):
    @wraps(view)
    def WrappedView(**kwargs):
        if "user_id" not in session:
            flash("请先登录。")
            return redirect(url_for("login"))
        return view(**kwargs)

    return WrappedView


@app.before_request
def LoadLoggedInUser():
    UserId = session.get("user_id")
    g.user = None
    if UserId is not None:
        g.user = GetDb().execute("SELECT * FROM users WHERE id = ?", (UserId,)).fetchone()


# 读取问卷基础信息，并带上创建者用户名，供列表、编辑和结果页展示。
def GetSurveyOr404(SurveyId):
    survey = GetDb().execute(
        """
        SELECT s.*, u.username AS owner_name
        FROM surveys s
        JOIN users u ON u.id = s.owner_id
        WHERE s.id = ?
        """,
        (SurveyId,),
    ).fetchone()
    if survey is None:
        return None
    return survey


# 读取问卷的完整题目结构：每个题目下包含自己的选项列表。
def LoadQuestions(SurveyId):
    db = GetDb()
    questions = db.execute(
        "SELECT * FROM questions WHERE survey_id = ? ORDER BY sort_order", (SurveyId,)
    ).fetchall()
    result = []
    for question in questions:
        options = db.execute(
            "SELECT * FROM options WHERE question_id = ? ORDER BY sort_order", (question["id"],)
        ).fetchall()
        result.append({"question": question, "options": options})
    return result


# 把数据库里的题目和选项转换成前端编辑器使用的 JSON 配置。
# jump_to 保存为题号而不是数据库 id，方便用户在页面上理解和填写跳转逻辑。
def QuestionsToConfig(questions):
    QuestionOrder = {bundle["question"]["id"]: bundle["question"]["sort_order"] for bundle in questions}
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
                        "jump_to": QuestionOrder.get(option["jump_to_question_id"], ""),
                    }
                    for option in bundle["options"]
                ],
            }
        )
    return data


# 清洗前端提交的问题配置：只保留合法题型、非空题目和非空选项。
# 这一步能避免无效 JSON、空题目或错误题型直接写入数据库。
def NormalizeQuestionsConfig(QuestionsData):
    if not isinstance(QuestionsData, list):
        return []
    clean = []
    for item in QuestionsData:
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
                OptionText = option.get("text", "").strip()
                if OptionText:
                    options.append(
                        {
                            "text": OptionText,
                            "jump_to": str(option.get("jump_to", "")).strip(),
                        }
                    )
        clean.append({"content": content, "qtype": qtype, "options": options})
    return clean


# 保存题目配置，并在所有题目创建完成后再回填选项跳转目标。
# 这样可以支持“选项跳到后面的题目”，因为后面题目的数据库 id 创建后才知道。
def SaveQuestionsFromConfig(db, SurveyId, QuestionsData):
    QuestionsData = NormalizeQuestionsConfig(QuestionsData)
    QuestionIds = []
    OptionUpdates = []

    for QuestionIndex, item in enumerate(QuestionsData, start=1):
        qtype = item.get("qtype", "single")
        if qtype not in {"single", "multiple", "text"}:
            qtype = "single"
        content = item.get("content", "").strip()
        if not content:
            continue
        QuestionId = db.execute(
            """
            INSERT INTO questions (survey_id, content, qtype, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (SurveyId, content, qtype, QuestionIndex),
        ).lastrowid
        QuestionIds.append(QuestionId)
        if qtype in {"single", "multiple"}:
            options = item.get("options") or []
            for OptionIndex, option in enumerate(options, start=1):
                OptionText = option.get("text", "").strip()
                if not OptionText:
                    continue
                OptionId = db.execute(
                    """
                    INSERT INTO options (question_id, content, sort_order)
                    VALUES (?, ?, ?)
                    """,
                    (QuestionId, OptionText, OptionIndex),
                ).lastrowid
                JumpIndex = str(option.get("jump_to", "")).strip()
                if JumpIndex.isdigit():
                    OptionUpdates.append((OptionId, int(JumpIndex)))

    for OptionId, JumpIndex in OptionUpdates:
        if 1 <= JumpIndex <= len(QuestionIds):
            db.execute(
                "UPDATE options SET jump_to_question_id = ? WHERE id = ?",
                (QuestionIds[JumpIndex - 1], OptionId),
            )

    return len(QuestionIds)


# 权限判断：目前只有问卷创建者可以编辑、删除、查看统计和导出数据。
def CanManage(survey):
    return g.user is not None and survey["owner_id"] == g.user["id"]


# 判断问卷当前是否可填写：未发布、未到开放时间、已过截止时间都会给出提示。
def AvailabilityMessage(survey):
    if not survey["is_published"]:
        return "问卷尚未发布。"
    now = datetime.now()
    OpenAt = ParseDatetime(survey["open_at"])
    CloseAt = ParseDatetime(survey["close_at"])
    if OpenAt and now < OpenAt:
        return f"问卷将在 {survey['open_at']} 开放。"
    if CloseAt and now > CloseAt:
        return f"问卷已在 {survey['close_at']} 截止。"
    return None


# 从表单和题目 JSON 创建问卷；发布时会生成 slug，用于公开填写链接。
def CreateSurveyFromConfig(OwnerId, form, QuestionsData, published=False):
    db = GetDb()
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
            OwnerId,
            form.get("title", "").strip(),
            form.get("description", "").strip(),
            1 if form.get("is_public") else 0,
            1 if form.get("is_anonymous") else 0,
            1 if published else 0,
            slug,
            form.get("open_at") or None,
            form.get("close_at") or None,
            int(form.get("target_count") or 0),
            NowText(),
        ),
    )
    SurveyId = cursor.lastrowid
    SaveQuestionsFromConfig(db, SurveyId, QuestionsData)
    db.commit()
    return SurveyId


# 把已有问卷打包成模板配置，模板复用时可以直接带回编辑页面。
def SurveyToTemplateConfig(SurveyId):
    survey = GetSurveyOr404(SurveyId)
    questions = LoadQuestions(SurveyId)
    data = {
        "title": survey["title"],
        "description": survey["description"],
        "is_public": survey["is_public"],
        "is_anonymous": survey["is_anonymous"],
        "open_at": survey["open_at"],
        "close_at": survey["close_at"],
        "target_count": survey["target_count"],
        "questions": QuestionsToConfig(questions),
    }
    return data


# 首页：未登录用户看到公开且已发布的问卷，已登录用户直接进入管理列表。
@app.route("/")
def index():
    if g.user:
        return redirect(url_for("surveys"))
    PublicSurveys = GetDb().execute(
        """
        SELECT s.*, u.username AS owner_name
        FROM surveys s JOIN users u ON u.id = s.owner_id
        WHERE s.is_public = 1 AND s.is_published = 1
        ORDER BY s.created_at DESC
        LIMIT 12
        """
    ).fetchall()
    return render_template("index.html", PublicSurveys=PublicSurveys)


# 注册：保存密码哈希，不直接保存明文密码。
@app.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if not username or not password:
            flash("用户名和密码不能为空。")
        else:
            try:
                db = GetDb()
                db.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), NowText()),
                )
                db.commit()
                flash("注册成功，请登录。")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("用户名已存在。")
    return render_template("auth.html", mode="register")


# 登录：校验用户名和密码后，把用户 id 写入 session。
@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = GetDb().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("用户名或密码错误。")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash("登录成功。")
            return redirect(url_for("surveys"))
    return render_template("auth.html", mode="login")


# 退出登录：清空 session 后回到首页。
@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录。")
    return redirect(url_for("index"))


# 问卷列表页：同时展示“我的问卷”“公开问卷”和“我的模板”。
@app.route("/surveys")
@LoginRequired
def surveys():
    db = GetDb()
    mine = db.execute(
        """
        SELECT s.*, COUNT(r.id) AS ResponseCount
        FROM surveys s
        LEFT JOIN responses r ON r.survey_id = s.id
        WHERE s.owner_id = ?
        GROUP BY s.id
        ORDER BY s.created_at DESC
        """,
        (g.user["id"],),
    ).fetchall()
    PublicSurveys = db.execute(
        """
        SELECT s.*, u.username AS owner_name, COUNT(r.id) AS ResponseCount
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
    return render_template("surveys.html", mine=mine, PublicSurveys=PublicSurveys, templates=templates)


# 新建问卷：页面通过 questions_json 提交题目结构，后端统一清洗后保存。
@app.route("/survey/new", methods=("GET", "POST"))
@LoginRequired
def NewSurvey():
    initial = None
    if request.method == "POST":
        QuestionsJson = request.form.get("questions_json", "[]")
        try:
            QuestionsData = json.loads(QuestionsJson)
        except json.JSONDecodeError:
            QuestionsData = []
        QuestionsData = NormalizeQuestionsConfig(QuestionsData)
        if not request.form.get("title", "").strip():
            flash("问卷标题不能为空。")
        elif not QuestionsData:
            flash("至少需要添加一个问题。")
        else:
            SurveyId = CreateSurveyFromConfig(g.user["id"], request.form, QuestionsData)
            flash("问卷创建成功，可以继续发布或保存为模板。")
            return redirect(url_for("EditSurvey", SurveyId=SurveyId))
    return render_template("edit_survey.html", survey=None, questions=[], initial=initial)


# 使用模板：把模板里的 JSON 配置作为初始数据传给问卷编辑页。
@app.route("/template/<int:TemplateId>/use")
@LoginRequired
def UseTemplate(TemplateId):
    template = GetDb().execute(
        "SELECT * FROM templates WHERE id = ? AND user_id = ?", (TemplateId, g.user["id"])
    ).fetchone()
    if template is None:
        flash("模板不存在。")
        return redirect(url_for("surveys"))
    config = json.loads(template["config_json"])
    return render_template("edit_survey.html", survey=None, questions=[], initial=config)


# 编辑问卷：允许修改标题、说明、开放时间等；已有答卷后禁止改题目结构，避免答案错位。
@app.route("/survey/<int:SurveyId>/edit", methods=("GET", "POST"))
@LoginRequired
def EditSurvey(SurveyId):
    survey = GetSurveyOr404(SurveyId)
    if survey is None or not CanManage(survey):
        flash("无权操作该问卷。")
        return redirect(url_for("surveys"))
    questions = LoadQuestions(SurveyId)
    if request.method == "POST":
        QuestionsJson = request.form.get("questions_json", "[]")
        try:
            QuestionsData = json.loads(QuestionsJson)
        except json.JSONDecodeError:
            QuestionsData = []
        QuestionsData = NormalizeQuestionsConfig(QuestionsData)
        ResponseCount = GetDb().execute(
            "SELECT COUNT(*) AS total FROM responses WHERE survey_id = ?", (SurveyId,)
        ).fetchone()["total"]
        CurrentConfig = NormalizeQuestionsConfig(QuestionsToConfig(questions))
        QuestionsChanged = QuestionsData != CurrentConfig
        if not request.form.get("title", "").strip():
            flash("问卷标题不能为空。")
            return redirect(url_for("EditSurvey", SurveyId=SurveyId))
        if not QuestionsData:
            flash("至少需要保留一个问题。")
            return redirect(url_for("EditSurvey", SurveyId=SurveyId))
        if ResponseCount and QuestionsChanged:
            flash("该问卷已有答卷，暂不能修改问题结构，避免已有答案错位。")
            return redirect(url_for("EditSurvey", SurveyId=SurveyId))

        db = GetDb()
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
                SurveyId,
            ),
        )
        if QuestionsChanged:
            db.execute(
                "DELETE FROM options WHERE question_id IN (SELECT id FROM questions WHERE survey_id = ?)",
                (SurveyId,),
            )
            db.execute("DELETE FROM questions WHERE survey_id = ?", (SurveyId,))
            SaveQuestionsFromConfig(db, SurveyId, QuestionsData)
        db.commit()
        flash("问卷设置和问题结构已保存。")
        return redirect(url_for("EditSurvey", SurveyId=SurveyId))
    return render_template(
        "edit_survey.html",
        survey=survey,
        questions=questions,
        QuestionConfig=QuestionsToConfig(questions),
        initial=None,
    )


# 发布问卷：生成或复用 slug，让问卷拥有可分享的填写地址和二维码。
@app.route("/survey/<int:SurveyId>/publish", methods=("POST",))
@LoginRequired
def PublishSurvey(SurveyId):
    survey = GetSurveyOr404(SurveyId)
    if survey is None or not CanManage(survey):
        flash("无权操作该问卷。")
        return redirect(url_for("surveys"))
    slug = survey["slug"] or uuid.uuid4().hex[:10]
    GetDb().execute(
        "UPDATE surveys SET is_published = 1, slug = ? WHERE id = ?", (slug, SurveyId)
    )
    GetDb().commit()
    flash("问卷已发布。")
    return redirect(url_for("EditSurvey", SurveyId=SurveyId))


# 删除问卷：按答案、答卷、选项、题目、问卷的顺序清理，保证外键关系不冲突。
@app.route("/survey/<int:SurveyId>/delete", methods=("POST",))
@LoginRequired
def DeleteSurvey(SurveyId):
    survey = GetSurveyOr404(SurveyId)
    if survey is None or not CanManage(survey):
        flash("无权操作该问卷。")
        return redirect(url_for("surveys"))
    db = GetDb()
    db.execute(
        """
        DELETE FROM answers
        WHERE response_id IN (SELECT id FROM responses WHERE survey_id = ?)
           OR question_id IN (SELECT id FROM questions WHERE survey_id = ?)
        """,
        (SurveyId, SurveyId),
    )
    db.execute("DELETE FROM responses WHERE survey_id = ?", (SurveyId,))
    db.execute(
        "DELETE FROM options WHERE question_id IN (SELECT id FROM questions WHERE survey_id = ?)",
        (SurveyId,),
    )
    db.execute("DELETE FROM questions WHERE survey_id = ?", (SurveyId,))
    db.execute("DELETE FROM surveys WHERE id = ?", (SurveyId,))
    db.commit()
    flash("问卷已删除。")
    return redirect(url_for("surveys"))


# 保存模板：把当前问卷结构保存成 JSON，后续可以快速生成相似问卷。
@app.route("/survey/<int:SurveyId>/template", methods=("POST",))
@LoginRequired
def SaveTemplate(SurveyId):
    survey = GetSurveyOr404(SurveyId)
    if survey is None or not CanManage(survey):
        flash("无权操作该问卷。")
        return redirect(url_for("surveys"))
    config = SurveyToTemplateConfig(SurveyId)
    name = request.form.get("template_name", "").strip() or survey["title"]
    GetDb().execute(
        "INSERT INTO templates (user_id, name, config_json, created_at) VALUES (?, ?, ?, ?)",
        (g.user["id"], name, json.dumps(config, ensure_ascii=False), NowText()),
    )
    GetDb().commit()
    flash("已保存为模板。")
    return redirect(url_for("surveys"))


# 删除模板：只允许删除当前登录用户自己的模板。
@app.route("/template/<int:TemplateId>/delete", methods=("POST",))
@LoginRequired
def DeleteTemplate(TemplateId):
    db = GetDb()
    cursor = db.execute(
        "DELETE FROM templates WHERE id = ? AND user_id = ?", (TemplateId, g.user["id"])
    )
    db.commit()
    flash("模板已删除。" if cursor.rowcount else "模板不存在。")
    return redirect(url_for("surveys"))


# 填写问卷：GET 展示题目，POST 保存答卷；匿名问卷不会记录填写用户 id。
@app.route("/s/<slug>", methods=("GET", "POST"))
def FillSurvey(slug):
    survey = GetDb().execute(
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
    message = AvailabilityMessage(survey)
    questions = LoadQuestions(survey["id"])
    if message:
        return render_template("fill.html", survey=survey, questions=questions, message=message)

    if request.method == "POST":
        StartedAt = request.form.get("started_at") or NowText()
        StartedTime = ParseDatetime(StartedAt)
        duration = int(time.time() - StartedTime.timestamp()) if StartedTime else 0
        UserId = None if survey["is_anonymous"] else session.get("user_id")
        db = GetDb()
        ResponseId = db.execute(
            """
            INSERT INTO responses (survey_id, user_id, started_at, submitted_at, duration_seconds)
            VALUES (?, ?, ?, ?, ?)
            """,
            (survey["id"], UserId, StartedAt, NowText(), max(duration, 0)),
        ).lastrowid

        for bundle in questions:
            question = bundle["question"]
            field = f"q_{question['id']}"
            if question["qtype"] == "text":
                db.execute(
                    "INSERT INTO answers (response_id, question_id, text_answer, option_ids) VALUES (?, ?, ?, ?)",
                    (ResponseId, question["id"], request.form.get(field, "").strip(), None),
                )
            elif question["qtype"] == "single":
                # 单选题也用 JSON 数组保存，和多选题共用同一套统计逻辑。
                OptionId = request.form.get(field)
                OptionIds = json.dumps([int(OptionId)]) if OptionId else json.dumps([])
                db.execute(
                    "INSERT INTO answers (response_id, question_id, text_answer, option_ids) VALUES (?, ?, ?, ?)",
                    (ResponseId, question["id"], None, OptionIds),
                )
            else:
                OptionIds = [int(value) for value in request.form.getlist(field) if value.isdigit()]
                db.execute(
                    "INSERT INTO answers (response_id, question_id, text_answer, option_ids) VALUES (?, ?, ?, ?)",
                    (ResponseId, question["id"], None, json.dumps(OptionIds)),
                )
        db.commit()
        return render_template("thanks.html", survey=survey)

    return render_template("fill.html", survey=survey, questions=questions, message=None, StartedAt=NowText())


# 统计结果：填空题收集文本答案，选择题按选项累计票数并生成前端图表数据。
def CollectStats(SurveyId):
    db = GetDb()
    questions = LoadQuestions(SurveyId)
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
                for OptionId in selected:
                    if OptionId in counts:
                        counts[OptionId] += 1
            item["options"] = [{"option": option, "count": counts[option["id"]]} for option in options]
            item["chart_data"] = [
                {"label": option["content"], "count": counts[option["id"]]}
                for option in options
            ]
            item["chart_type"] = "donut" if question["qtype"] == "single" else "bar"
            item["total"] = sum(counts.values())
        stats.append(item)
    return stats


# 旧版静态图表生成函数：保留给可能的扩展使用，当前结果页主要使用前端交互图表。
def BuildChart(SurveyId, QuestionId, OptionCounts, qtype):
    if plt is None or not OptionCounts:
        return None
    labels = [row["option"]["content"] for row in OptionCounts]
    values = [row["count"] for row in OptionCounts]
    if sum(values) == 0:
        return None
    filename = f"survey_{SurveyId}_q_{QuestionId}.png"
    path = os.path.join(ChartDir, filename)
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


# 结果页：汇总回收数量、平均答题时长、回收率，并展示每道题的统计结果。
@app.route("/survey/<int:SurveyId>/results")
@LoginRequired
def results(SurveyId):
    survey = GetSurveyOr404(SurveyId)
    if survey is None or not CanManage(survey):
        flash("无权查看该问卷结果。")
        return redirect(url_for("surveys"))
    db = GetDb()
    ResponseCount = db.execute(
        "SELECT COUNT(*) AS total FROM responses WHERE survey_id = ?", (SurveyId,)
    ).fetchone()["total"]
    AvgDuration = db.execute(
        "SELECT AVG(duration_seconds) AS avg_time FROM responses WHERE survey_id = ?", (SurveyId,)
    ).fetchone()["avg_time"]
    RecoveryRate = None
    if survey["target_count"]:
        RecoveryRate = round(ResponseCount * 100 / survey["target_count"], 2)
    stats = CollectStats(SurveyId)
    return render_template(
        "results.html",
        survey=survey,
        stats=stats,
        ResponseCount=ResponseCount,
        AvgDuration=round(AvgDuration or 0, 1),
        RecoveryRate=RecoveryRate,
    )


# 导出数据行：每份答卷是一行，每道题展开成一列，选择题导出选项文字。
def ExportRows(SurveyId):
    survey = GetSurveyOr404(SurveyId)
    questions = LoadQuestions(SurveyId)
    db = GetDb()
    responses = db.execute(
        """
        SELECT r.*, u.username
        FROM responses r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.survey_id = ?
        ORDER BY r.id
        """,
        (SurveyId,),
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
                    # 数据库存的是选项 id，导出时转换为用户能看懂的选项文字。
                    SelectedIds = json.loads(answer["option_ids"] or "[]")
                    OptionMap = {option["id"]: option["content"] for option in bundle["options"]}
                    value = "；".join(OptionMap.get(OptionId, "") for OptionId in SelectedIds)
            base[f"Q{question['sort_order']} {question['content']}"] = value
        rows.append(base)
    return rows


# CSV 导出：加 UTF-8 BOM，方便 Excel 直接打开中文不乱码。
@app.route("/survey/<int:SurveyId>/export.csv")
@LoginRequired
def ExportCsvFile(SurveyId):
    survey = GetSurveyOr404(SurveyId)
    if survey is None or not CanManage(survey):
        flash("无权导出该问卷。")
        return redirect(url_for("surveys"))
    rows = ExportRows(SurveyId)
    output = io.StringIO()
    headers = list(rows[0].keys()) if rows else ["response_id", "user", "submitted_at", "duration_seconds"]
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    data = "\ufeff" + output.getvalue()
    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=survey_{SurveyId}.csv"},
    )


# Excel 导出：在 CSV 的基础上增加表头加粗和列宽自适应。
@app.route("/survey/<int:SurveyId>/export.xlsx")
@LoginRequired
def ExportXlsxFile(SurveyId):
    survey = GetSurveyOr404(SurveyId)
    if survey is None or not CanManage(survey):
        flash("无权导出该问卷。")
        return redirect(url_for("surveys"))
    if openpyxl is None:
        # Excel 导出依赖 openpyxl；缺少时直接回到结果页，不把安装细节展示给用户。
        return redirect(url_for("results", SurveyId=SurveyId))
    rows = ExportRows(SurveyId)
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
    FileObj = io.BytesIO()
    workbook.save(FileObj)
    FileObj.seek(0)
    return send_file(
        FileObj,
        as_attachment=True,
        download_name=f"survey_{SurveyId}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# 二维码接口：根据问卷填写链接生成 PNG，编辑页直接把它当图片加载。
@app.route("/s/<slug>/qr.png")
def QrCode(slug):
    survey = GetDb().execute("SELECT * FROM surveys WHERE slug = ?", (slug,)).fetchone()
    if survey is None:
        flash("问卷不存在。")
        return redirect(url_for("index"))
    if qrcode is None:
        # 二维码图片依赖 qrcode；缺少时返回空响应，避免页面出现安装命令说明。
        return Response(status=204)
    link = url_for("FillSurvey", slug=slug, _external=True)
    image = qrcode.make(link)
    FileObj = io.BytesIO()
    image.save(FileObj, format="PNG")
    FileObj.seek(0)
    return send_file(FileObj, mimetype="image/png")


# 模板里可直接调用 AvailabilityMessage，用来显示问卷是否可填写。
@app.context_processor
def InjectHelpers():
    return {"AvailabilityMessage": AvailabilityMessage}


if __name__ == "__main__":
    InitDb()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
