"""项目配置。
这里集中保存路径和密钥，其他模块通过 current_app.config 读取。
小项目可以先这样写；如果以后部署到服务器，可以再改成读取环境变量。
"""

from pathlib import Path


# BASE_DIR 指向项目根目录，也就是 app.py 所在目录。
BASE_DIR = Path(__file__).resolve().parent.parent

# SQLite 数据库文件放在项目根目录，便于课程项目演示和备份。
DATABASE = str(BASE_DIR / "survey.db")

# matplotlib 生成的静态图表放到 static/charts，浏览器可以直接访问。
CHART_DIR = str(BASE_DIR / "static" / "charts")

# Flask session 加密使用的密钥。正式部署时建议换成环境变量。
SECRET_KEY = "survey-course-project-secret"
