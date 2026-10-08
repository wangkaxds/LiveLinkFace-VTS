from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox, ttk

from bridge import Bridge, Settings, Storage

VERSION = "0.1.5"
ORIENTATION_CHOICES = {
    "portrait": "竖屏 / 不补偿",
    "landscape_left": "横屏 · 镜头在左",
    "landscape_right": "横屏 · 镜头在右",
    "upside_down": "倒置竖屏 · 镜头在下",
}
BLINK_SYNC_CHOICES = {"never": "从不", "always": "总是", "when_turning": "当面部转动时"}
TRACKING_LOST_CHOICES = {"hold": "保持动作", "idle": "回到待机状态"}


def local_addresses() -> list[str]:
    try:
        addresses = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        addresses = {a for a in addresses if not a.startswith(("127.", "169.254."))}
        return sorted(addresses, key=lambda a: (not ipaddress.ip_address(a).is_private, a))
    except OSError:
        return []


HELP = """第一次接入

1. 打开 VTube Studio，载入 Live2D 模型。
   在设置中打开「允许插件 API 访问 / Allow Plugin API access」，默认端口 8001。

2. 手机与电脑连接同一个局域网。
   Live Link Face → 设置 → Capture Mode → Live Link（ARKit）。
   开启 Stream Head Rotation；在 Live Link → Add Target 填电脑 IP 与 UDP 端口。
   回到手机主画面，打开 LIVE 发送，保持脸在画面内。

3. 在这个工具中点击「开始连接」。
   VTS 弹出授权时，对 LiveLink Face to VTS 点击「允许」。
   手机数据和 VTS 两项均正常后，模型就能接收面捕输入。

4. 正对手机，保持普通坐姿、自然睁开双眼并目视正中，点击「校准」。
   当前头部姿势和左右眼视线会成为正中位置，同时记录自然睁眼的基准。
   校准后仍能完整闭眼或单眼眨眼；不要闭眼或刻意瞪大眼睛进行校准。
   然后根据自己的模型调头部、嘴巴和眨眼幅度。

眼睛开合调节

•「睁眼灵敏度」越高，模型越容易睁开；低于 1 倍时会缩小睁眼幅度。
•「眨眼灵敏度」越高，模型越容易闭合；单眼眨眼和闭眼使用同一项。
•「开合平滑」同时控制睁眼、闭眼和眨眼的平滑程度，默认 8 ms。
  两个灵敏度可以分别调整，不影响头部和眼球视线。
•「张嘴灵敏度」调嘴巴开合，「笑容灵敏度」调嘴角与笑眼，
  「眉毛灵敏度」调挑眉与皱眉的幅度，正常眉毛基准不变。

同步眨眼与面捕丢失

• 同步眨眼「从不」保留单眼动作；「总是」让两眼使用平均开合值。
  「当面部转动时」在左右侧转至少 15° 时采用较睁开的眼睛联动两眼，
  减少遮挡导致的误闭眼；回到正面后恢复独立眨眼。
• 超过 1 秒没有手机有效数据时，「保持动作」持续保持最后一帧，
  「回到待机状态」释放输入，交给 VTS 的待机动作。点击停止始终释放输入。
  ARKit UDP 没有人脸存在标志，此选项按有效数据断流判断。
• 默认同步眨眼「从不」、丢失后「回到待机状态」，兼容旧设置。
  摄像头 A/B 算法属于 VTS 电脑摄像头；本工具使用手机 ARKit 输入。

平滑调节

• 在「实时数值与调节 → 动作响应」中，分别调整「整体平滑」和「开合平滑」。
  数值越大越平滑，越小响应越快；设为 0 关闭对应平滑，修改后立即生效。
• 整体平滑默认 100 ms，作用于头部、视线等；嘴巴平滑最多 25 ms。
  开合平滑默认 8 ms，单独控制左右眼开合，不随整体平滑变化。
  旧设置中的整体平滑数值会保留；滑块调整后自动保存。

保存与恢复默认

• 窗口右下角「保存设置」会立即保存当前调节与校准，重开程序后继续使用。
  窗口底部会显示保存结果；正常关闭程序时也会保存当前设置。
•「恢复默认」会恢复灵敏度、幅度、平滑、反向和跟踪选项并保存。
  保留连接端口、帧率、手机方向与校准；整体平滑 100 ms，开合平滑 8 ms。

横放手机（旧版横屏面捕适配）

• 在「动作响应」的「手机方向」中，按面向手机屏幕时的前置镜头位置选择。
  镜头在你的左边选「横屏 · 镜头在左」，右边选「横屏 · 镜头在右」。
• 手机摆好后，自然睁开双眼、目视正中，重新点击「校准」。
  改变摆放方向或移动手机后需要重新校准。
• 若手机发送的数据方向本身已正确，保留「竖屏 / 不补偿」。
  眨眼、嘴巴和眼球视线按脸部方向处理；手机方向选项补偿头部转动轴。

接不上时

• 收不到手机：确认 IP、端口、LIVE、ARKit 模式和 iOS 的本地网络权限。
  Windows 防火墙如弹出提示，允许此程序在私人网络接收数据。
  Unreal Engine 可能占用 11111：可把两端 UDP 端口都改为 11112。

• VTS 未连接：确认 API 开关和端口。第一次授权需要在 VTS 中手动点允许。
  授权被拒绝后，点击「停止」，再点击「开始连接」。

• 数值正常但模型不动：检查 VTS 模型设置中的输入参数映射。
  常用输入是 FaceAngleX/Y/Z、EyeOpenLeft/Right、MouthOpen、MouthSmile。
  模型必须有对应的绑定；普通模型不能凭空增加高级嘴型或舌头动作。
  其他插件若正在覆盖同一参数，先停止那个插件。

• 头部或视线方向反了：在「调节」中勾选对应反向或交换左右眼。
  头部默认按弧度转角度；实机幅度可通过滑块调节。

「模拟动作」会用假数据驱动模型，方便先检查 VTS 连接。
它不会证明手机连接成功。退出再打开后，模拟模式默认关闭。

此工具独立接收 ARKit UDP 数据，运行时无需启动 Unreal Engine。
本版不接收 MetaHuman Animator 模式的数据，也不修改模型文件。
设置与授权保存在工具文件夹下的 data 目录。
"""


