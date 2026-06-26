import json
import sqlite3
import time
import tkinter as tk
from tkinter import messagebox, ttk

from app import DATABASE, init_db, normalize_questions_config, now_text, save_questions_from_config


APP_USER = "__gui_user__"


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.body.bind("<Configure>", self._sync_scroll_region)
        self.canvas.bind("<Configure>", self._sync_width)

    def _sync_scroll_region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_width(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)


class OptionEditor(ttk.Frame):
    def __init__(self, parent, on_remove, text="", jump_to=""):
        super().__init__(parent)
        self.text_var = tk.StringVar(value=text)
        self.jump_var = tk.StringVar(value=str(jump_to or ""))
        ttk.Entry(self, textvariable=self.text_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Entry(self, textvariable=self.jump_var, width=12).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(self, text="删除选项", command=lambda: on_remove(self)).grid(row=0, column=2)
        self.columnconfigure(0, weight=1)

    def data(self):
        return {"text": self.text_var.get().strip(), "jump_to": self.jump_var.get().strip()}


class QuestionEditor(ttk.LabelFrame):
    def __init__(self, parent, index, on_remove, data=None):
        super().__init__(parent, text=f"问题 {index}")
        data = data or {}
        self.on_remove = on_remove
        self.content_var = tk.StringVar(value=data.get("content", ""))
        self.qtype_var = tk.StringVar(value=data.get("qtype", "single"))
        self.option_rows = []

        head = ttk.Frame(self)
        head.pack(fill="x", padx=12, pady=(12, 8))
        ttk.Button(head, text="移除", command=lambda: self.on_remove(self)).pack(side="right")

        ttk.Label(self, text="题目内容").pack(anchor="w", padx=12)
        ttk.Entry(self, textvariable=self.content_var).pack(fill="x", padx=12, pady=(4, 10))

        ttk.Label(self, text="题型").pack(anchor="w", padx=12)
        type_box = ttk.Combobox(
            self,
            textvariable=self.qtype_var,
            values=("single", "multiple", "text"),
            state="readonly",
        )
        type_box.pack(fill="x", padx=12, pady=(4, 10))
        type_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_options_state())

        self.option_panel = ttk.Frame(self)
        self.option_panel.pack(fill="x", padx=12, pady=(8, 12))
        option_head = ttk.Frame(self.option_panel)
        option_head.pack(fill="x", pady=(0, 8))
        ttk.Label(option_head, text="选项").pack(side="left")
        ttk.Button(option_head, text="添加选项", command=self.add_option).pack(side="right")
        hint = ttk.Label(option_head, text="跳转题号可不填", foreground="#667085")
        hint.pack(side="right", padx=(0, 12))

        self.option_list = ttk.Frame(self.option_panel)
        self.option_list.pack(fill="x")
        options = data.get("options") or [{"text": "选项 1"}, {"text": "选项 2"}]
        for option in options:
            self.add_option(option)
        self.refresh_options_state()

    def set_index(self, index):
        self.configure(text=f"问题 {index}")

    def add_option(self, option=None):
        option = option or {"text": f"选项 {len(self.option_rows) + 1}"}
        row = OptionEditor(
            self.option_list,
            self.remove_option,
            text=option.get("text", ""),
            jump_to=option.get("jump_to", ""),
        )
        row.pack(fill="x", pady=6)
        self.option_rows.append(row)

    def remove_option(self, row):
        if len(self.option_rows) <= 1:
            row.text_var.set("")
            row.jump_var.set("")
            return
        self.option_rows.remove(row)
        row.destroy()

    def refresh_options_state(self):
        if self.qtype_var.get() == "text":
            self.option_panel.pack_forget()
        else:
            self.option_panel.pack(fill="x", padx=12, pady=(8, 12))

    def data(self):
        qtype = self.qtype_var.get()
        return {
            "content": self.content_var.get().strip(),
            "qtype": qtype,
            "options": [] if qtype == "text" else [row.data() for row in self.option_rows],
        }


