"""Flask 应用工厂。
这个文件负责把项目各部分组装起来：
- 指定模板和静态文件目录；
- 写入数据库路径、图表目录等配置；
- 注册数据库关闭钩子、登录用户加载钩子；
- 注册所有页面路由。
使用应用工厂的好处是：以后做测试、换配置、扩展蓝图时，不需要改启动入口。
"""

from flask import Flask
from . import auth, config, database
from .routes import RegisterRoutes


def CreateApp():
    """
    创建并配置 Flask 应用实例。
    """
    
    app=Flask(
        __name__,
        # 模板和静态目录放在项目根目录，而不是 survey_app 包内部。
        template_folder=str(config.BASE_DIR / "templates"),
        static_folder=str(config.BASE_DIR / "static"),
    )

    app.config.from_mapping(
        SECRET_KEY=config.SECRET_KEY,
        DATABASE=config.DATABASE,
        CHART_DIR=config.CHART_DIR,
    )

    # 注册请求结束时关闭数据库连接的钩子。
    database.InitApp(app)
    # 注册 before_request：每次请求前把当前登录用户放到 g.user。
    auth.InitApp(app)
    # 注册所有 URL 规则。
    RegisterRoutes(app)
    return app
