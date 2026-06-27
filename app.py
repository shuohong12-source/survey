"""项目启动入口。

这里故意只保留很少的代码：
1. 通过 CreateApp() 创建 Flask 应用对象；
2. 程序直接运行时初始化 SQLite 表结构；
3. 启动本地开发服务器。

把业务代码拆到 survey_app 包里后，app.py 更像一个“开关”，方便老师或同学
快速找到项目从哪里启动。
"""

from survey_app import CreateApp
from survey_app.database import InitDb


# Flask 约定很多工具都会寻找名为 app 的变量，例如 flask run 或测试代码。
app = CreateApp()


if __name__ == "__main__":
    # InitDb 需要读取 current_app.config，所以要放在 app_context 中执行。
    with app.app_context():
        InitDb()

    # use_reloader=False 可以避免调试环境下重复初始化数据库或重复启动一次服务。
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