class SurveyEditor(tk.Toplevel):
    def __init__(self, app, survey_id=None):
        super().__init__(app)
        self.app = app
        self.survey_id = survey_id
        self.question_cards = []
        self.title("编辑问卷" if survey_id else "创建问卷")
        self.geometry("920x720")
        self.minsize(760, 560)

        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        form = ttk.Frame(root)
        form.pack(fill="x")
        self.title_var = tk.StringVar()
        self.target_var = tk.StringVar(value="0")
        self.public_var = tk.BooleanVar(value=True)
        self.anonymous_var = tk.BooleanVar(value=False)
        self.published_var = tk.BooleanVar(value=True)

        ttk.Label(form, text="标题").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.title_var).grid(row=1, column=0, sticky="ew", pady=(4, 10))
        ttk.Label(form, text="预计回收数量").grid(row=0, column=1, sticky="w", padx=(14, 0))
        ttk.Entry(form, textvariable=self.target_var, width=18).grid(row=1, column=1, sticky="ew", padx=(14, 0), pady=(4, 10))
        form.columnconfigure(0, weight=1)

        ttk.Label(root, text="说明").pack(anchor="w")
        self.description = tk.Text(root, height=3, wrap="word")
        self.description.pack(fill="x", pady=(4, 10))

        checks = ttk.Frame(root)
        checks.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(checks, text="公开问卷", variable=self.public_var).pack(side="left")
        ttk.Checkbutton(checks, text="匿名投票", variable=self.anonymous_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(checks, text="创建后可填写", variable=self.published_var).pack(side="left", padx=(16, 0))

        toolbar = ttk.Frame(root)
        toolbar.pack(fill="x", pady=(4, 8))
        ttk.Label(toolbar, text="问题列表", font=("Microsoft YaHei", 12, "bold")).pack(side="left")
        ttk.Button(toolbar, text="添加问题", command=self.add_question).pack(side="right")

        self.scroll = ScrollFrame(root)
        self.scroll.pack(fill="both", expand=True)

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="保存", command=self.save).pack(side="right")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))

        if survey_id:
            self.load_survey()
        else:
            self.add_question({"content": "你对本课程实践的满意度如何？", "qtype": "single"})

    def load_survey(self):
        with connect_db() as db:
            survey = db.execute("SELECT * FROM surveys WHERE id = ?", (self.survey_id,)).fetchone()
            bundles = load_questions_db(db, self.survey_id)
        if survey is None:
            messagebox.showerror("错误", "问卷不存在。", parent=self)
            self.destroy()
            return
        self.title_var.set(survey["title"])
        self.target_var.set(str(survey["target_count"] or 0))
        self.public_var.set(bool(survey["is_public"]))
        self.anonymous_var.set(bool(survey["is_anonymous"]))
        self.published_var.set(bool(survey["is_published"]))
        self.description.delete("1.0", "end")
        self.description.insert("1.0", survey["description"] or "")
        for item in questions_to_config_db(bundles):
            self.add_question(item)

    def add_question(self, data=None):
        card = QuestionEditor(self.scroll.body, len(self.question_cards) + 1, self.remove_question, data)
        card.pack(fill="x", pady=8, padx=4)
        self.question_cards.append(card)

    def remove_question(self, card):
        if len(self.question_cards) <= 1:
            messagebox.showinfo("提示", "至少保留一个问题。", parent=self)
            return
        self.question_cards.remove(card)
        card.destroy()
        for index, item in enumerate(self.question_cards, start=1):
            item.set_index(index)

    def save(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("提示", "问卷标题不能为空。", parent=self)
            return
        try:
            target_count = int(self.target_var.get() or 0)
        except ValueError:
            messagebox.showwarning("提示", "预计回收数量必须是数字。", parent=self)
            return
        questions = normalize_questions_config([card.data() for card in self.question_cards])
        if not questions:
            messagebox.showwarning("提示", "至少需要一个有效问题。", parent=self)
            return

        with connect_db() as db:
            if self.survey_id:
                response_count = db.execute(
                    "SELECT COUNT(*) AS total FROM responses WHERE survey_id = ?", (self.survey_id,)
                ).fetchone()["total"]
                old_questions = normalize_questions_config(
                    questions_to_config_db(load_questions_db(db, self.survey_id))
                )
                changed = old_questions != questions
                if response_count and changed:
                    messagebox.showwarning("提示", "该问卷已有答卷，不能修改问题结构。", parent=self)
                    return
                db.execute(
                    """
                    UPDATE surveys
                    SET title = ?, description = ?, is_public = ?, is_anonymous = ?,
                        is_published = ?, target_count = ?
                    WHERE id = ?
                    """,
                    (
                        title,
                        self.description.get("1.0", "end").strip(),
                        int(self.public_var.get()),
                        int(self.anonymous_var.get()),
                        int(self.published_var.get()),
                        target_count,
                        self.survey_id,
                    ),
                )
                if changed:
                    db.execute(
                        "DELETE FROM options WHERE question_id IN (SELECT id FROM questions WHERE survey_id = ?)",
                        (self.survey_id,),
                    )
                    db.execute("DELETE FROM questions WHERE survey_id = ?", (self.survey_id,))
                    save_questions_from_config(db, self.survey_id, questions)
            else:
                cursor = db.execute(
                    """
                    INSERT INTO surveys (
                        owner_id, title, description, is_public, is_anonymous, is_published,
                        slug, open_at, close_at, target_count, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        self.app.user_id,
                        title,
                        self.description.get("1.0", "end").strip(),
                        int(self.public_var.get()),
                        int(self.anonymous_var.get()),
                        int(self.published_var.get()),
                        None,
                        target_count,
                        now_text(),
                    ),
                )
                self.survey_id = cursor.lastrowid
                save_questions_from_config(db, self.survey_id, questions)
            db.commit()
        self.app.refresh_surveys()
        messagebox.showinfo("成功", "问卷已保存。", parent=self)
        self.destroy()


class FillWindow(tk.Toplevel):
    def __init__(self, app, survey_id):
        super().__init__(app)
        self.app = app
        self.survey_id = survey_id
        self.started_at = time.time()
        self.answer_vars = {}
        self.question_views = []
        self.title("填写问卷")
        self.geometry("860x680")
        self.minsize(720, 520)

        with connect_db() as db:
            self.survey = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
            self.questions = load_questions_db(db, survey_id)
        if self.survey is None:
            messagebox.showerror("错误", "问卷不存在。", parent=self)
            self.destroy()
            return

        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text=self.survey["title"], font=("Microsoft YaHei", 18, "bold")).pack(anchor="w")
        if self.survey["description"]:
            ttk.Label(root, text=self.survey["description"], foreground="#667085", wraplength=760).pack(anchor="w", pady=(4, 12))

        self.scroll = ScrollFrame(root)
        self.scroll.pack(fill="both", expand=True)
        self.build_questions()

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="提交答案", command=self.submit).pack(side="right")

    def build_questions(self):
        for bundle in self.questions:
            question = bundle["question"]
            frame = ttk.LabelFrame(
                self.scroll.body,
                text=f"Q{question['sort_order']}：{question['content']}",
                padding=12,
            )
            frame.pack(fill="x", padx=4, pady=10)
            view = {"frame": frame, "question": question, "options": bundle["options"]}
            self.question_views.append(view)
            field = f"q_{question['id']}"
            if question["qtype"] == "text":
                text = tk.Text(frame, height=4, wrap="word")
                text.pack(fill="x", pady=(8, 0))
                self.answer_vars[field] = text
            elif question["qtype"] == "single":
                var = tk.StringVar()
                self.answer_vars[field] = var
                for option in bundle["options"]:
                    ttk.Radiobutton(
                        frame,
                        text=option["content"],
                        value=str(option["id"]),
                        variable=var,
                        command=self.apply_jump_logic,
                    ).pack(anchor="w", pady=6)
            else:
                vars_for_question = {}
                self.answer_vars[field] = vars_for_question
                for option in bundle["options"]:
                    var = tk.BooleanVar()
                    vars_for_question[str(option["id"])] = var
                    ttk.Checkbutton(
                        frame,
                        text=option["content"],
                        variable=var,
                        command=self.apply_jump_logic,
                    ).pack(anchor="w", pady=6)

    def selected_jump_target(self, view):
        question = view["question"]
        field = f"q_{question['id']}"
        if question["qtype"] == "text":
            return None
        selected = []
        if question["qtype"] == "single":
            value = self.answer_vars[field].get()
            selected = [value] if value else []
        else:
            selected = [option_id for option_id, var in self.answer_vars[field].items() if var.get()]
        for option in view["options"]:
            if str(option["id"]) in selected and option["jump_to_question_id"]:
                return option["jump_to_question_id"]
        return None

    def apply_jump_logic(self):
        hidden = set()
        id_to_index = {view["question"]["id"]: index for index, view in enumerate(self.question_views)}
        for index, view in enumerate(self.question_views):
            if index in hidden:
                continue
            target_id = self.selected_jump_target(view)
            target_index = id_to_index.get(target_id)
            if target_index is not None and target_index > index + 1:
                hidden.update(range(index + 1, target_index))
        for index, view in enumerate(self.question_views):
            if index in hidden:
                view["frame"].pack_forget()
            else:
                view["frame"].pack(fill="x", padx=4, pady=10)

    def submit(self):
        with connect_db() as db:
            duration = max(0, int(time.time() - self.started_at))
            response_id = db.execute(
                """
                INSERT INTO responses (survey_id, user_id, started_at, submitted_at, duration_seconds)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.survey_id, None if self.survey["is_anonymous"] else self.app.user_id, now_text(), now_text(), duration),
            ).lastrowid
            visible_frames = {view["frame"] for view in self.question_views if view["frame"].winfo_ismapped()}
            for view in self.question_views:
                if view["frame"] not in visible_frames:
                    continue
                question = view["question"]
                field = f"q_{question['id']}"
                if question["qtype"] == "text":
                    text_answer = self.answer_vars[field].get("1.0", "end").strip()
                    option_ids = None
                elif question["qtype"] == "single":
                    value = self.answer_vars[field].get()
                    text_answer = None
                    option_ids = json.dumps([int(value)] if value else [])
                else:
                    selected = [
                        int(option_id)
                        for option_id, var in self.answer_vars[field].items()
                        if var.get()
                    ]
                    text_answer = None
                    option_ids = json.dumps(selected)
                db.execute(
                    "INSERT INTO answers (response_id, question_id, text_answer, option_ids) VALUES (?, ?, ?, ?)",
                    (response_id, question["id"], text_answer, option_ids),
                )
            db.commit()
        messagebox.showinfo("提交成功", f"感谢你填写 {self.survey['title']}。", parent=self)
        self.app.refresh_surveys()
        self.destroy()


