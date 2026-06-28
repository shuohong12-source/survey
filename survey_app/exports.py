"""答卷导出。
导出时采用“一份答卷一行”的结构：
- 前几列是答卷基本信息；
- 后面每道题展开成一列；
- 选择题把选项 id 转换成选项文字，方便 Excel 中阅读。
"""

import csv
import io
import json
from flask import Response, send_file
from .database import GetDb
from .dependencies import Font, openpyxl
from .models import GetSurveyOr404, LoadQuestions
DEFAULT_EXPORT_HEADERS = ["response_id", "user", "submitted_at", "duration_seconds"]


def ExportRows(SurveyId):
    """把数据库中的答卷整理成可导出的行数据。
    """
    
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
        # 每份答卷的基础信息。匿名问卷不显示真实用户。
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
                    # 数据库保存的是选项 id，导出时转换成人能看懂的选项文字。
                    SelectedIds = json.loads(answer["option_ids"] or "[]")
                    OptionMap = {option["id"]: option["content"] for option in bundle["options"]}
                    value = "；".join(OptionMap.get(OptionId, "") for OptionId in SelectedIds)

            # 列名中带题号和题目内容，导出后不需要再对照数据库。
            base[f"Q{question['sort_order']} {question['content']}"] = value
        rows.append(base)
    return rows


def BuildCsvResponse(SurveyId):
    """构造 CSV 下载响应。
    """
    
    rows = ExportRows(SurveyId)
    output = io.StringIO()
    headers = list(rows[0].keys()) if rows else DEFAULT_EXPORT_HEADERS
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)

    # 添加 UTF-8 BOM，Windows 版 Excel 直接打开中文不容易乱码。
    data = "\ufeff" + output.getvalue()
    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=survey_{SurveyId}.csv"},
    )


def BuildXlsxResponse(SurveyId):
    """构造 Excel 下载响应。
    """
    
    rows = ExportRows(SurveyId)
    headers = list(rows[0].keys()) if rows else DEFAULT_EXPORT_HEADERS
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "问卷结果"
    sheet.append(headers)

    # 表头加粗，便于区分字段名和数据。
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])

    # 根据内容长度自动估算列宽，并限制最大宽度，避免特别长的问题撑爆表格。
    for column in sheet.columns:
        width = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 12), 40)

    # openpyxl 写入内存字节流，Flask 再把它作为附件返回给浏览器。
    FileObj = io.BytesIO()
    workbook.save(FileObj)
    FileObj.seek(0)
    return send_file(
        FileObj,
        as_attachment=True,
        download_name=f"survey_{SurveyId}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