class App:
    def __init__(self, root: tk.Tk, storage: Storage):
        self.root, self.storage = root, storage
        error = None
        try:
            self.settings = storage.load_settings()
        except (ValueError, TypeError, OSError) as exc:
            self.settings = Settings()
            error = str(exc)
        self.engine = Bridge(self.settings, storage)
        self.ready = False
        self.stopping = False
        self.closing = False
        self.save_job = None
        self.last_logs = ""
        self.addresses = local_addresses()
        self.root.title(f"Live Link Face → VTube Studio  ·  {VERSION}")
        width = min(960, max(640, self.root.winfo_screenwidth() - 80))
        height = min(780, max(560, self.root.winfo_screenheight() - 90))
        self.root.geometry(f"{width}x{height}+40+10")
        self.root.minsize(min(880, width), height)
        self.root.configure(bg="#151c25")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._build()
        self.ready = True
        if error:
            self.engine.log(f"设置文件无法读取，已使用默认设置：{error}")
        self.engine.log("打开 VTS 插件 API 后开始连接；手机使用 Live Link（ARKit）模式")
        self.root.after(100, self.tick)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background="#151c25", foreground="#e7edf4")
        style.configure("TFrame", background="#151c25")
        style.configure("Panel.TFrame", background="#202b37")
        style.configure("Panel.TLabel", background="#202b37")
        style.configure("TLabel", background="#151c25", foreground="#e7edf4")
        style.configure("Muted.TLabel", foreground="#aab9c9")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TButton", padding=(14, 9), background="#31445a", borderwidth=0)
        style.configure("Compact.TButton", padding=(10, 3))
        style.map("TButton", background=[("active", "#435e78"), ("disabled", "#253342")],
                  foreground=[("disabled", "#8998a9")])
        style.configure("Accent.TButton", background="#56d4b2", foreground="#102a27")
        style.map("Accent.TButton", background=[("active", "#80e6ca"), ("disabled", "#324c49")],
                  foreground=[("disabled", "#a1b9b5")])
        style.configure("TEntry", fieldbackground="#253342", foreground="#e7edf4", padding=6)
        style.configure("TCombobox", fieldbackground="#253342", foreground="#e7edf4", padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", "#253342")], foreground=[("readonly", "#e7edf4")])
        style.configure("TCheckbutton", background="#151c25", foreground="#e7edf4", padding=0)
        style.map("TCheckbutton", background=[("active", "#151c25")])
        style.configure("TNotebook", borderwidth=0, tabmargins=(0, 0, 0, 6))
        style.configure("TNotebook.Tab", padding=(18, 8), background="#253342")
        style.map("TNotebook.Tab", background=[("selected", "#3b5269")])
        style.configure("Horizontal.TProgressbar", background="#56d4b2", troughcolor="#293848", borderwidth=0)
        style.configure("Horizontal.TScale", background="#151c25", troughcolor="#31445a")

    def _build(self):
        outer = ttk.Frame(self.root, padding=(24, 16))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Live Link Face → VTS", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="接收 iPhone 的 ARKit 面捕数据，实时驱动你的 Live2D。", style="Muted.TLabel").pack(anchor="w", pady=(2, 8))

        address = ttk.Frame(outer, style="Panel.TFrame", padding=8)
        address.pack(fill="x", pady=(0, 6))
        self.ip_text = tk.StringVar(value="   ·   ".join(self.addresses) if self.addresses else "未找到局域网 IP，请查看电脑网络设置")
        ttk.Label(address, text="手机目标 IP", style="Panel.TLabel").pack(side="left", padx=(0, 15))
        ttk.Label(address, textvariable=self.ip_text, style="Panel.TLabel", font=("Consolas", 13)).pack(side="left", expand=True, anchor="w")
        ttk.Button(address, text="复制 IP", style="Compact.TButton", command=self.copy_ip).pack(side="right")

        connection = ttk.Frame(outer)
        connection.pack(fill="x")
        self.udp_var = tk.StringVar(value=str(self.settings.udp_port))
        self.vts_var = tk.StringVar(value=str(self.settings.vts_port))
        self.fps_var = tk.StringVar(value=str(self.settings.fps))
        self.demo_var = tk.BooleanVar(value=False)
        self.connection_widgets = []
        for column, (label, variable) in enumerate((("手机 UDP 端口", self.udp_var), ("VTS API 端口", self.vts_var))):
            group = ttk.Frame(connection)
            group.grid(row=0, column=column, padx=(0, 18), sticky="w")
            ttk.Label(group, text=label).pack(anchor="w", pady=(0, 4))
            entry = ttk.Entry(group, textvariable=variable, width=12)
            entry.pack()
            entry.bind("<FocusOut>", lambda _: self.changed())
            self.connection_widgets.append(entry)
        group = ttk.Frame(connection)
        group.grid(row=0, column=2, padx=(0, 18), sticky="w")
        ttk.Label(group, text="发送帧率").pack(anchor="w", pady=(0, 4))
        combo = ttk.Combobox(group, textvariable=self.fps_var, values=(30, 60, 90, 120), width=7, state="readonly")
        combo.pack()
        combo.bind("<<ComboboxSelected>>", lambda _: self.changed())
        self.connection_widgets.append(combo)
        self.demo_check = ttk.Checkbutton(connection, text="模拟动作", variable=self.demo_var)
        self.demo_check.grid(row=0, column=3, padx=(5, 10), sticky="s")
        self.connection_widgets.append(self.demo_check)
        connection.columnconfigure(4, weight=1)
        self.start_button = ttk.Button(connection, text="开始连接", style="Accent.TButton", command=self.start)
        self.start_button.grid(row=0, column=4, sticky="se", padx=(0, 8))
        self.stop_button = ttk.Button(connection, text="停止", command=self.stop, state="disabled")
        self.stop_button.grid(row=0, column=5, sticky="se")

        status = ttk.Frame(outer, style="Panel.TFrame", padding=8)
        status.pack(fill="x", pady=(8, 8))
        self.phone_status = tk.StringVar(value="手机  ·  尚未开始")
        self.vts_status = tk.StringVar(value="VTS   ·  尚未连接")
        self.source_status = tk.StringVar(value="来源 —    收到 0 帧    发送 0 帧")
        ttk.Label(status, textvariable=self.phone_status, style="Panel.TLabel", wraplength=870).pack(anchor="w")
        ttk.Label(status, textvariable=self.vts_status, style="Panel.TLabel", wraplength=870).pack(anchor="w", pady=2)
        ttk.Label(status, textvariable=self.source_status, style="Panel.TLabel", foreground="#aab9c9").pack(anchor="w")

        footer = ttk.Frame(outer)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(footer, text=f"v{VERSION}   ·   局域网面捕 / 本机 VTS", style="Muted.TLabel").pack(side="left")
        self.restore_defaults_button = ttk.Button(footer, text="恢复默认", style="Compact.TButton", command=self.restore_defaults)
        self.restore_defaults_button.pack(side="right")
        self.save_settings_button = ttk.Button(footer, text="保存设置", style="Compact.TButton", command=lambda: self.save(show_error=True))
        self.save_settings_button.pack(side="right", padx=(0, 8))
        self.settings_status = tk.StringVar(value="调节后自动保存")
        ttk.Label(footer, textvariable=self.settings_status, style="Muted.TLabel").pack(side="right", padx=(8, 12))
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        adjust = ttk.Frame(notebook, padding=14)
        help_tab = ttk.Frame(notebook, padding=12)
        log_tab = ttk.Frame(notebook, padding=12)
        notebook.add(adjust, text="实时数值与调节")
        notebook.add(help_tab, text="接入说明")
        notebook.add(log_tab, text="连接日志")
        self._adjust(adjust)
        help_text = tk.Text(help_tab, wrap="word", font=("Microsoft YaHei UI", 10), bg="#151c25", fg="#d7e2ee",
                            relief="flat", padx=8, pady=8, insertbackground="#e7edf4")
        scroll = ttk.Scrollbar(help_tab, command=help_text.yview)
        help_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        help_text.pack(fill="both", expand=True)
        help_text.insert("1.0", HELP)
        help_text.configure(state="disabled")
        self.log_text = tk.Text(log_tab, wrap="word", font=("Microsoft YaHei UI", 10), bg="#151c25", fg="#bdcddb",
                                relief="flat", state="disabled", padx=6, pady=6)
        log_scroll = ttk.Scrollbar(log_tab, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

    def _adjust(self, parent):
        parent.columnconfigure(0, weight=1, uniform="adjust")
        parent.columnconfigure(1, weight=1, uniform="adjust")
        parent.rowconfigure(0, weight=1)
        left, right_shell = ttk.Frame(parent), ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 24))
        right_shell.grid(row=0, column=1, sticky="nsew")
        self.adjust_canvas = tk.Canvas(right_shell, bg="#151c25", highlightthickness=0, width=380, yscrollincrement=20)
        scroll = ttk.Scrollbar(right_shell, orient="vertical", command=self.adjust_canvas.yview)
        self.adjust_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.adjust_canvas.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(self.adjust_canvas)
        content = self.adjust_canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind("<Configure>", lambda _: self.adjust_canvas.configure(scrollregion=self.adjust_canvas.bbox("all")))
        self.adjust_canvas.bind("<Configure>", lambda event: self.adjust_canvas.itemconfigure(content, width=event.width))
        ttk.Label(left, text="表情输入", style="Sub.TLabel").pack(anchor="w", pady=(0, 10))
        self.meters = {}
        for key, label, low, high in (
            ("FaceAngleX", "头部左右", -30, 30), ("FaceAngleY", "头部上下", -30, 30),
            ("FaceAngleZ", "头部倾斜", -30, 30), ("EyeOpenLeft", "左眼开合", 0, 1),
            ("EyeOpenRight", "右眼开合", 0, 1), ("MouthOpen", "嘴巴开合", 0, 1),
            ("MouthSmile", "微笑", -1, 1), ("Brows", "眉毛", 0, 1),
        ):
            row = ttk.Frame(left)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=9).pack(side="left")
            number = tk.StringVar(value="—")
            ttk.Label(row, textvariable=number, width=6, anchor="e").pack(side="right")
            progress = ttk.Progressbar(row, maximum=1.0, length=160)
            progress.pack(side="left", fill="x", expand=True, padx=(6, 10))
            self.meters[key] = (number, progress, low, high)
        calibrate = ttk.Frame(left)
        calibrate.pack(fill="x", pady=(12, 4))
        self.calibrate_button = ttk.Button(calibrate, text="校准", command=self.calibrate)
        self.calibrate_button.pack(side="left")
        self.clear_calibration_button = ttk.Button(calibrate, text="清除校准", command=self.clear_calibration)
        self.clear_calibration_button.pack(side="left", padx=(8, 0))
        ttk.Label(calibrate, text="自然睁眼、看向正中", style="Muted.TLabel", wraplength=100).pack(side="left", padx=(12, 0))

        ttk.Label(right, text="动作响应", style="Sub.TLabel").pack(anchor="w", pady=(0, 8))
        orientation_row = ttk.Frame(right)
        orientation_row.pack(fill="x", pady=(0, 6))
        ttk.Label(orientation_row, text="手机方向", width=9).pack(side="left")
        self.orientation_var = tk.StringVar(value=ORIENTATION_CHOICES[self.settings.phone_orientation])
        self.orientation_combo = ttk.Combobox(orientation_row, textvariable=self.orientation_var,
                                             values=tuple(ORIENTATION_CHOICES.values()), width=23, state="readonly")
        self.orientation_combo.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.orientation_combo.bind("<<ComboboxSelected>>", lambda _: self.changed())
        self.tracking_choices = {}
        for key, label, choices in (("tracking_lost", "面捕丢失后", TRACKING_LOST_CHOICES),
                                    ("blink_sync", "同步眨眼", BLINK_SYNC_CHOICES)):
            row = ttk.Frame(right)
            row.pack(fill="x", pady=(0, 6))
            ttk.Label(row, text=label, width=10).pack(side="left")
            variable = tk.StringVar(value=choices[getattr(self.settings, key)])
            combo = ttk.Combobox(row, textvariable=variable, values=tuple(choices.values()), width=20, state="readonly")
            combo.pack(side="left", fill="x", expand=True, padx=(6, 0))
            combo.bind("<<ComboboxSelected>>", lambda _: self.changed())
            self.tracking_choices[key] = variable, combo, choices
        ttk.Label(right, text="输入：手机 ARKit · 手动灵敏度调节", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.tuning = {}
        self.sliders = {}
        for key, label, low, high in (
            ("blink_gain", "眨眼灵敏度", 0.1, 3), ("eye_open_gain", "睁眼灵敏度", 0.1, 3),
            ("mouth_gain", "张嘴灵敏度", 0.1, 4), ("smile_gain", "笑容灵敏度", 0.1, 3),
            ("brow_gain", "眉毛灵敏度", 0.1, 3), ("head_gain", "头部幅度", 0.1, 3),
            ("eye_gain", "视线幅度", 0.1, 3), ("smoothing_ms", "整体平滑", 0, 250),
            ("blink_smoothing_ms", "开合平滑", 0, 120),
        ):
            row = ttk.Frame(right)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label, width=10).pack(side="left")
            variable = tk.DoubleVar(value=getattr(self.settings, key))
            number = tk.StringVar()
            ttk.Label(row, textvariable=number, width=7, anchor="e").pack(side="right")
            self.tuning[key] = variable, number
            slider = ttk.Scale(row, from_=low, to=high, variable=variable, length=170, command=lambda _, k=key: self.tune(k))
            slider.pack(side="left", fill="x", expand=True, padx=(6, 10))
            self.sliders[key] = slider
            self.tune(key)
        ttk.Label(right, text="睁眼越高越易打开，闭眼越高越易闭合。", style="Muted.TLabel", wraplength=375).pack(anchor="w", pady=(2, 4))
        self.switches = {}
        switches_frame = ttk.Frame(right)
        switches_frame.pack(fill="x")
        for index, (key, text) in enumerate((("invert_x", "左右转头反向"), ("invert_y", "抬头低头反向"),
                                             ("invert_z", "头部倾斜反向"), ("swap_eyes", "交换左右眼"),
                                             ("invert_gaze", "左右视线反向"))):
            variable = tk.BooleanVar(value=getattr(self.settings, key))
            self.switches[key] = variable
            ttk.Checkbutton(switches_frame, text=text, variable=variable, command=self.changed).grid(
                row=index // 2, column=index % 2, sticky="w", padx=(0, 8))
        def scroll_controls(event):
            self.adjust_canvas.yview_scroll(-int(event.delta / 120) * 3, "units")
            return "break"

        def bind_scroll(widget):
            widget.bind("<MouseWheel>", scroll_controls)
            for child in widget.winfo_children():
                bind_scroll(child)

        bind_scroll(right)
        self.adjust_canvas.bind("<MouseWheel>", scroll_controls)

    def tune(self, key):
        variable, number = self.tuning[key]
        number.set(f"{variable.get():.0f} ms" if key.endswith("_ms") else f"{variable.get():.2f}×")
        self.changed()

    def read_settings(self) -> Settings:
        orientation = next(key for key, label in ORIENTATION_CHOICES.items() if label == self.orientation_var.get())
        return replace(self.settings, udp_port=int(self.udp_var.get()), vts_port=int(self.vts_var.get()),
                       fps=int(self.fps_var.get()), demo=self.demo_var.get(),
                       phone_orientation=orientation,
                       **{key: next(value for value, label in choices.items() if label == variable.get())
                          for key, (variable, _, choices) in self.tracking_choices.items()},
                       **{k: variable.get() for k, (variable, _) in self.tuning.items()},
                       **{k: variable.get() for k, variable in self.switches.items()})

    def changed(self):
        if not self.ready or self.closing:
            return
        try:
            self.settings = self.read_settings()
            self.engine.update_settings(self.settings)
            if self.save_job:
                self.root.after_cancel(self.save_job)
            self.settings_status.set("等待保存…")
            self.save_job = self.root.after(500, self.save)
        except ValueError:
            self.settings_status.set("设置有误，尚未保存")

    def save(self, show_error=False) -> bool:
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = None
        try:
            self.settings = self.read_settings()
            self.engine.update_settings(self.settings)
            self.storage.save_settings(self.settings)
            self.settings_status.set("设置已保存")
            if show_error:
                self.engine.log("设置已保存；重开程序后会继续使用当前调节与校准")
            return True
        except (OSError, ValueError, TypeError) as exc:
            self.settings_status.set("保存失败，请查看日志")
            self.engine.log(f"设置保存失败：{exc}")
            if show_error:
                messagebox.showerror("保存设置失败", str(exc), parent=self.root)
            return False

    def restore_defaults(self):
        defaults = Settings()
        self.ready = False
        try:
            for key, (variable, _) in self.tuning.items():
                variable.set(getattr(defaults, key))
                self.tune(key)
            for key, variable in self.switches.items():
                variable.set(getattr(defaults, key))
            for key, (variable, _, choices) in self.tracking_choices.items():
                variable.set(choices[getattr(defaults, key)])
        finally:
            self.ready = True
        if self.save(show_error=True):
            with self.engine.lock:
                self.engine.reset_mapper = True
            self.settings_status.set("已恢复默认并保存")
            self.engine.log("灵敏度、平滑、反向与跟踪选项已恢复默认；连接参数、手机方向与校准已保留")

    def start(self):
        try:
            self.settings = self.read_settings()
            self.engine.update_settings(self.settings)
            self.engine.start()
            self.save()
            self._controls(True)
        except OSError as exc:
            messagebox.showerror("无法监听手机端口", f"UDP 端口 {self.udp_var.get()} 无法使用。\n可将这里和手机都改为 11112。\n\n{exc}")
        except (ValueError, TypeError) as exc:
            messagebox.showerror("检查设置", str(exc))

    def _controls(self, running):
        for widget in self.connection_widgets:
            widget.configure(state="disabled" if running else ("readonly" if isinstance(widget, ttk.Combobox) else "normal"))
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def stop(self):
        self.engine.stop()
        self.stopping = True
        self.stop_button.configure(state="disabled")

    def calibrate(self):
        try:
            self.settings = self.engine.calibrate()
            self.save()
        except ValueError as exc:
            messagebox.showinfo("校准", str(exc))

    def clear_calibration(self):
        self.settings = replace(self.settings, head_zero=(0.0, 0.0, 0.0), gaze_zero=(0.0, 0.0, 0.0, 0.0),
                                blink_zero=(0.0, 0.0))
        self.engine.update_settings(self.settings)
        with self.engine.lock:
            self.engine.reset_mapper = True
        self.engine.log("已清除头部、视线与眼睛开合校准")
        self.save()

    def copy_ip(self):
        if self.addresses:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.addresses[0])
            self.engine.log(f"已复制目标 IP：{self.addresses[0]}")

    def tick(self):
        if self.closing:
            if not self.engine.running:
                self.root.destroy()
                return
            self.root.after(100, self.tick)
            return
        state = self.engine.snapshot()
        self.phone_status.set(f"手机  ·  {state.phone}")
        self.vts_status.set(f"VTS   ·  {state.vts}")
        active_rx = state.last_received and time.monotonic() - state.last_received <= 1
        rx = f"{state.rx_fps:.0f}" if active_rx else "0"
        tx = f"{state.tx_fps:.0f}" if state.connected else "0"
        self.source_status.set(f"来源 {state.source}    收到 {state.received} 帧 / {rx} fps    发送 {state.sent} 帧 / {tx} fps    无效 {state.invalid}")
        for key, (number, progress, low, high) in self.meters.items():
            if key in state.values:
                value = state.values[key]
                number.set(f"{value:.1f}°" if key.startswith("FaceAngle") else f"{value:.2f}")
                progress.configure(value=(value - low) / (high - low))
            else:
                number.set("—")
                progress.configure(value=0)
        with self.engine.lock:
            logs = "\n".join(self.engine.logs)
        if logs != self.last_logs:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("1.0", logs)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
            self.last_logs = logs
        if self.stopping and not self.engine.running:
            self.stopping = False
            self._controls(False)
        elif not self.engine.running and self.engine.threads and not self.stopping:
            self._controls(False)
        self.root.after(100, self.tick)

    def close(self):
        self.save(show_error=True)
        self.engine.stop()
        self.closing = True
        self.root.withdraw()


