"""通用工具函数。
当前主要处理时间字符串，因为 SQLite 没有专门的 datetime 类型，
项目中统一用字符串保存时间，读取时再转成 Python datetime。
"""

from datetime import datetime


def NowText():
    """返回当前时间字符串，格式适合直接写入 SQLite。
    """
    
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ParseDatetime(value):
    """把数据库或表单中的时间字符串解析成 datetime。
    页面中的 datetime-local 控件提交格式通常是 ``YYYY-MM-DDTHH:MM``，
    数据库中保存的是 ``YYYY-MM-DD HH:MM:SS``。这里用 replace 统一兼容。
    解析失败时返回 None，让调用方按“没有设置时间”处理。
    """
    
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("T", " "))
    except ValueError:
        return None
