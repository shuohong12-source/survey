"""数据库相关代码。

本项目使用 SQLite。Flask 每处理一次请求都会创建一个请求上下文，
因此这里把数据库连接放到 ``flask.g`` 中，同一个请求内复用连接，
请求结束后再统一关闭。
"""

import os
import sqlite3

from flask import current_app, g


def GetDb():

    """获取当前请求使用的数据库连接。
    第一次调用时创建连接，之后同一个请求再次调用会直接复用 g.db。
    row_factory 设置为 sqlite3.Row 后，可以像字典一样用 row["title"] 取值。
    """
    
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])

        # SQLite 默认不强制执行外键约束，需要手动打开。
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.row_factory = sqlite3.Row
    return g.db


def CloseDb(error=None):

    """请求结束时关闭数据库连接。
    这个函数会被 Flask 的 teardown_appcontext 调用，不需要在每个视图函数里
    手动 close，避免遗漏。
    """
    
    db = g.pop("db", None)
    if db is not None:
        db.close()


def InitDb():

    """初始化数据库表结构。
    CREATE TABLE IF NOT EXISTS 可以反复执行：表不存在就创建，已经存在就跳过。
    这样首次启动和后续启动都可以安全调用 InitDb。
    """



    os.makedirs(current_app.config["CHART_DIR"], exist_ok=True)
    conn = sqlite3.connect(current_app.config["DATABASE"])
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
            -- owner_id 对应 users.id，表示问卷创建者。
            FOREIGN KEY(owner_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            qtype TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            -- 删除问卷时，相关题目自动删除。
            FOREIGN KEY(survey_id) REFERENCES surveys(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            jump_to_question_id INTEGER,
            -- jump_to_question_id 用于实现“选择某个选项后跳到指定题目”。
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
            -- 匿名问卷的 user_id 会保存为 NULL。
            FOREIGN KEY(survey_id) REFERENCES surveys(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            response_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            text_answer TEXT,
            option_ids TEXT,
            -- 选择题答案统一用 JSON 数组保存选项 id，单选和多选可共用统计逻辑。
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


def InitApp(app):
    """把数据库清理函数注册到 Flask 应用。"""
    app.teardown_appcontext(CloseDb)
