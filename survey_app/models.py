"""问卷核心数据逻辑。

这个模块不直接处理页面渲染，而是负责“数据怎么查、怎么清洗、怎么保存”。
路由层调用这些函数，就能让 routes.py 不至于堆满数据库细节。
"""

import uuid
from datetime import datetime

from flask import g

from .database import GetDb
from .utils import NowText, ParseDatetime


def GetSurveyOr404(SurveyId):
    """读取单个问卷，并带上创建者用户名。

    函数名沿用了早期写法中的 Or404，但当前实现返回 None，由路由层决定
    是提示“无权操作”还是跳转回列表页。
    """
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


def LoadQuestions(SurveyId):
    """读取问卷的完整题目结构。

    返回格式是一个列表，每一项包含：
    - question：题目行；
    - options：该题目的所有选项。

    这种结构非常适合模板渲染，也方便统计和导出复用。
    """
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


def QuestionsToConfig(questions):
    """把数据库题目结构转换成前端编辑器使用的 JSON 配置。

    数据库中的跳题目标保存为 jump_to_question_id，也就是题目的数据库 id。
    但用户在页面上更容易理解“跳到第几题”，所以这里转换成 sort_order。
    """
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


def NormalizeQuestionsConfig(QuestionsData):
    """清洗前端提交的问题 JSON。

    前端传来的 questions_json 可能出现这些情况：
    - 不是列表；
    - 某一项不是字典；
    - 题型被篡改；
    - 题目内容为空；
    - 选项为空。

    统一清洗后再写入数据库，可以减少无效数据和后续统计错误。
    """
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

        # 填空题不需要选项；单选题和多选题才需要保存选项列表。
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


def SaveQuestionsFromConfig(db, SurveyId, QuestionsData):
    """把清洗后的题目配置写入 questions 和 options 表。

    跳题保存分两步：
    1. 先创建所有题目，拿到每道题真实的数据库 id；
    2. 再根据用户填写的“跳到第几题”回填 options.jump_to_question_id。

    这样可以支持“第 1 题的某个选项跳到第 5 题”，即使第 5 题在保存前还没有 id。
    """
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

        # sort_order 从 1 开始，既用于页面排序，也用于跳题时的人类可读题号。
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
                    # 此时先保存“选项 id -> 目标题号”，等所有题目都创建完再转成目标题目 id。
                    OptionUpdates.append((OptionId, int(JumpIndex)))

    for OptionId, JumpIndex in OptionUpdates:
        if 1 <= JumpIndex <= len(QuestionIds):
            db.execute(
                "UPDATE options SET jump_to_question_id = ? WHERE id = ?",
                (QuestionIds[JumpIndex - 1], OptionId),
            )

    return len(QuestionIds)


def CanManage(survey):
    """判断当前登录用户是否是问卷创建者。"""
    return g.user is not None and survey["owner_id"] == g.user["id"]


def AvailabilityMessage(survey):
    """判断问卷当前是否允许填写。

    返回 None 表示可以填写；返回字符串表示不可填写，并把原因交给页面展示。
    """
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


def CreateSurveyFromConfig(OwnerId, form, QuestionsData, published=False):
    """根据表单和题目配置创建问卷。

    published=True 时会立即生成 slug，slug 是公开填写链接的一部分。
    普通创建先不发布，用户可以继续编辑、预览和保存模板。
    """
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
            # HTML checkbox 只有勾选时才会提交字段，所以这里用是否存在判断。
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

    # 问卷主表创建成功后，再保存子表 questions/options。
    SaveQuestionsFromConfig(db, SurveyId, QuestionsData)
    db.commit()
    return SurveyId


def SurveyToTemplateConfig(SurveyId):
    """把已有问卷打包成模板 JSON。

    模板不直接复制数据库 id，而是保存标题、说明、配置项和题目结构。
    使用模板时会重新创建新的问卷和新的题目 id。
    """
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
