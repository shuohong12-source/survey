# 问卷调查系统

这是一个基于 Flask + SQLite 的在线问卷调查系统，适合作为 Python Web 课程设计、数据库课程实践或小型问卷平台原型。系统支持用户注册登录、问卷创建与发布、在线填写、结果统计、模板复用、CSV/Excel 导出、二维码分享、匿名投票和开放时间控制。

## 技术栈

- 后端框架：Flask
- 数据库：SQLite
- 模板引擎：Jinja2
- 前端：HTML、CSS、原生 JavaScript
- 图表能力：页面内前端图表，后端保留 matplotlib 静态图生成函数
- 文件导出：CSV、Excel
- 二维码：qrcode

## 项目结构

```text
survey/
├─ app.py                     # 应用启动入口
├─ requirements.txt           # Python 依赖
├─ survey.db                  # SQLite 数据库，首次运行自动创建
├─ survey_app/                # 后端应用包
│  ├─ __init__.py             # Flask app 工厂，注册数据库、登录钩子和路由
│  ├─ auth.py                 # 登录保护、当前用户加载
│  ├─ config.py               # 项目路径、数据库路径、密钥等配置
│  ├─ database.py             # 数据库连接、关闭、建表初始化
│  ├─ dependencies.py         # matplotlib、openpyxl、qrcode 等可选依赖加载
│  ├─ exports.py              # CSV / Excel 导出逻辑
│  ├─ models.py               # 问卷、题目、选项、模板等数据处理逻辑
│  ├─ routes.py               # 页面路由和请求处理
│  ├─ stats.py                # 答卷统计和图表数据生成
│  └─ utils.py                # 时间格式化、时间解析等工具函数
├─ templates/                 # 页面模板
│  ├─ auth.html
│  ├─ base.html
│  ├─ edit_survey.html
│  ├─ fill.html
│  ├─ index.html
│  ├─ results.html
│  ├─ surveys.html
│  └─ thanks.html
└─ static/
   └─ style.css               # 页面样式
```

## 功能说明

### 用户模块

- 用户注册
- 用户登录
- 用户退出登录
- 登录后才能创建、编辑、发布、删除问卷，以及查看统计和导出数据

### 问卷管理

- 创建问卷：填写标题、说明、题目列表
- 编辑问卷：修改基础信息、开放时间、截止时间、目标回收数量
- 发布问卷：生成可分享填写链接
- 删除问卷：同步清理题目、选项、答卷和答案数据
- 问卷列表：区分“我的问卷”“公开问卷”“我的模板”

### 题型支持

- 单选题：只能选择一个选项
- 多选题：可以选择多个选项
- 填空题：提交文本答案

### 扩展功能

- 逻辑跳转：选择题选项可设置跳转到指定题目
- 问卷模板：可将已有问卷保存为模板，下次快速创建
- 结果统计：统计每个选项的票数，展示填空题文本答案
- 回收统计：统计答卷数量、目标回收率、平均答题时间
- 数据导出：支持 CSV 和 Excel
- 时间控制：支持设置开放时间和截止日期
- 匿名投票：匿名问卷不会记录填写用户 ID
- 二维码分享：发布后可生成填写二维码

## 数据库表设计

系统使用 SQLite，主要数据表如下：

| 表名 | 作用 |
|---|---|
| users | 保存用户账号、密码哈希和注册时间 |
| surveys | 保存问卷标题、说明、发布状态、开放时间、匿名设置等 |
| questions | 保存问卷题目、题型和排序 |
| options | 保存选择题选项，以及逻辑跳转目标 |
| responses | 保存每一次答卷提交记录、提交时间和答题时长 |
| answers | 保存每道题的具体答案 |
| templates | 保存用户的问卷模板 JSON 配置 |

## 安装与运行

建议使用虚拟环境运行项目。

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

浏览器访问：

```text
http://127.0.0.1:5000
```

首次启动时会自动创建或更新 `survey.db` 数据库表结构。

## 使用流程

1. 注册账号并登录。
2. 点击“创建问卷”，填写标题、说明和问题。
3. 根据需要设置公开、匿名、开放时间、截止时间和目标回收数量。
4. 保存后点击发布，系统生成填写链接和二维码。
5. 用户通过链接填写问卷并提交。
6. 创建者进入结果页查看统计结果。
7. 如需留档，可导出 CSV 或 Excel。
8. 常用问卷可保存为模板，下次快速复用。

## 主要路由

| 路由 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 首页，展示公开已发布问卷 |
| `/register` | GET/POST | 注册 |
| `/login` | GET/POST | 登录 |
| `/logout` | GET | 退出 |
| `/surveys` | GET | 问卷列表 |
| `/survey/new` | GET/POST | 创建问卷 |
| `/survey/<id>/edit` | GET/POST | 编辑问卷 |
| `/survey/<id>/publish` | POST | 发布问卷 |
| `/survey/<id>/delete` | POST | 删除问卷 |
| `/survey/<id>/results` | GET | 查看结果 |
| `/survey/<id>/export.csv` | GET | 导出 CSV |
| `/survey/<id>/export.xlsx` | GET | 导出 Excel |
| `/s/<slug>` | GET/POST | 填写问卷 |
| `/s/<slug>/qr.png` | GET | 获取二维码图片 |

## 代码模块说明

- `app.py` 只负责启动应用，不再堆放业务代码。
- `survey_app/database.py` 负责 SQLite 连接和建表，所有请求共享 `g.db`。
- `survey_app/models.py` 负责问卷、问题、选项和模板的数据处理。
- `survey_app/routes.py` 负责页面入口、表单处理和跳转。
- `survey_app/stats.py` 负责结果统计，选择题生成票数和图表数据，填空题收集文本答案。
- `survey_app/exports.py` 负责把答卷整理为行数据，并导出 CSV / Excel。
- `survey_app/dependencies.py` 将可选依赖集中管理，避免缺少增强库时影响基础功能。

## 常见问题

### 1. Excel 导出不可用怎么办？

确认已经安装依赖：

```bash
pip install -r requirements.txt
```

Excel 导出依赖 `openpyxl`。如果缺少该库，系统会回到结果页，基础问卷功能不受影响。

### 2. 二维码不显示怎么办？

二维码依赖 `qrcode[pil]`。重新安装依赖即可：

```bash
pip install -r requirements.txt
```

### 3. 中文图表乱码怎么办？

项目会优先尝试加载 Windows 系统字体：

- `C:/Windows/Fonts/msyh.ttc`
- `C:/Windows/Fonts/simhei.ttf`

如果运行环境没有这些字体，matplotlib 静态图可能出现中文乱码，但页面统计和基础功能不受影响。

### 4. 如何重置数据？

停止程序后，手动删除 `survey.db`，再次运行 `python app.py` 会重新创建数据库。注意删除数据库会清空所有用户、问卷和答卷数据。

## 依赖列表

见 `requirements.txt`：

```text
Flask
matplotlib
openpyxl
qrcode[pil]
```