class ResultsWindow(tk.Toplevel):
    def __init__(self, app, survey_id):
        super().__init__(app)
        self.title("问卷结果")
        self.geometry("900x680")
        self.minsize(760, 520)

        with connect_db() as db:
            self.survey = db.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,)).fetchone()
            self.stats = collect_stats_db(db, survey_id)
            response_count = db.execute(
                "SELECT COUNT(*) AS total FROM responses WHERE survey_id = ?", (survey_id,)
            ).fetchone()["total"]
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text=f"{self.survey['title']} 的结果", font=("Microsoft YaHei", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text=f"已回收 {response_count} 份", foreground="#667085").pack(anchor="w", pady=(4, 12))

        panes = ttk.PanedWindow(root, orient="horizontal")
        panes.pack(fill="both", expand=True)
        self.list_frame = ttk.Frame(panes, padding=(0, 0, 12, 0))
        self.chart_frame = ttk.Frame(panes)
        panes.add(self.list_frame, weight=1)
        panes.add(self.chart_frame, weight=2)

        self.question_list = tk.Listbox(self.list_frame, activestyle="dotbox")
        self.question_list.pack(fill="both", expand=True)
        for item in self.stats:
            self.question_list.insert("end", f"Q{item['order']}：{item['content']}")
        self.question_list.bind("<<ListboxSelect>>", lambda _event: self.show_selected())

        self.detail = ttk.Label(self.chart_frame, text="", font=("Microsoft YaHei", 12, "bold"), wraplength=520)
        self.detail.pack(anchor="w", pady=(0, 10))
        self.canvas = tk.Canvas(self.chart_frame, height=360, bg="white", highlightthickness=1, highlightbackground="#d9dee8")
        self.canvas.pack(fill="both", expand=True)
        self.table = ttk.Treeview(self.chart_frame, columns=("option", "count"), show="headings", height=7)
        self.table.heading("option", text="选项/答案")
        self.table.heading("count", text="票数")
        self.table.pack(fill="x", pady=(12, 0))
        self.tooltip = None
        if self.stats:
            self.question_list.selection_set(0)
            self.show_selected()

    def show_selected(self):
        selection = self.question_list.curselection()
        if not selection:
            return
        item = self.stats[selection[0]]
        self.detail.configure(text=f"Q{item['order']}：{item['content']}")
        self.canvas.delete("all")
        self.table.delete(*self.table.get_children())
        if item["qtype"] == "text":
            self.canvas.create_text(24, 24, anchor="nw", text="填空题答案见下方列表", fill="#667085", font=("Microsoft YaHei", 12, "bold"))
            for text in item["texts"]:
                self.table.insert("", "end", values=(text, ""))
            if not item["texts"]:
                self.table.insert("", "end", values=("暂无填空答案", ""))
            return
        for row in item["options"]:
            self.table.insert("", "end", values=(row["label"], row["count"]))
        self.draw_bar_chart(item["options"])

    def draw_bar_chart(self, rows):
        colors = ["#2563eb", "#06b6d4", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"]
        total = sum(row["count"] for row in rows)
        max_count = max([row["count"] for row in rows] + [1])
        width = max(self.canvas.winfo_width(), 700)
        left = 150
        right = width - 70
        top = 36
        gap = 18
        bar_h = 30
        for index, row in enumerate(rows):
            y = top + index * (bar_h + gap)
            color = colors[index % len(colors)]
            bar_w = int((right - left) * row["count"] / max_count) if max_count else 0
            self.canvas.create_text(18, y + bar_h / 2, anchor="w", text=row["label"], font=("Microsoft YaHei", 10, "bold"), fill="#172033")
            self.canvas.create_rectangle(left, y, right, y + bar_h, fill="#e8edf5", outline="")
            rect = self.canvas.create_rectangle(left, y, left + max(bar_w, 4), y + bar_h, fill=color, outline="")
            percent = round(row["count"] * 100 / total, 1) if total else 0
            self.canvas.create_text(right + 12, y + bar_h / 2, anchor="w", text=f"{row['count']}票", fill="#667085", font=("Microsoft YaHei", 10, "bold"))
            self.canvas.tag_bind(rect, "<Enter>", lambda event, r=row, p=percent: self.show_tip(event, f"{r['label']}：{r['count']} 票，{p}%"))
            self.canvas.tag_bind(rect, "<Leave>", lambda _event: self.hide_tip())

    def show_tip(self, event, text):
        self.hide_tip()
        self.tooltip = tk.Toplevel(self)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.geometry(f"+{event.x_root + 12}+{event.y_root + 10}")
        ttk.Label(self.tooltip, text=text, padding=8, background="#172033", foreground="white").pack()

    def hide_tip(self):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None


class SurveyGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        init_db()
        self.user_id = ensure_gui_user()
        self.title("问卷调查系统 GUI")
        self.geometry("1040x680")
        self.minsize(860, 560)
        self.configure(bg="#f6f7fb")
        self.setup_style()
        self.build()
        self.refresh_surveys()

    def setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei", 10))
        style.configure("TFrame", background="#f6f7fb")
        style.configure("TLabel", background="#f6f7fb", foreground="#172033")
        style.configure("TButton", padding=(12, 6), font=("Microsoft YaHei", 10, "bold"))
        style.configure("Treeview", rowheight=30, background="white", fieldbackground="white")
        style.configure("TLabelframe", background="#ffffff", padding=10)
        style.configure("TLabelframe.Label", font=("Microsoft YaHei", 11, "bold"))

    def build(self):
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="问卷调查系统 GUI", font=("Microsoft YaHei", 22, "bold")).pack(side="left")
        ttk.Button(header, text="刷新", command=self.refresh_surveys).pack(side="right")

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="创建问卷", command=lambda: SurveyEditor(self)).pack(side="left")
        ttk.Button(actions, text="编辑选中", command=self.edit_selected).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="填写选中", command=self.fill_selected).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="查看结果", command=self.results_selected).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="删除选中", command=self.delete_selected).pack(side="right")

        self.tree = ttk.Treeview(
            root,
            columns=("title", "published", "responses", "created"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("title", text="标题")
        self.tree.heading("published", text="状态")
        self.tree.heading("responses", text="答卷数")
        self.tree.heading("created", text="创建时间")
        self.tree.column("title", width=420)
        self.tree.column("published", width=100, anchor="center")
        self.tree.column("responses", width=100, anchor="center")
        self.tree.column("created", width=180)
        self.tree.pack(fill="both", expand=True)

    def selected_survey_id(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择一个问卷。", parent=self)
            return None
        return int(selected[0])

    def refresh_surveys(self):
        self.tree.delete(*self.tree.get_children())
        with connect_db() as db:
            rows = db.execute(
                """
                SELECT s.*, COUNT(r.id) AS response_count
                FROM surveys s
                LEFT JOIN responses r ON r.survey_id = s.id
                GROUP BY s.id
                ORDER BY s.created_at DESC
                """
            ).fetchall()
        for row in rows:
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["title"],
                    "可填写" if row["is_published"] else "草稿",
                    row["response_count"],
                    row["created_at"],
                ),
            )

    def edit_selected(self):
        survey_id = self.selected_survey_id()
        if survey_id:
            SurveyEditor(self, survey_id)

    def fill_selected(self):
        survey_id = self.selected_survey_id()
        if survey_id:
            FillWindow(self, survey_id)

    def results_selected(self):
        survey_id = self.selected_survey_id()
        if survey_id:
            ResultsWindow(self, survey_id)

    def delete_selected(self):
        survey_id = self.selected_survey_id()
        if not survey_id:
            return
        if not messagebox.askyesno("确认删除", "确定删除这个问卷及相关答案吗？", parent=self):
            return
        with connect_db() as db:
            db.execute(
                """
                DELETE FROM answers
                WHERE response_id IN (SELECT id FROM responses WHERE survey_id = ?)
                   OR question_id IN (SELECT id FROM questions WHERE survey_id = ?)
                """,
                (survey_id, survey_id),
            )
            db.execute("DELETE FROM responses WHERE survey_id = ?", (survey_id,))
            db.execute(
                "DELETE FROM options WHERE question_id IN (SELECT id FROM questions WHERE survey_id = ?)",
                (survey_id,),
            )
            db.execute("DELETE FROM questions WHERE survey_id = ?", (survey_id,))
            db.execute("DELETE FROM surveys WHERE id = ?", (survey_id,))
            db.commit()
        self.refresh_surveys()


def connect_db():
    db = sqlite3.connect(DATABASE)
    db.execute("PRAGMA foreign_keys = ON")
    db.row_factory = sqlite3.Row
    return db


def ensure_gui_user():
    with connect_db() as db:
        user = db.execute("SELECT * FROM users WHERE username = ?", (APP_USER,)).fetchone()
        if user is None:
            cursor = db.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (APP_USER, "gui-local-user", now_text()),
            )
            db.commit()
            return cursor.lastrowid
        return user["id"]