def main():
    parser = argparse.ArgumentParser(description="Live Link Face to VTube Studio")
    parser.add_argument("--self-test", action="store_true", help="运行协议与网络集成自检，退出码表示结果")
    parser.add_argument("--report", type=Path, help="自检结果 JSON 路径")
    parser.add_argument("--settings-dir", type=Path, help="自定义设置目录")
    parser.add_argument("--smoke-test", action="store_true", help="检查 GUI 创建后退出")
    args = parser.parse_args()
    if args.self_test:
        import io
        import unittest
        from tests import test_bridge
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_bridge))
        report = {"version": VERSION, "success": result.wasSuccessful(), "tests": result.testsRun, "output": stream.getvalue()}
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        elif sys.stdout:
            print(report["output"])
        raise SystemExit(0 if result.wasSuccessful() else 1)
    root = tk.Tk()
    app = App(root, Storage(args.settings_dir))
    if args.smoke_test:
        root.update()
        app.storage.save_settings(app.settings)
        report = {"version": VERSION, "success": True, "title": root.title(),
                  "size": [root.winfo_width(), root.winfo_height()], "meters": len(app.meters),
                  "settingsDirectory": str(app.storage.directory.resolve())}
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        elif sys.stdout:
            print(report)
        root.destroy()
    else:
        root.mainloop()


if __name__ == "__main__":
    main()
