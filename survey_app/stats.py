"""问卷结果统计。
统计模块只负责把数据库中的答案整理成页面需要的数据结构：
- 选择题：统计每个选项的票数；
- 填空题：收集文本答案；
- chart_data：提供给前端图表使用。
"""

import json
import os
from flask import current_app
from .database import GetDb
from .dependencies import plt
from .models import LoadQuestions


def CollectStats(SurveyId):
    """统计指定问卷的所有题目结果。
    """

    

    db = GetDb()
    questions=LoadQuestions(SurveyId)
    stats = []
    for bundle in questions:
        question = bundle["question"]
        options = bundle["options"]

        # 每道题最终都整理成相同结构，模板渲染时可以统一处理。
        item = {
            "question": question,
            "options": [],
            "texts": [],
            "chart_data": [],
            "chart_type": None,
            "total": 0,
        }
        if question["qtype"] == "text":
            # 填空题没有选项票数，只展示非空文本答案。
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
            # 选择题先给每个选项初始化 0 票，再遍历所有答案累加。
            counts = {option["id"]: 0 for option in options}
            answers = db.execute(
                "SELECT option_ids FROM answers WHERE question_id = ?", (question["id"],)
            ).fetchall()
            for answer in answers:
                try:
                    # option_ids 是 JSON 数组，单选题也保存成 [id]，便于共用这段逻辑。
                    selected = json.loads(answer["option_ids"] or "[]")
                except json.JSONDecodeError:
                    selected = []
                for OptionId in selected:
                    if OptionId in counts:
                        counts[OptionId] += 1

            # options 用于列表展示，chart_data 用于前端画图。
            item["options"] = [{"option": option, "count": counts[option["id"]]} for option in options]
            item["chart_data"] = [
                {"label": option["content"], "count": counts[option["id"]]} for option in options
            ]

            # 单选题适合饼图/环形图，多选题适合柱状图。
            item["chart_type"] = "donut" if question["qtype"] == "single" else "bar"
            item["total"] = sum(counts.values())
        stats.append(item)
    return stats


def BuildChart(SurveyId, QuestionId, OptionCounts, qtype):
    """生成 matplotlib 静态图表。
    当前页面主要使用前端图表，这个函数保留给扩展使用。
    如果 matplotlib 没安装、没有数据或票数全为 0，就返回 None。
    """
    
    
    if plt is None or not OptionCounts:
        return None
    labels = [row["option"]["content"] for row in OptionCounts]
    values = [row["count"] for row in OptionCounts]
    if sum(values) == 0:
        return None
    filename = f"survey_{SurveyId}_q_{QuestionId}.png"
    path = os.path.join(current_app.config["CHART_DIR"], filename)


    # 选择题类型不同，使用不同图形表达。
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