def load_questions_db(db, survey_id):
    questions = db.execute(
        "SELECT * FROM questions WHERE survey_id = ? ORDER BY sort_order", (survey_id,)
    ).fetchall()
    result = []
    for question in questions:
        options = db.execute(
            "SELECT * FROM options WHERE question_id = ? ORDER BY sort_order", (question["id"],)
        ).fetchall()
        result.append({"question": question, "options": options})
    return result


def questions_to_config_db(bundles):
    question_order = {bundle["question"]["id"]: bundle["question"]["sort_order"] for bundle in bundles}
    return [
        {
            "content": bundle["question"]["content"],
            "qtype": bundle["question"]["qtype"],
            "options": [
                {
                    "text": option["content"],
                    "jump_to": question_order.get(option["jump_to_question_id"], ""),
                }
                for option in bundle["options"]
            ],
        }
        for bundle in bundles
    ]


def collect_stats_db(db, survey_id):
    stats = []
    for bundle in load_questions_db(db, survey_id):
        question = bundle["question"]
        item = {
            "order": question["sort_order"],
            "content": question["content"],
            "qtype": question["qtype"],
            "options": [],
            "texts": [],
        }
        if question["qtype"] == "text":
            rows = db.execute(
                """
                SELECT text_answer FROM answers
                WHERE question_id = ? AND text_answer IS NOT NULL AND text_answer != ''
                ORDER BY id DESC
                """,
                (question["id"],),
            ).fetchall()
            item["texts"] = [row["text_answer"] for row in rows]
        else:
            counts = {option["id"]: 0 for option in bundle["options"]}
            rows = db.execute("SELECT option_ids FROM answers WHERE question_id = ?", (question["id"],)).fetchall()
            for row in rows:
                try:
                    selected = json.loads(row["option_ids"] or "[]")
                except json.JSONDecodeError:
                    selected = []
                for option_id in selected:
                    if option_id in counts:
                        counts[option_id] += 1
            item["options"] = [
                {"label": option["content"], "count": counts[option["id"]]}
                for option in bundle["options"]
            ]
        stats.append(item)
    return stats


if __name__ == "__main__":
    SurveyGUI().mainloop()
