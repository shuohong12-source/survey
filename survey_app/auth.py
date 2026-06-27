"""登录和当前用户相关代码。"""

from functools import wraps

from flask import flash, g, redirect, session, url_for

from .database import GetDb


def LoginRequired(view):
    
    """登录保护装饰器。
    被这个装饰器包住的视图函数必须先登录才能访问。
    如果 session 中没有 user_id，就跳转到登录页。
    """
    
    @wraps(view)
    def WrappedView(**kwargs):
        if "user_id" not in session:
            flash("请先登录。")
            return redirect(url_for("login"))
        return view(**kwargs)

    return WrappedView


def LoadLoggedInUser():
    
    """每次请求前从 session 读取当前用户。
    Flask 的 g 对象只在本次请求内有效，模板和路由都可以通过 g.user 判断
    当前是否登录，以及当前登录用户是谁。
    """

    UserId = session.get("user_id")
    g.user = None
    if UserId is not None:
        g.user = GetDb().execute("SELECT * FROM users WHERE id = ?", (UserId,)).fetchone()


def InitApp(app):

    """注册 before_request 钩子。"""
    
    app.before_request(LoadLoggedInUser)
