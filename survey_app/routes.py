"""页面路由。

routes.py 负责处理 HTTP 请求：
- 从 request 中读取表单参数；
- 调用 models/stats/exports 中的业务函数；
- 根据结果渲染模板或跳转页面。

为了保持模板兼容，函数名保留了原来的命名，例如 EditSurvey、FillSurvey，
这样 templates 中已有的 url_for("EditSurvey") 不需要改。
"""

import io
import json
import sqlite3
import time
import uuid

from flask import (
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

from .auth import LoginRequired
from .database import GetDb
from .dependencies import openpyxl, qrcode
from .exports import BuildCsvResponse, BuildXlsxResponse
from .models import (
    AvailabilityMessage,
    CanManage,
    CreateSurveyFromConfig,
    GetSurveyOr404,
    LoadQuestions,
    NormalizeQuestionsConfig,
    QuestionsToConfig,
    SaveQuestionsFromConfig,
    SurveyToTemplateConfig,
)
from .stats import CollectStats
from .utils import NowText, ParseDatetime


def RegisterRoutes(app):
    """把所有 URL 规则注册到 Flask 应用上。
    """

   
    @app.route("/")
    def index():
        """首页。
        已登录用户直接进入问卷列表；未登录用户展示公开且已发布的问卷。
        """


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

   
   
   
    @app.route("/register", methods=("GET", "POST"))
    def register():
        """注册账号。
        POST 时保存密码哈希，不保存明文密码。
        """
        
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

    
    
    @app.route("/login", methods=("GET", "POST"))
    def login():
        """登录账号。
        校验通过后把用户 id 写入 session，后续请求通过 auth.LoadLoggedInUser
        自动加载 g.user。
        """
    
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

    
    @app.route("/logout")
    def logout():
        """退出登录，清空 session。
        """
    
        session.clear()
        flash("已退出登录。")
        return redirect(url_for("index"))



    @app.route("/surveys")
    @LoginRequired
    def surveys(): 
        """问卷列表页。
        同时查询三类数据：
        - mine：当前用户创建的问卷；
        - PublicSurveys：所有公开已发布问卷；
        - templates：当前用户保存的模板。
        """
        
        
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



    @app.route("/survey/new", methods=("GET", "POST"))
    @LoginRequired
    def NewSurvey():
        """创建问卷。
        前端编辑器会把题目列表序列化到 questions_json 字段。
        后端先解析 JSON，再调用 NormalizeQuestionsConfig 做安全清洗。
        """
        
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
                # 创建成功后进入编辑页，用户可以继续发布或保存为模板。                
                SurveyId = CreateSurveyFromConfig(g.user["id"], request.form, QuestionsData)
                flash("问卷创建成功，可以继续发布或保存为模板。")
                return redirect(url_for("EditSurvey", SurveyId=SurveyId))
        return render_template("edit_survey.html", survey=None, questions=[], initial=initial)

    
    @app.route("/template/<int:TemplateId>/use")
    @LoginRequired
    def UseTemplate(TemplateId):
        """使用模板创建问卷。
        这里不直接写数据库，而是把模板配置传给编辑页，让用户确认后再保存。
        """


        template = GetDb().execute(
            "SELECT * FROM templates WHERE id = ? AND user_id = ?", (TemplateId, g.user["id"])
        ).fetchone()
        if template is None:
            flash("模板不存在。")
            return redirect(url_for("surveys"))
        config = json.loads(template["config_json"])
        return render_template("edit_survey.html", survey=None, questions=[], initial=config)

    
    @app.route("/survey/<int:SurveyId>/edit", methods=("GET", "POST"))
    @LoginRequired
    def EditSurvey(SurveyId):
        """编辑问卷。
        如果问卷已经收到答卷，则不允许修改题目结构，避免已有答案和新题目对不上。
        但标题、说明、公开状态、匿名设置、开放时间等基础信息仍可修改。
        """

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

            
            # 用当前数据库结构和新提交结构做对比，判断题目是否真的发生变化。
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
                # 先删旧选项，再删旧题目，然后按新配置重建。这里是数据库删除记录，
                # 不是文件删除，符合项目数据更新需求。
    
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



    @app.route("/survey/<int:SurveyId>/publish", methods=("POST",))
    @LoginRequired
    def PublishSurvey(SurveyId):
        """发布问卷。
        slug 是公开填写链接中的短标识。如果问卷之前发布过，就复用原 slug。
        """
        
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



    @app.route("/survey/<int:SurveyId>/delete", methods=("POST",))
    @LoginRequired
    def DeleteSurvey(SurveyId):
        """删除问卷及其相关数据。
        虽然数据库有部分 ON DELETE CASCADE，但这里显式按依赖顺序删除，
        更便于课程展示“先删答案，再删答卷/选项/题目，最后删问卷”的关系。
        """
        
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




    @app.route("/survey/<int:SurveyId>/template", methods=("POST",))
    @LoginRequired
    def SaveTemplate(SurveyId):
        """把当前问卷保存为模板。
        """


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


    @app.route("/template/<int:TemplateId>/delete", methods=("POST",))
    @LoginRequired
    def DeleteTemplate(TemplateId):
        """删除当前用户自己的模板。
        """

        db = GetDb()
        cursor = db.execute(
            "DELETE FROM templates WHERE id = ? AND user_id = ?", (TemplateId, g.user["id"])
        )
        db.commit()
        flash("模板已删除。" if cursor.rowcount else "模板不存在。")
        return redirect(url_for("surveys"))




    @app.route("/s/<slug>", methods=("GET", "POST"))
    def FillSurvey(slug):
        """填写问卷。
        GET 展示问卷题目，POST 保存用户提交的答案。
        匿名问卷不会记录 session 中的 user_id。
        """
        
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
            # 问卷未发布、未到开放时间或已截止时，只展示提示，不允许提交。
            return render_template("fill.html", survey=survey, questions=questions, message=message)

        if request.method == "POST":
            StartedAt = request.form.get("started_at") or NowText()
            StartedTime = ParseDatetime(StartedAt)

            # 用页面加载时间和提交时间估算答题时长，结果页可计算平均答题时间。
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
                    # 填空题直接保存文本答案。
                    db.execute(
                        "INSERT INTO answers (response_id, question_id, text_answer, option_ids) VALUES (?, ?, ?, ?)",
                        (ResponseId, question["id"], request.form.get(field, "").strip(), None),
                    )
                elif question["qtype"] == "single":
                    # 单选题也保存成 JSON 数组，统计时可以和多选题使用同一套逻辑。
                    OptionId = request.form.get(field)
                    OptionIds = json.dumps([int(OptionId)]) if OptionId else json.dumps([])
                    db.execute(
                        "INSERT INTO answers (response_id, question_id, text_answer, option_ids) VALUES (?, ?, ?, ?)",
                        (ResponseId, question["id"], None, OptionIds),
                    )
                else:
                    # 多选题从表单中读取多个选项 id，并过滤非数字值。
                    OptionIds = [int(value) for value in request.form.getlist(field) if value.isdigit()]
                    db.execute(
                        "INSERT INTO answers (response_id, question_id, text_answer, option_ids) VALUES (?, ?, ?, ?)",
                        (ResponseId, question["id"], None, json.dumps(OptionIds)),
                    )
            db.commit()
            return render_template("thanks.html", survey=survey)

        return render_template(
            "fill.html", survey=survey, questions=questions, message=None, StartedAt=NowText()
        )




    @app.route("/survey/<int:SurveyId>/results")
    @LoginRequired
    def results(SurveyId):
        """结果统计页。
        这里统计整体回收情况；每道题的详细统计交给 stats.CollectStats。
        """
        
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
            # 目标回收数不为 0 时，才计算回收率。
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


    @app.route("/survey/<int:SurveyId>/export.csv")
    @LoginRequired
    def ExportCsvFile(SurveyId):
        """导出 CSV 文件。
        """
        
        survey = GetSurveyOr404(SurveyId)
        if survey is None or not CanManage(survey):
            flash("无权导出该问卷。")
            return redirect(url_for("surveys"))
        return BuildCsvResponse(SurveyId)



    @app.route("/survey/<int:SurveyId>/export.xlsx")
    @LoginRequired
    def ExportXlsxFile(SurveyId):
        """导出 Excel 文件。
        """
        
        survey = GetSurveyOr404(SurveyId)
        if survey is None or not CanManage(survey):
            flash("无权导出该问卷。")
            return redirect(url_for("surveys"))
        if openpyxl is None:
            # openpyxl 是增强依赖，缺失时不报错，回到结果页即可。
            return redirect(url_for("results", SurveyId=SurveyId))
        return BuildXlsxResponse(SurveyId)



    @app.route("/s/<slug>/qr.png")
    def QrCode(slug):
        """生成问卷填写链接二维码。
        """

        survey = GetDb().execute("SELECT * FROM surveys WHERE slug = ?", (slug,)).fetchone()
        if survey is None:
            flash("问卷不存在。")
            return redirect(url_for("index"))
        if qrcode is None:
            # 二维码依赖缺失时返回 204，页面不会崩溃。
            return Response(status=204)
        link = url_for("FillSurvey", slug=slug, _external=True)
        image = qrcode.make(link)
        FileObj = io.BytesIO()
        image.save(FileObj, format="PNG")
        FileObj.seek(0)
        return send_file(FileObj, mimetype="image/png")



    @app.context_processor
    def InjectHelpers():
        """给所有模板注入可直接调用的辅助函数。
        """
        
        return {"AvailabilityMessage": AvailabilityMessage}
