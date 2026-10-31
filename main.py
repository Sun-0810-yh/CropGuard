import traceback
from datetime import datetime
import time
import os
import sys
import threading
import queue
import ast

# CRITICAL: torch must be imported before PyQt5 to avoid DLL conflicts
import cv2
import numpy as np
import torch

from ultralytics import YOLO

from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox, QFileDialog
from PyQt5 import QtCore, QtWidgets, QtGui
from PyQt5.QtGui import QColor

from UI import Ui_MainWindow, APP_STYLE, TABLE_COLUMN_WIDTHS

from tool.parser import get_config
from tool.tools import draw_info, result_info_format, format_data, writexls, writecsv, assess_risk
from tool.camera_source import get_source, release_source, spec_from_config
from llm import knowledge_base
from feedback.feedback import Feedback
from feedback.tts import TTSEngine

# 现场评分口径：GUI 与 Web 测评页（/eval）共用同一份纯函数，避免两处口径漂移
from app.eval_score import compute_eval_score as _compute_eval_score


# ==================== 从配置文件加载全局变量 ====================
# 注意：这些变量原来只在 `if __name__ == '__main__'` 里赋值，
# 导致「被别的模块 import 时」`MyMainWindow()` 一构造就 NameError
# （PyCharm 直接跑 main.py 恰好绕过）。现在统一在这里读，两条路径都能用。
config = get_config('./config/configs.yaml')
_cfg_ui = config.get('UI', {}) or {}

# UI配置
title = _cfg_ui.get('title', '农作物虫害检测识别系统')
label_title = _cfg_ui.get('label_title', '农作物虫害检测识别系统')
background = _cfg_ui.get('background', './icon/background.jpg')
zhutu2 = _cfg_ui.get('zhutu2', './icon/zhutu2.png')
background_img = _cfg_ui.get('background', background)     # 兼容旧变量名
padvalue = _cfg_ui.get('padvalue', [167, 200, 171])
column_color = _cfg_ui.get('column_color', [0, 0, 0])
label_control_color = _cfg_ui.get('label_control_color', '255,255,255, 80')
label_img_color = _cfg_ui.get('label_img_color', '255,255,255, 80')
_table_style = _cfg_ui.get('table_widget_info_styles', {}) or {}
header_background_color = _table_style.get('header_background_color', '48,77,49')
header_color = _table_style.get('header_color', '255,255,255')

# 摄像头配置
# 选相机优先用"设备标识"，数字索引只作兜底 ——
# 索引由 DirectShow 枚举顺序决定，插拔一次 USB / 重启一次就可能整个翻过来
# （本机实测：10-08 时 index1=外接，10-11 时 index1=内置，配置没动但相机换了）。
# 设备标识（VID/PID 或设备名）不会变，所以推荐写 camera_device。
_cfg_conn = config.get('CONFIG', {}) or {}
camera_device = str(_cfg_conn.get('camera_device') or '').strip()
camera_num = _cfg_conn.get('camera_num', 0)
# 与网页/演示台同一套口径（tool/camera_source.spec_from_config）：别再各处各写一份
camera_spec = spec_from_config(_cfg_conn)
# 中文名称映射
chinese_name = _cfg_conn.get('chinese_name', {})

# ==================== AI配置 ====================
# AI模型显示名称映射
AI_MODEL_DISPLAY_NAMES = {
    'deepseek': 'DeepSeek',
    'qwen': '通义千问',
    'openai': 'OpenAI',
    'zhipu': '智谱AI',
    'qianfan': '百度千帆',
    'doubao': '豆包大模型',
    'custom': '自定义模型',
}

# 加载AI模型配置
# 注意：只保留真正被用到的字段（on_advice_error 里要报超时/Token 上限）。
# 原来还有 ai_temperature / model_configs / current_model_config，
# 只被已删除的 show_model_info() 使用，一并清掉。
AI_CONFIG = config.get('AI', {})
active_model = AI_CONFIG.get('active_model', 'openai')
ai_timeout = AI_CONFIG.get('timeout', 60)
ai_max_tokens = AI_CONFIG.get('max_tokens', 2000)


# ==================== 摄像头 / 性能参数 ====================
# 摄像头采集与推理全部放到后台线程，Qt 主线程只负责显示，避免「跑一会就卡死」
_cam_cfg = config.get('CAMERA', {}) if config else {}
CAMERA_WIDTH = int(_cam_cfg.get('width', 1280) or 1280)    # 0=使用驱动默认
CAMERA_HEIGHT = int(_cam_cfg.get('height', 720) or 720)    # 0=使用驱动默认
# 强制 MJPG：很多 USB 摄像头默认走 YUY2，640x480 下只有 5~10fps，切 MJPG 才能上 30fps
CAMERA_FOURCC = _cam_cfg.get('fourcc', 'MJPG') or None     # 设为 None 则不强制
CAMERA_BACKEND = str(_cam_cfg.get('backend', 'auto') or 'auto')
CAMERA_STALE_SEC = float(_cam_cfg.get('stale_sec', 2.0) or 2.0)
DISPLAY_INTERVAL_MS = 30    # 定时器间隔(ms)：负责"及时取帧"，不等于渲染频率
# 预览渲染的最高频率(次/秒)。1280x720 的 BGR→RGB + 缩放是 CPU 活，
# 33fps 渲染会把界面拖卡（实测：光帧拷贝就 ~180MB/s）。15fps 肉眼已经流畅。
DISPLAY_FPS = float(_cam_cfg.get('display_fps', 15) or 15)
# 送进预览窗口的最长边(px)：超过就等比缩小后再显示，进一步省 CPU
DISPLAY_MAX_SIDE = int(_cam_cfg.get('display_max_side', 960) or 0)
MAX_TABLE_ROWS = 500        # 表格最大行数，防止长时间运行内存/绘制开销无限增长
CAMERA_LOG_INTERVAL = 1.0   # 摄像头/视频模式每隔多少秒才往表格记一行
# 相机探测（打不开时自动找可用索引；放后台线程，绝不阻塞界面）
CAMERA_PROBE_INDICES = 3    # 扫描 0 ~ N-1 号
CAMERA_PROBE_TIMEOUT = 1.2  # 每个索引打开超时（秒）；试 3 个后端总共最多约 10 秒
CAMERA_BACKENDS_TO_TRY = ('dshow', 'msmf', 'default')
# 打开设备后等第一帧的最长时间（秒）。实测 USB 相机冷启动要 ~3 秒才出图，
# 所以这里给到 8 秒；等的是后台采集线程，界面不会卡（只是按钮文字变化稍晚）
CAMERA_FIRST_FRAME_WAIT = float(_cam_cfg.get('probe_timeout', 8.0) or 8.0)

# ---- 历史记录 / 点击回看（底部表格）----
# ⚠️ 只有"样本"才写这张表：拍照识别、打开图片、打开文件夹。
#    实时检测只在预览上画框，不写日志（现场评测的 20 个样本必须干净）。
#    带框快照落盘，点表格行即可回看当时的画面。
HISTORY_DIR = str(_cam_cfg.get('history_dir', './output/snapshots') or './output/snapshots')

# 拍照后自动回到实时画面的等待时长（毫秒）。
# 原来拍完就永久定格，必须再点一次「开始运行 >」才能继续看实时 ——
# 现场 180 秒要拍 20 张，这一下就废掉好几秒。设 0 = 结果一回来就立刻恢复。
_snap_resume_cfg = _cam_cfg.get('snap_resume_ms', 400)
SNAP_RESUME_MS = 400 if _snap_resume_cfg is None else max(0, int(_snap_resume_cfg))

# 实时流投递给推理线程时用的固定帧名。拍照/图片/文件夹都用别的名字，
# 结果回来时靠它区分"实时帧"与"样本采集"（见 on_detect_result 的竞态说明）。
LIVE_FRAME_NAME = 'camera_frame.jpg'
SNAP_FRAME_NAME = 'camera_snap.jpg'

# ---- 现场评测（准确度 20 分 + 时效性 10 分 = 30 分）----
# 口径见 config/configs.yaml 的 EVAL 段注释。
_eval_cfg = config.get('EVAL', {}) or {}
EVAL_TARGET = int(_eval_cfg.get('target_samples', 20) or 20)
EVAL_POINTS_PER_SAMPLE = float(_eval_cfg.get('points_per_sample', 1.0) or 1.0)
EVAL_TIME_LIMIT = float(_eval_cfg.get('time_limit_sec', 180) or 180)
EVAL_TIME_FULL = float(_eval_cfg.get('time_full_score', 10.0) or 10.0)
EVAL_OVERTIME_PENALTY = float(_eval_cfg.get('overtime_penalty_per_sec', 0.5) or 0.5)
EVAL_AUTO_EXPORT_ON_CLOSE = bool(_eval_cfg.get('auto_export_on_close', True))
EVAL_TICK_MS = 200          # 倒计时刷新间隔（毫秒）
EVAL_ACC_FULL = EVAL_TARGET * EVAL_POINTS_PER_SAMPLE   # 准确度满分 = 20
EVAL_TOTAL_FULL = EVAL_ACC_FULL + EVAL_TIME_FULL       # 总分满分 = 30


def compute_eval_score(records, elapsed, target=None, pps=None,
                       limit=None, time_full=None, penalty=None):
    """按现场评分口径算分（纯函数，便于单测）。

    实现已抽到 `app/eval_score.py`（GUI 与 Web 测评页 /eval 共用同一份口径，
    防止两处各改一边导致"评委现场核对时两个数字对不上"）；这里保留同名包装，
    把 config 的 EVAL 段作为默认值传入。

    准确度 = min(正确数, target) × pps
    时效性 = 拍满 target 张：max(0, time_full − max(0, 用时 − limit) × penalty)   # 扣完为止；没拍满得 0
    总分   = 准确度 + 时效性

    「未评判」不计入正确 —— 漏判只会少分，不会被误判成对。
    """
    return _compute_eval_score(
        records, elapsed,
        target=EVAL_TARGET if target is None else target,
        pps=EVAL_POINTS_PER_SAMPLE if pps is None else pps,
        limit=EVAL_TIME_LIMIT if limit is None else limit,
        time_full=EVAL_TIME_FULL if time_full is None else time_full,
        penalty=EVAL_OVERTIME_PENALTY if penalty is None else penalty)


# ==================== 模型 / 推理参数（模块级，import 时也可用） ====================
# `python main.py` 会在入口块里真正加载模型（见文件末尾），
# 这里先把参数与占位符定义好，避免"被 import 时" MyMainWindow 一构造就 NameError。
_cfg_model = config.get('MODEL', {}) or {}
weights_path = _cfg_model.get('WEIGHT', './runs/train/pest27_final4/weights/best.pt')
conf_thres = float(_cfg_model.get('CONF', 0.4) or 0.4)
imgsz = int(_cfg_model.get('IMGSIZE', 640) or 640)
device = _cfg_model.get('DEVICE', 'cuda:0')
_cfg_classes = _cfg_model.get('CLASSES', None)
try:
    # literal_eval 而不是 eval：配置文件里的字符串不该能执行任意代码
    classes = ast.literal_eval(_cfg_classes) if isinstance(_cfg_classes, str) else _cfg_classes
except Exception:
    classes = None
if str(device).startswith('cuda') and not torch.cuda.is_available():
    print('警告: CUDA 不可用，自动回退到 CPU 推理')
    device = 'cpu'
# 真正的模型对象在 __main__ 块里赋值（`global yolo`）；import 场景下保持 None
yolo = None


# ==================== 相机可用性探测（后台线程） ====================
def probe_available_cameras(max_index=CAMERA_PROBE_INDICES, timeout=CAMERA_PROBE_TIMEOUT):
    """扫一遍 0..max_index-1，返回"真的能出图"的索引列表。

    为什么要它：现场多数"打开摄像头就卡住"其实是**根本没有可用相机**
    （没插 / 被禁用 / 没驱动）。这里给出可执行结论，而不是让用户猜。
    每个索引都在**子进程**里探测并硬超时，驱动层阻塞也拖不垮界面。
    """
    import subprocess
    import sys as _sys
    code = (
        "import sys, cv2, time\n"
        "idx=int(sys.argv[1]); be=sys.argv[2]\n"
        "flag={'dshow':cv2.CAP_DSHOW,'msmf':cv2.CAP_MSMF,'default':None}[be]\n"
        "try:\n"
        "    cap=cv2.VideoCapture(idx, flag) if flag is not None else cv2.VideoCapture(idx)\n"
        "except Exception:\n"
        "    print('fail'); sys.exit(0)\n"
        "if not cap.isOpened():\n"
        "    print('fail'); sys.exit(0)\n"
        "ok=False\n"
        "for _ in range(5):\n"
        "    r,f=cap.read()\n"
        "    if r and f is not None:\n"
        "        ok=True; break\n"
        "    time.sleep(0.05)\n"
        "cap.release()\n"
        "print('ok' if ok else 'fail')\n"
    )
    found = []
    for idx in range(int(max_index)):
        for backend in CAMERA_BACKENDS_TO_TRY:
            try:
                proc = subprocess.run([_sys.executable, '-c', code, str(idx), backend],
                                      capture_output=True, text=True, timeout=timeout)
            except Exception:
                continue
            if 'ok' in (proc.stdout or ''):
                found.append(idx)
                break
    return found


def _camera_probe_worker(out_queue, max_index=CAMERA_PROBE_INDICES):
    """后台探测线程：结果丢进队列，由 Qt 定时器取（避免跨线程碰控件）。"""
    try:
        out_queue.put(probe_available_cameras(max_index))
    except Exception:
        out_queue.put([])


class LocalAdviceWorker(QThread):
    """本地大模型建议生成线程：统一走 RAG + Ollama（与 ChatWorker 同一入口）。"""
    success = pyqtSignal(str)

    def __init__(self, results, pest_text, risk_info, chinese_name):
        super().__init__()
        self.results = results
        self.pest_text = pest_text
        self.risk_info = risk_info
        self.chinese_name = chinese_name

    def run(self):
        try:
            from llm import ai_gateway
            detections = []
            for r in (self.results or []):
                cls_name = r[0] if isinstance(r, (list, tuple)) else str(r)
                cn_name = self.chinese_name.get(cls_name, cls_name)
                conf = float(r[1]) if isinstance(r, (list, tuple)) and len(r) > 1 else 0.0
                detections.append({
                    'class_name': cls_name,
                    'chinese_name': cn_name,
                    'confidence': conf,
                })
            risk_level = ''
            risk_detail = ''
            for line in (self.risk_info or '').split('\n'):
                if line.startswith('病害风险等级：'):
                    risk_level = line.replace('病害风险等级：', '').strip()
                elif line.startswith('风险详情：'):
                    risk_detail = line.replace('风险详情：', '').strip()
            context = {'detections': detections,
                       'risk': {'level': risk_level, 'detail': risk_detail}}
            question = f"检测到 {self.pest_text or '害虫'}，风险等级 {risk_level}，请给出防治建议。"
            res = ai_gateway.answer(question, context=context)
            advice = res.get('answer') or res.get('advice', '')
            if advice:
                self.success.emit(advice)
        except Exception:
            # 本地大模型不可用时，静默保留知识库兜底版本
            pass


# ==================== 大模型问答线程（RAG + 防幻觉） ====================
class ChatWorker(QThread):
    """自由问答线程：调用 llm.ai_gateway（检索 → 接地提示词 → 本地 Ollama → 后校验）。

    与 LocalAdviceWorker 同一写法：网络/推理都在工作线程里跑，
    Qt 主线程只更新控件，避免 7B 生成时界面卡死。
    """
    done = pyqtSignal(dict)

    def __init__(self, question, history=None, context=None):
        super().__init__()
        self.question = question
        self.history = history or []
        self.context = context or None

    def run(self):
        try:
            from llm import ai_gateway
            res = ai_gateway.answer(self.question, history=self.history,
                                    context=self.context)
        except Exception as exc:
            res = {'success': False, 'source': 'error',
                   'answer': f'问答失败：{type(exc).__name__}: {str(exc)[:160]}',
                   'citations': [], 'guard': {}}
        self.done.emit(res)


# ==================== 摄像头采集 ====================
class CameraCapture:
    """摄像头采集适配器（GUI 侧）。

    真正的采集/看门狗/自愈已移到 tool/camera_source.py，
    这里只保留原来的接口（start/read/stop），避免 main.py 大面积改动。
    好处：GUI 与 api_server.py 共享同一套设备管理逻辑，不再两套实现各自卡死。
    """

    def __init__(self, index=0, width=0, height=0, fourcc=None, key=None):
        self.index = index
        # key 与 api_server 保持一致，同一相机全程只打开一次
        self.key = key if key is not None else ('cam', index)
        self._src = get_source(
            self.key, source=index,
            width=width or 0, height=height or 0, fourcc=fourcc,
            backend=CAMERA_BACKEND, stale_sec=CAMERA_STALE_SEC,
            name=f'相机{index}')

    def start(self):
        """确保设备已打开；返回是否"已打开"。

        语义与 CameraSource.start() 一致：**只保证设备打开成功**，
        首帧由后台采集线程继续等，调用方用 `wait_first_frame()` 显式等。

        注意：`get_source()` 在构造时已经尝试打开过一次，所以这里先看状态，
        已打开就直接返回 True（避免重复 open 抢设备、也避免重复起线程）。
        """
        if self._src is None:
            return False
        st = self._src.state()
        if st.get('opened_once') or st.get('opened'):
            return True
        return self._src.start()

    def wait_first_frame(self, timeout=None):
        """等首帧；返回是否拿到。实测 USB 相机冷启动要 ~3 秒，别用太小的超时。"""
        return self._src.wait_first_frame(timeout=timeout)

    def read(self):
        """非阻塞返回 (ok, 最新一帧副本)。"""
        return self._src.read()

    def peek(self):
        """零拷贝取最新帧引用（只读）。"""
        return self._src.peek()

    def state(self):
        return self._src.state()

    def stop(self):
        """释放本类引用；计数归零时才真正关设备。"""
        if self._src is not None:
            release_source(self.key)
            self._src = None


# ==================== 后台推理线程 ====================
class InferenceWorker(QThread):
    """在后台线程执行 YOLO 推理，Qt 主线程只刷新界面。

    单槽位 + 自动丢帧：推理跟不上采集时旧帧直接丢弃，永远只处理最新一帧，
    所以画面延迟不会随时间累积，界面也不会被推理阻塞。
    """
    # results, result_info, consum, input_time, img_name, frame
    # frame 一起回传：落盘的历史快照必须严格对应"当时推理的那一帧"。
    # 不能等回调时再去读 self.img —— 相机实时模式下那已经是最新的帧了，框会错位。
    result_ready = pyqtSignal(object, object, object, object, object, object)

    def __init__(self, infer_fn):
        super().__init__()
        self._infer = infer_fn
        self._cond = threading.Condition()
        self._pending = None
        self._running = True

    def submit(self, frame, img_name):
        with self._cond:
            self._pending = (frame, img_name)  # 只保留最新，旧的自动丢弃
            self._cond.notify()

    def clear(self):
        with self._cond:
            self._pending = None

    def run(self):
        while True:
            with self._cond:
                while self._running and self._pending is None:
                    self._cond.wait(0.1)
                if not self._running:
                    return
                frame, img_name = self._pending
                self._pending = None
            try:
                results, result_info, consum, input_time = self._infer(frame)
            except Exception:
                traceback.print_exc()
                continue
            self.result_ready.emit(results, result_info, consum, input_time, img_name, frame)

    def stop(self):
        with self._cond:
            self._running = False
            self._pending = None
            self._cond.notify_all()
        self.wait(2000)


# ==================== 主窗口类 ====================
class MyMainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self, cfg=None):
        super().__init__()
        self.frame_number = 0
        self.comboBox_index = None
        self.results = []
        self.result_img_name = None
        self.setupUi(self)

        # 根据config配置文件更新界面配置
        self.init_UI_config()
        self.start_type = None
        self.img = None
        self.img_path = None
        self.img_show = None
        self.img_name = None
        self.result_session_dir = None
        self.result_img_path = None
        self.result_txt = None
        self.output_dir = './output'

        # 默认选择为所有目标
        self.comboBox_value = '所有目标'
        self.comboBox_text = '所有目标'

        # 中文名映射
        self.chinese_name = chinese_name

        # 项目根路径
        self.ProjectPath = os.getcwd()

        # 行号计数器：show_table 里先 +=1 再写行，所以这里必须是 0，
        # 否则第一行会从 2 号开始（原来写 1，现场看着像"丢了一个样本"）。
        self.number = 0
        self.RowLength = 0
        self.consum_time = 0
        self.input_time = 0

        # 打开图片
        self.pushButton_img.clicked.connect(self.open_img)

        # 异步建议线程 / 问答线程。⚠️ 不能只用一个属性引用：连点两次时第二次赋值会让
        # 第一个仍在运行的 QThread 失去最后一个 Python 引用被析构，
        # Qt 直接 abort（"QThread: Destroyed while thread is still running"）→ 整个 GUI 闪退。
        # 所以所有在跑的线程都放进这个集合，finished 之后才移除。
        self.advice_thread = None
        self._live_threads = set()
        # 打开文件夹
        self.pushButton_dir.clicked.connect(self.open_dir)
        # 打开视频
        self.pushButton_video.clicked.connect(self.open_video)
        # 打开摄像头
        self.pushButton_camera.clicked.connect(self.open_camera)
        # 拍照识别（手持人工确认：拍一张 → 立刻检测）
        if hasattr(self, 'pushButton_snap'):
            self.pushButton_snap.clicked.connect(self.snap_and_detect)
        # 关闭摄像头
        if hasattr(self, 'pushButton_cam_close'):
            self.pushButton_cam_close.clicked.connect(self.close_camera)
        # 绑定开始运行
        self.pushButton_start.clicked.connect(self.start)
        # 导出数据
        self.pushButton_export.clicked.connect(self.write_files)

        # ---- 现场评测：开新一轮/结束本轮、评委判分、导出评分报告 ----
        if hasattr(self, 'pushButton_eval_start'):
            self.pushButton_eval_start.clicked.connect(self.on_eval_start_clicked)
        if hasattr(self, 'pushButton_verdict_ok'):
            self.pushButton_verdict_ok.clicked.connect(lambda: self.set_verdict('正确'))
        if hasattr(self, 'pushButton_verdict_bad'):
            self.pushButton_verdict_bad.clicked.connect(lambda: self.set_verdict('错误'))
        if hasattr(self, 'pushButton_eval_export'):
            self.pushButton_eval_export.clicked.connect(self.export_eval_report)

        # 保存结果
        self.pushButton_save.clicked.connect(self.save_current_result)

        # 获取防治建议
        self.pushButton_advice.clicked.connect(self.get_advice)

        self.comboBox.activated.connect(self.onComboBoxActivated)
        self.comboBox.mousePressEvent = self.handle_mouse_press

        # 大模型自由问答（RAG + 防幻觉）：与网页/手机走同一个 llm.ai_gateway
        self.chat_thread = None
        self.chat_history = []
        if hasattr(self, 'pushButton_chat'):
            self.pushButton_chat.clicked.connect(self.ask_chat)
            self.lineEdit_chat.returnPressed.connect(self.ask_chat)

        # 表格点击事件绑定
        self.tableWidget_info.cellClicked.connect(self.cell_clicked)

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_frame)

        # 后台任务轮询（相机打开 / 相机探测的结果都从队列里取）。
        # 原来探测结果只在 update_frame 里取，而相机打不开时 update_frame 根本不跑，
        # 结果就永远显示不出来。独立一个低频定时器，常驻运行。
        self._cam_open_q = queue.Queue()
        self._cam_open_token = 0          # 当前有效的打开请求（关闭/切源时 +1 作废旧请求）
        self._cam_open_launched = 0       # 最近一次真正发起的后台打开
        self._cam_opening = False
        self.bg_timer = QtCore.QTimer()
        self.bg_timer.timeout.connect(self._poll_background)
        self.bg_timer.start(150)

        # 摄像头采集线程 / 后台推理线程
        self.camera = None
        self.worker = None
        self.awaiting_result = False
        self._last_camera_log = 0.0
        # 预览节流 / 去重（避免每 30ms 重复渲染同一帧，把 CPU 烧在无用的转换上）
        self._last_display_ts = 0.0
        self._last_display_seq = None
        self._last_cam_stat_ts = 0.0

        self.image_files = []
        self.current_index = 0
        self.is_running = False
        self.sign = False

        # 信号反馈：语音播报 + 可选 Arduino 声光报警（离线）
        # 语音引擎 = feedback/tts.py（专用线程 + 多后端兜底），
        # 解决了旧版"只有第一次检测有语音、后面全哑"的线程/COM 问题。
        _fb = config.get('FEEDBACK', {}) if config else {}
        _tts_cfg = _fb.get('tts', {}) or {}
        self.tts = TTSEngine(
            enabled=bool(_fb.get('enabled', True)) and str(_tts_cfg.get('engine', 'auto')) != 'none',
            engine=str(_tts_cfg.get('engine', 'auto') or 'auto'),
            rate=float(_tts_cfg.get('rate', 150) or 150),
            volume=float(_tts_cfg.get('volume', 1.0) or 1.0),
            dedup_sec=float(_tts_cfg.get('dedup_sec', 0.5) or 0),
        )
        self.feedback = Feedback(
            serial_port=_fb.get('serial_port') or None,
            baud=int(_fb.get('baud', 9600)),
            enabled=bool(_fb.get('enabled', True)),
            speak_interval=float(_fb.get('speak_interval', 3)),
            tts=self.tts,
            announce_no_risk=bool(_fb.get('announce_no_risk', False)),
        )
        _st = self.feedback.status()['tts']
        print(f"[INFO] TTS engine={_st.get('engine')} ready={_st.get('ready')} "
              f"errors={_st.get('init_errors') or []}")

        # 相机可用性探测（后台线程 + 队列，界面永不被它阻塞）
        self._cam_probe_q = queue.Queue()
        self._cam_probe_thread = None
        self._cam_probe_result = None
        self._cam_open_error = ''

        # ---- 历史记录（底部表格）状态 ----
        # 每一行的完整数据（快照路径/结果/评判/用时）挂在第 0 列 item 的 UserRole 上，
        # 这样 removeRow(0) 裁剪旧行时 Qt 会连数据一起搬，不会和行号错位。
        # 表格是"视图"，记录才是数据源 —— 导出、评分都直接读记录，不读单元格文本。
        self._snap_seq = 0             # 快照文件名序号，保证同一秒内不重名
        # 记录唯一号：⚠️ PyQt 的 item.data(UserRole) 取回的是**深拷贝**，
        # 对象身份（is）不成立，所以查找/回写一律靠这个 uid，不能靠 id()。
        self._record_uid = 0
        self._current_record = None    # 当前展示的那一行（判分/保存都作用于它）
        self._snap_pending = False     # 刚点了「拍照识别」：结果回来后定格显示
        self._snap_resume_token = 0    # "拍照后自动恢复实时"的令牌（用户一停就作废）
        self._sample_capture = False   # 本次推理来自"明确的样本采集"（拍照），才写日志
        self._cam_label = ''           # 状态栏里的"设备名 分辨率"短标签

        # ---- 现场评测会话 ----
        # eval_t0 为 None 表示"还没开始计时"，会在第一行样本记录时自动打点。
        self.eval_t0 = None
        self.eval_t_stop = None
        self.eval_running = False
        self._eval_exported = False
        self.eval_timer = QtCore.QTimer()
        self.eval_timer.timeout.connect(self._eval_tick)
        self._eval_refresh()           # 先把面板刷成初始状态

    def init_UI_config(self):
        """
        根据config.yaml中的配置，更新界面
        """
        self.setWindowTitle(title)
        self.label_title.setText(label_title)
        # 背景图用配置里的 UI.background（原来这里引用了不存在的 background_img，
        # 一启动就 NameError 崩溃 —— 已修）。图片缺失时只跳过背景，不影响其他配置。
        # ⚠️ 必须在 APP_STYLE 后面追加，不能直接 setStyleSheet(背景规则) ——
        #    那会把整窗样式表（按钮悬停/按下/禁用态、表格样式）整个替换掉。
        try:
            qss = APP_STYLE
            if background and os.path.exists(background):
                bg = os.path.abspath(background).replace('\\', '/')
                qss += "\n#centralwidget {background-image: url('%s')}" % bg
            self.setStyleSheet(qss)
        except Exception:
            traceback.print_exc()
        self.label_img.setPixmap(QtGui.QPixmap(zhutu2))
        self.label_control.setStyleSheet(
            "background-color: rgba(%s); border-radius: 14px; border: 1px solid rgba(255,255,255,120);"
            % label_control_color)
        self.label_img.setStyleSheet(
            "background-color: rgba(%s); border-radius: 14px; border: 1px solid rgba(255,255,255,120);"
            % label_img_color)
        # 设置 tableWidget 表头的样式
        # （原来写成 `color: 112,63,46` 少了 rgb()，是无效 QSS，配置的字色从未生效）
        header_style_sheet = """
                    QHeaderView::section {{
                        background-color: rgb({header_background_color});
                        color: rgb({header_color});
                        border: none; padding: 6px 4px; font-weight: 600;
                    }}
                    """.format(
            header_background_color=header_background_color,
            header_color=header_color
        )
        hh = self.tableWidget_info.horizontalHeader()
        hh.setStyleSheet(header_style_sheet)
        # 表头套上样式表后 Qt 会按字体重算"最小列宽"（实测被抬到 170px），
        # setupUi 里设的列宽全部失效、右侧几列被挤出可视区 → 这里显式压回去再设一次
        hh.setMinimumSectionSize(40)
        for i, w in enumerate(TABLE_COLUMN_WIDTHS):
            self.tableWidget_info.setColumnWidth(i, w)

    def cell_clicked(self, row, column):
        """点历史行 → 定格回看当时那张带框快照，并恢复右侧信息栏。

        数据来源是行内 UserRole 上的记录（见 `_write_row`），不是从单元格文本
        eval 出来 —— 这样快照路径、风险等级、坐标都能原样还原。
        """
        self.update_comboBox_default()
        try:
            rec = self._row_record(row)
            if rec is None:
                return

            # 实时预览中点击历史行：先停掉实时刷新，否则画面 30ms 后就被新帧覆盖，
            # 用户"点了看不到那张照片"。停的只是 UI 定时器，采集线程保留。
            if self.is_running or self.timer.isActive():
                self.stop()
                print('[GUI] 已暂停实时检测，定格显示历史记录；'
                      '点「开始运行 >」继续。', flush=True)

            self._current_record = rec
            results = list(rec.get('results') or [])
            self.results = results
            self.img_path = rec.get('image_path')
            self.img_name = os.path.basename(self.img_path) if self.img_path else None
            self.input_time = rec.get('time')
            self.consum_time = rec.get('consum')

            # 1) 取图：摄像头/视频存的是"带框图"，图片/文件夹引用的是"原图"
            shown = None
            path = rec.get('image_path')
            src = None
            if path and os.path.exists(path):
                src = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)

            if src is not None:
                if rec.get('annotated', True) or not results:
                    # 本来就是带框图 → 原样显示，和当时保存的完全一致
                    shown = src
                else:
                    # 引用的是原图（图片/文件夹模式）→ 按当时的结果重画框
                    shown = draw_info(src.copy(), results)
            elif results and self.img is not None:
                # 2) 快照被清理/移走了：拿当前缓存的帧兜底重画，别让回看变成空白
                shown = draw_info(self.img.copy(), results)

            if shown is not None:
                self.img_show = shown
                self._display_image(shown)

            # 3) 信息栏：优先用当时存下来的 result_info（含风险等级/颜色/坐标）
            info = rec.get('result_info')
            if not info and results:
                info = result_info_format({}, results[0][2], results[0][1], results[0][0])
            if info:
                self.show_info(info)

            # 4) 已选中一行 → 评委可以判分了
            self._update_verdict_buttons()
        except Exception:
            traceback.print_exc()

    # ==================== 历史记录（表格）辅助 ====================
    def _row_record(self, row):
        """取某一行的隐藏记录（存在第 0 列 item 的 UserRole 上）。"""
        try:
            item = self.tableWidget_info.item(row, 0)
            if item is None:
                return None
            rec = item.data(QtCore.Qt.UserRole)
            return rec if isinstance(rec, dict) else None
        except Exception:
            return None

    def _write_row(self, row, rec):
        """把一条记录渲染到第 row 行（数据全在 rec 里，表格只负责显示）。

        8 列：编号 / 图片 / 输入时间 / 识别结果 / 目标数量 / 耗时 / 评判 / 保存状态。
        「图片」列只显示文件名 —— 表格是给评委看的，塞完整路径没意义，
        完整路径与其它字段都在记录里（导出走记录，不读单元格文本）。
        """
        verdict = rec.get('verdict')
        path = rec.get('image_path')
        short = os.path.basename(path) if path else '(无图)'
        values = [rec.get('no'), short,
                  rec.get('time'), self._results_text(rec.get('results')),
                  len(rec.get('results') or []), rec.get('consum'),
                  {None: '—', '正确': '✓ 正确', '错误': '✗ 错误'}.get(verdict, '—'),
                  rec.get('saved') or '待保存']
        for column, content in enumerate(values):
            item = QtWidgets.QTableWidgetItem(str(content))
            item.setTextAlignment(QtCore.Qt.AlignCenter)
            item.setForeground(QColor.fromRgb(column_color[0], column_color[1], column_color[2]))
            if column == 0:
                item.setData(QtCore.Qt.UserRole, rec)
            if column in (1, 3):
                item.setToolTip(path or '' if column == 1 else str(content))
            if column == 6:
                # 评判列上色，评委一眼能扫出已判/未判
                if verdict == '正确':
                    item.setForeground(QColor(20, 130, 60))
                elif verdict == '错误':
                    item.setForeground(QColor(180, 40, 40))
                else:
                    item.setForeground(QColor(140, 140, 140))
            self.tableWidget_info.setItem(row, column, item)

    def _results_text(self, results):
        """识别结果给人看：'蚜虫(aphids) 91%、粘虫(army_worm) 77%'。

        原来直接 str(list) —— 表格里是 [['aphids', 0.91, [200.0, ...]]]，评委根本没法看。
        保留英文类名：现场核对标签/权重时要用。
        """
        parts = []
        for r in (results or []):
            try:
                name, conf = r[0], float(r[1])
            except (TypeError, ValueError, IndexError):
                continue
            cn = self.chinese_name.get(name, name) if isinstance(self.chinese_name, dict) else name
            label = f'{cn}({name})' if cn != name else str(name)
            parts.append(f'{label} {conf:.0%}')
        return '、'.join(parts) or '—'

    def _find_row_by_record(self, rec):
        """按 uid 找行号；找不到返回 None。

        ⚠️ 用 uid 而不是 `is`：PyQt 的 item.data(Qt.UserRole) 返回的是**深拷贝**
        （实测 `got is rec` == False），身份比较永远不成立，
        会导致"改了 self._current_record 但表里那份没变"这种静默 bug。
        用 uid 还有个好处：removeRow(0) 裁剪旧行后行号会变，uid 不变。
        """
        if not isinstance(rec, dict):
            return None
        uid = rec.get('uid')
        for r in range(self.RowLength):
            other = self._row_record(r)
            if not isinstance(other, dict):
                continue
            if uid is not None and other.get('uid') == uid:
                return r
            if other is rec:                      # 兜底（万一将来 PyQt 改了行为）
                return r
        return None

    def _update_record(self, rec, **changes):
        """改表里那一份记录：按 uid 定位 → 取出 → 改 → 写回。返回写回后的记录。

        必须"取出再写回"，不能直接改传进来的 dict —— 见 `_find_row_by_record` 的说明。
        """
        row = self._find_row_by_record(rec)
        if row is None:
            return None
        stored = self._row_record(row)
        if not isinstance(stored, dict):
            return None
        stored.update(changes)
        self._write_row(row, stored)
        self._current_record = stored
        return stored

    def _all_records(self):
        """按行序取出所有记录（表格是视图，记录才是数据源）。"""
        out = []
        for r in range(self.RowLength):
            rec = self._row_record(r)
            if isinstance(rec, dict):
                out.append(rec)
        return out

    def _save_snapshot(self, annotated):
        """把带框结果图落盘，供表格点击回看；失败返回 None（不影响检测主流程）。"""
        if annotated is None:
            return None
        try:
            os.makedirs(HISTORY_DIR, exist_ok=True)
            self._snap_seq += 1
            name = datetime.now().strftime('%Y%m%d_%H%M%S') + f'_{self._snap_seq:04d}.jpg'
            path = os.path.join(HISTORY_DIR, name)
            cv2.imencode('.jpg', annotated)[1].tofile(path)
            return path
        except Exception:
            traceback.print_exc()
            return None

    def handle_mouse_press(self, event):
        """鼠标点击下拉列表时更新项目列表"""
        # 更新下拉列表项
        self.comboBox.clear()
        self.comboBox.addItems([self.comboBox_text])
        # 调用默认的mousePressEvent
        QtWidgets.QComboBox.mousePressEvent(self.comboBox, event)

    def onComboBoxActivated(self):
        """
        点击下拉列表
        """
        self.sign = True
        comboBox_text = self.comboBox.currentText()
        self.comboBox_index = self.comboBox.currentIndex()
        result_info = {}

        if len(self.results) == 0:
            print('图片中无目标！')
            QMessageBox.information(self, "信息", "图片中无目标", QMessageBox.Yes)
            return

        if comboBox_text == '所有目标':
            box = self.results[0][2]
            score = self.results[0][1]
            cls_name = self.results[0][0]
            lst_info = self.results
        else:
            select_result = self.results[self.comboBox_index - 1]
            box = select_result[2]
            cls_name = select_result[0]
            score = select_result[1]
            lst_info = [[cls_name, score, box]]

        result_info = result_info_format(result_info, box, score, cls_name)

        # 当前展示的如果是"已经画好框的快照"（摄像头/视频存的），就不能再往上
        # 叠一层框 —— 否则同一只虫会出现两个错位的框（重影）。原样显示 + 只刷信息栏。
        rec = self._current_record
        if isinstance(rec, dict) and rec.get('annotated') and rec.get('image_path') \
                and os.path.exists(rec['image_path']):
            snap = cv2.imdecode(np.fromfile(rec['image_path'], dtype=np.uint8), cv2.IMREAD_COLOR)
            if snap is not None:
                self.img_show = snap
                self._display_image(snap)
                self.show_info(result_info)
                return

        if not self.img_path or not os.path.exists(self.img_path):
            return
        self.img = cv2.imdecode(np.fromfile(self.img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if self.img is None:
            return
        self.img_show = draw_info(self.img, lst_info)
        self.show_all(self.img_show, result_info)

    def _display_image(self, img):
        """在 label_img 上显示图像。

        性能要点（现场实测很关键）：
        * 预览用 FastTransformation + 最长边限制，避免每帧大图缩放；
        * 1280x720 的 BGR→RGB 本身就要几毫秒，所以调用方必须做帧率节流
          （见 `update_frame` 的 DISPLAY_FPS），否则 CPU 被吃满、界面发顿。
        """
        if img is None:
            return
        # 太大先等比缩小（比在 Qt 里缩超大图便宜）
        try:
            h0, w0 = img.shape[:2]
            max_side = max(h0, w0)
            if DISPLAY_MAX_SIDE and max_side > DISPLAY_MAX_SIDE:
                scale = float(DISPLAY_MAX_SIDE) / float(max_side)
                img = cv2.resize(img, (max(1, int(w0 * scale)), max(1, int(h0 * scale))),
                                 interpolation=cv2.INTER_AREA)
        except Exception:
            pass
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w, ch = img_rgb.shape
        bytes_per_line = ch * w
        qt_img = QtGui.QImage(img_rgb.data, w, h, bytes_per_line, QtGui.QImage.Format_RGB888)
        pixmap = QtGui.QPixmap.fromImage(qt_img)
        # 缩放以适应label（实时画面用 FastTransformation，避免逐帧平滑缩放开销过大）
        scaled_pixmap = pixmap.scaled(
            self.label_img.width(), self.label_img.height(),
            QtCore.Qt.KeepAspectRatio, QtCore.Qt.FastTransformation
        )
        self.label_img.setPixmap(scaled_pixmap)

    def show_frame(self, img):
        """显示一帧图像"""
        self._display_image(img)
        if img is not None:
            self.img_show = img

    def _show_camera_off_placeholder(self):
        """把预览区切成"相机未开启"的黑屏样式，并复位右侧信息栏。

        关闭摄像头时如果只 stop 采集线程、不动 QLabel，界面会停在最后一帧上
        （看起来像卡死），所以这里显式画一块黑底 + 提示文字。
        """
        try:
            w = max(self.label_img.width(), 320)
            h = max(self.label_img.height(), 240)
            pixmap = QtGui.QPixmap(w, h)
            pixmap.fill(QtGui.QColor(0, 0, 0))
            painter = QtGui.QPainter(pixmap)
            try:
                painter.setPen(QtGui.QColor(140, 140, 140))
                font = QtGui.QFont('Microsoft YaHei')
                font.setPointSize(15)
                painter.setFont(font)
                painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter,
                                 '相机未开启\n\n点「打开摄像头」开始')
            finally:
                painter.end()
            self.label_img.setPixmap(pixmap)
        except Exception:
            traceback.print_exc()

        # 信息栏复位：否则还留着上一帧的类别/置信度，看着像"还在检测"
        for name, text in (('label_class', '--'), ('label_score', '--'),
                           ('label_risk', '--'),
                           ('label_xmin_v', 'xmin: --'), ('label_ymin_v', 'ymin: --'),
                           ('label_xmax_v', 'xmax: --'), ('label_ymax_v', 'ymax: --')):
            try:
                getattr(self, name).setText(text)
            except Exception:
                pass
        try:
            self.label_risk.setStyleSheet('font-weight: bold;')
        except Exception:
            pass

    @staticmethod
    def _camera_label(st):
        """状态栏用的短标签 = 设备名 + 分辨率。

        把**实际打开的设备名**显示出来很重要：索引会重排，画面又都是"正常的画面"，
        不写出来就分不清用的是外接还是内置（这正是现场踩过的坑）。
        标签只有 240px 宽，所以设备名过长要截断。
        """
        st = st or {}
        dev = str(st.get('device') or '').strip()
        if not dev:
            idx = st.get('index')
            dev = f'索引{idx}' if idx is not None else '摄像头'
        if len(dev) > 16:
            dev = dev[:15] + '…'
        size = st.get('size') or ''
        return f' {dev} {size}'.rstrip()

    def _reset_camera_view(self):
        """关摄像头后统一收尾：清空当前帧状态 + 显示"相机未开启"占位。

        ⚠️ 只清"当前帧"，**不动日志表格** —— 评测记录与回看必须保留下来。
        """
        self.img = None
        self.img_show = None
        self.results = []
        self.img_path = None
        self.img_name = None
        self._current_record = None
        self._last_display_seq = None
        self._update_verdict_buttons()
        self._show_camera_off_placeholder()

    def open_img(self):
        """打开单张图片"""
        try:
            self._cam_open_token += 1      # 切到别的输入源：后台还没开完的相机作废
            self.update_comboBox_default()
            self.img_path, filetype = QFileDialog.getOpenFileName(
                None, "选择文件", self.ProjectPath,
                "JPEG Image (*.jpg);;PNG Image (*.png);;JFIF Image (*.jfif);;All Files (*)"
            )
            if self.img_path == "":
                self.start_type = None
                return

            self.img_name = os.path.basename(self.img_path)
            self.label_img_path.setText(" " + self.img_path)
            self.label_dir_path.setText(" 选择图片文件夹")
            self.label_video_path.setText(" 选择视频文件")
            self.label_camera_path.setText(" 打开摄像头")

            self.start_type = 'img'
            self.img = cv2.imdecode(np.fromfile(self.img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
            self.show_frame(self.img)
        except Exception as e:
            traceback.print_exc()

    def open_dir(self):
        try:
            self._cam_open_token += 1      # 切到别的输入源：后台还没开完的相机作废
            self.update_comboBox_default()
            self.img_path_dir = QFileDialog.getExistingDirectory(None, "选择文件夹")
            if not self.img_path_dir:
                self.start_type = None
                return

            self.start_type = 'dir'
            self.label_dir_path.setText(" " + self.img_path_dir)
            self.label_img_path.setText(" 选择图片文件")
            self.label_video_path.setText(" 选择视频文件")
            self.label_camera_path.setText(" 打开摄像头")

            self.image_files = [file for file in os.listdir(self.img_path_dir) if file.lower().endswith(
                ('.bmp', '.dib', '.png', '.jpg', '.jpeg', '.pbm', '.pgm', '.ppm', '.tif', '.tiff'))]

            if not self.image_files:
                QMessageBox.information(self, "信息", "文件夹中没有符合条件的图片", QMessageBox.Yes)
                return

            self.current_index = 0
            self.img_path = os.path.join(self.img_path_dir, self.image_files[self.current_index])
            self.img_name = self.image_files[self.current_index]

            self.img = cv2.imdecode(np.fromfile(self.img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
            self.show_frame(self.img)
        except Exception as e:
            traceback.print_exc()

    def open_video(self):
        try:
            self._cam_open_token += 1      # 切到别的输入源：后台还没开完的相机作废
            self.update_comboBox_default()
            self.video_path, filetype = QFileDialog.getOpenFileName(None, "选择文件", self.ProjectPath,
                                                                     "mp4 Video (*.mp4);;avi Video (*.avi)")
            if not self.video_path:
                self.start_type = None
                return

            self.start_type = 'video'
            self.label_video_path.setText(" " + self.video_path)
            self.label_img_path.setText(" 选择图片文件")
            self.label_dir_path.setText(" 选择图片文件夹")
            self.label_camera_path.setText(" 打开摄像头")

            self.video_name = os.path.basename(self.video_path)
            self.video = cv2.VideoCapture(self.video_path)
            ret, self.img = self.video.read()
            if ret:
                self.img_name = f"{self.video_name}_frame.jpg"
                self.show_frame(self.img)
        except Exception as e:
            traceback.print_exc()

    def open_camera(self):
        """打开/关闭摄像头。

        ⚠️ 这里**绝不能弹阻塞式对话框、也不能在 UI 线程里长时间等待**：
        现场"打开摄像头就卡死"的经典原因就是——相机其实不可用，
        然后在 UI 线程弹了个模态框（窗口失去响应）或死等首帧。
        所以现在改成：状态写进界面提示 + 后台线程去探测哪个索引可用。
        """
        try:
            self.update_comboBox_default()
            text = self.label_camera_path.text()

            # ---------- 关闭 ----------
            if text == ' 摄像头已打开' or (self._cam_opening and self.start_type == 'camera'):
                self._cam_open_token += 1      # 后台还在打开的话，结果到了直接作废
                self.pushButton_start.setText("开始运行 >")
                self.label_camera_path.setText(" 摄像头已关闭")
                self.start_type = None
                self.is_running = False
                self.awaiting_result = False
                self.timer.stop()
                if self.camera is not None:
                    self.camera.stop()
                    self.camera = None
                self._reset_camera_view()      # 画面切黑屏，不要停在最后一帧
                return

            # ---------- 打开 ----------
            self.start_type = 'camera'
            self.label_img_path.setText(" 选择图片文件")
            self.label_dir_path.setText(" 选择图片文件夹")
            self.label_video_path.setText(" 选择视频文件")
            self.video_name = camera_spec
            self._cam_open_error = ''

            # 先释放上一个采集线程，避免设备被重复占用
            if self.camera is not None:
                self.camera.stop()
                self.camera = None

            # ⚠️ 打开设备 + 等首帧可能要 8 秒以上（冷启动、多策略解析、扫索引），
            # 原来直接在 UI 线程里做，整个窗口"未响应"。现在丢后台线程，
            # 结果经队列回到 _poll_background（Qt 控件只在主线程碰）。
            self._cam_opening = True
            self._cam_open_token += 1
            token = self._cam_open_token
            self._cam_open_launched = token
            self.label_camera_path.setText(" 正在打开摄像头…")
            threading.Thread(target=self._open_camera_job, args=(token, camera_spec),
                             name='CamOpen', daemon=True).start()
        except Exception as exc:
            # 关键：这里以前只打 traceback，界面上什么都不显示 ——
            # 用户就会看到"点了没反应/没画面"。现在把错误写进界面，绝不静默。
            traceback.print_exc()
            msg = (f'\n⚠ 打开摄像头时发生异常：{type(exc).__name__}: {exc}\n'
                   f'（已把详情打到控制台，请把这行发我）')
            try:
                self.textEdit_advice.setText(msg)
                self.label_camera_path.setText(" 摄像头不可用")
            except Exception:
                pass

    def _open_camera_job(self, token, spec):
        """后台线程：打开相机 + 等首帧，结果放进队列（这里绝不碰 Qt 控件）。"""
        cam = None
        try:
            cam = CameraCapture(spec, CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FOURCC)
            if not cam.start():
                self._cam_open_q.put((token, cam, 'fail', None))
                return
            got = cam.wait_first_frame(timeout=CAMERA_FIRST_FRAME_WAIT)
            self._cam_open_q.put((token, cam, 'ok' if got else 'noframe', None))
        except Exception as exc:
            traceback.print_exc()
            self._cam_open_q.put((token, cam, 'error', exc))

    def _poll_background(self):
        """主线程定时器：取相机打开结果 + 相机探测结果。"""
        try:
            self._poll_camera_open()
            if self.start_type != 'camera' or not self.timer.isActive():
                self._poll_camera_probe()     # 实时刷新在跑时由 update_frame 负责
        except Exception:
            traceback.print_exc()

    def _poll_camera_open(self):
        while True:
            try:
                token, cam, status, exc = self._cam_open_q.get_nowait()
            except queue.Empty:
                return
            if token == self._cam_open_launched:
                self._cam_opening = False
            if token != self._cam_open_token or self.start_type != 'camera':
                # 打开期间用户关了摄像头 / 切了输入源：这份结果作废，引用还回去
                if cam is not None:
                    cam.stop()
                continue
            self._on_camera_opened(cam, status, exc)

    def _on_camera_opened(self, cam, status, exc=None):
        """相机后台打开完成（主线程）：更新界面。"""
        try:
            if status == 'error':
                raise exc if exc is not None else RuntimeError('未知错误')
            if status == 'fail':
                st = cam.state()
                self._cam_open_error = st.get('last_error') or '未知原因'
                cam.stop()
                self.camera = None
                self.start_type = None
                # 非阻塞提示：写在界面状态栏 + 控制台，不弹模态框
                self.label_class.setText('摄像头不可用')
                self.label_score.setText('--')
                self.label_risk.setText('--')
                hint = (f"⚠ 无法打开摄像头 {camera_spec}（{self._cam_open_error}）"
                        f"；正在后台扫描可用索引…")
                self.label_camera_path.setText(" 摄像头不可用")
                print('[CAM] ' + hint, flush=True)
                self.textEdit_advice.setText(
                    hint + '\n\n控制台里能看到逐步日志（每个索引/后端/格式的尝试结果）。\n'
                    '常见原因：\n'
                    '1) 相机没插好 / 没装驱动 / 在设备管理器里被禁用；\n'
                    '2) 被别的程序占用（微信、QQ、浏览器、另一个 Python/PyCharm 窗口）；\n'
                    '3) camera_device 写的标识不该变；若用 camera_num 索引，注意它会被重排\n'
                    '   —— 跑 `python tool/ds_enum.py` 看当前「索引 ↔ 设备名」对应关系；\n'
                    '4) Windows 设置 → 隐私和安全性 → 相机：允许桌面应用访问相机。\n\n'
                    '（没有相机也能继续用：打开图片 / 文件夹 / 视频 都能检测）')
                self._start_camera_probe()
                return

            self.camera = cam
            if status == 'noframe':
                st = cam.state()
                msg = (f"⚠ 摄像头 {camera_spec} 已打开但 {CAMERA_FIRST_FRAME_WAIT:.0f}s 内没出画面"
                       f"（{st.get('last_error') or '无帧'}）；后台仍会继续尝试出帧。")
                print('[CAM] ' + msg, flush=True)
                self.textEdit_advice.setText(msg)
                self.label_camera_path.setText(" 摄像头无画面")
                self._start_camera_probe()
                return

            ok, frame = cam.read()
            if ok and frame is not None:
                self.img = frame
                self.img_name = LIVE_FRAME_NAME
                self.show_frame(frame)
                st = cam.state()
                # 明确写出"实际打开的是哪个设备" —— 万一用的不是外接相机，一眼就能看出来
                self._cam_label = self._camera_label(st)
                print(f"[CAM] 画面就绪 设备={st.get('device') or '未知'} "
                      f"index={st.get('index')} size={st.get('size')} fps={st.get('fps')} "
                      f"（点「开始运行 >」进入连续实时检测）", flush=True)
            self.label_camera_path.setText(" 摄像头已打开")
        except Exception as exc2:
            traceback.print_exc()
            msg = (f'\n⚠ 打开摄像头时发生异常：{type(exc2).__name__}: {exc2}\n'
                   f'（已把详情打到控制台，请把这行发我）')
            try:
                self.textEdit_advice.setText(msg)
                self.label_camera_path.setText(" 摄像头不可用")
            except Exception:
                pass

    # ---------- 拍照识别 / 关闭摄像头（手持人工确认场景） ----------
    def snap_and_detect(self):
        """拍照识别：抓当前一帧 → 立刻检测一次 → 显示带框结果。

        与"实时检测"的区别：实时是持续跑；拍照是**按需拍一张**，
        适合"农户手持、拍一下再决定要不要喷"的人工确认流程。
        没开摄像头时，用当前显示的图片也能跑一次检测（相当于"重检"）。
        """
        try:
            frame = None
            if self.camera is not None:
                ok, frame = self.camera.read()
                if not ok or frame is None:
                    st = self.camera.state()
                    msg = (f'⚠ 摄像头当前没有画面（{st.get("last_error") or "无帧"}）。'
                           f'请检查设备，或改用「打开图片」做检测。')
                    print('[CAM] ' + msg, flush=True)
                    self.textEdit_advice.setText(msg)
                    self._start_camera_probe()
                    return
                self.img = frame
                self.img_name = SNAP_FRAME_NAME
            elif self.img is not None:
                frame = self.img
                if self.img_name == LIVE_FRAME_NAME:
                    self.img_name = SNAP_FRAME_NAME   # 别和实时帧同名，否则结果回来会被当成实时帧
            else:
                self.textEdit_advice.setText(
                    '请先「打开摄像头」或「打开图片」，再点「📷 拍照识别」。')
                return

            self._ensure_worker()
            self.awaiting_result = True
            # 定格：停掉实时刷新，让画面停在"拍下的这一帧"。
            # 不停的话 30ms 后就被下一帧覆盖，用户根本看不到自己拍的那张。
            # ⚠️ 只对摄像头模式生效 —— 图片/文件夹模式点这个按钮不该把批量检测掐断。
            if self.start_type == 'camera':
                if self.is_running or self.timer.isActive():
                    self.timer.stop()
                    self.is_running = False
                    self.pushButton_start.setText('开始运行 >')
                self._snap_pending = True
            # 标记"这是一次明确的样本采集"：只有采集才会写识别日志
            #（实时流不写）。评测计时也从这一刻起算 —— 对应"第一张拍完开始计时"。
            self._sample_capture = True
            if self.eval_t0 is None:
                self._eval_begin(now=time.time())
            # 扔掉还排在槽里的实时帧：拍照这一帧必须是下一个被推理的
            if hasattr(self.worker, 'clear'):
                self.worker.clear()
            self.worker.submit(frame, self.img_name or SNAP_FRAME_NAME)
            self.input_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print('[CAM] 已拍下当前帧并定格，正在识别…', flush=True)
        except Exception:
            traceback.print_exc()

    def close_camera(self):
        """关闭摄像头：停定时器 + 释放采集源（引用计数归零才真关设备）。"""
        try:
            self._cam_open_token += 1          # 后台还在打开：结果到了直接作废
            if self.camera is not None:
                self.camera.stop()
                self.camera = None
            self.timer.stop()
            self.is_running = False
            self.awaiting_result = False
            self._snap_pending = False
            self.start_type = None
            self.pushButton_start.setText('开始运行 >')
            self.label_camera_path.setText(' 打开摄像头')
            self._reset_camera_view()          # 画面切黑屏，不要停在最后一帧
            print('[CAM] 摄像头已关闭（设备已释放，预览已复位）', flush=True)
        except Exception:
            traceback.print_exc()

    # ---------- 相机探测（后台） ----------
    def _start_camera_probe(self):
        """后台扫描可用索引；同一时间只跑一个探测线程。"""
        if self._cam_probe_thread is not None and self._cam_probe_thread.is_alive():
            return
        while not self._cam_probe_q.empty():      # 清掉上一次的旧结果
            try:
                self._cam_probe_q.get_nowait()
            except Exception:
                break
        self._cam_probe_result = None
        self._cam_probe_thread = threading.Thread(
            target=_camera_probe_worker, args=(self._cam_probe_q,),
            name='CamProbe', daemon=True)
        self._cam_probe_thread.start()

    def _poll_camera_probe(self):
        """在 UI 定时器里取探测结果（跨线程只通过队列传数据）。"""
        if self._cam_probe_result is not None or self._cam_probe_q.empty():
            return
        try:
            found = self._cam_probe_q.get_nowait()
        except Exception:
            return
        self._cam_probe_result = found or []
        if self._cam_probe_result:
            idx = self._cam_probe_result[0]
            extra = ('，另有：' + '、'.join(str(i) for i in self._cam_probe_result[1:])
                     if len(self._cam_probe_result) > 1 else '')
            msg = (f'🔍 后台扫描发现可用相机索引 {idx}{extra}。'
                   f'请把 config/configs.yaml 的 CONFIG.camera_num 改成 {idx} 后重开摄像头。')
        else:
            msg = ('🔍 后台扫描完成：0~'
                   f'{CAMERA_PROBE_INDICES - 1} 号索引都没有可用相机。'
                   '请检查相机是否插好/驱动是否正常（设备管理器 → 照相机），'
                   '或改用「打开图片/文件夹/视频」做检测。')
        print('[CAM] ' + msg, flush=True)
        self.label_camera_path.setText(" 摄像头不可用")
        self.textEdit_advice.append('\n' + msg)


    def show_all(self, img, info):
        '''
        展示所有的信息
        '''
        self.show_frame(img)
        self.show_info(info)

    def start(self):
        """开始运行检测"""
        self.update_comboBox_default()
        try:
            if self.start_type is None:
                QMessageBox.information(self, "信息", "请先选择输入类型！", QMessageBox.Yes)
                return

            # 如果正在运行，点击按钮 = 停止
            if self.is_running:
                self.stop()
                return

            # 惰性创建后台推理线程
            self._ensure_worker()

            if self.start_type == 'img':
                # 单张图片检测
                self.img = cv2.imdecode(np.fromfile(self.img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if self.img is None:
                    QMessageBox.information(self, "信息", "图片读取失败！", QMessageBox.Yes)
                    return
                self.is_running = True
                self.pushButton_start.setText('停止检测')
                self.awaiting_result = True
                self.worker.submit(self.img, self.img_name)
                return

            if self.start_type == 'camera' and self.camera is None:
                QMessageBox.information(self, "信息", "请先打开摄像头！", QMessageBox.Yes)
                return

            # 文件夹 / 视频 / 摄像头：启动定时器驱动
            self.is_running = True
            self.pushButton_start.setText('停止检测')
            self.timer.start(DISPLAY_INTERVAL_MS)

        except Exception as e:
            traceback.print_exc()

    def stop(self):
        """停止检测"""
        self.is_running = False
        self.awaiting_result = False
        self.timer.stop()
        # 用户主动停 → 作废"拍照后自动恢复实时"，否则几百毫秒后又被自动拉起来，
        # 用户会觉得"我明明点了停止，它自己又动了"。
        self._snap_resume_token += 1
        self.pushButton_start.setText('开始运行 >')
        if self.worker is not None:
            self.worker.clear()
        # 只释放视频文件句柄；摄像头采集线程要保留，继续排空缓冲防止卡死
        if self.start_type == 'video' and hasattr(self, 'video') and self.video is not None:
            try:
                self.video.release()
            except Exception:
                pass
            self.video = None

    def update_frame(self):
        """定时器回调：摄像头模式刷新画面并投递推理；图片/视频模式顺序处理。"""
        try:
            # 相机探测结果（如果后台在扫）——无论什么模式都要取，才能及时给提示
            self._poll_camera_probe()

            # ---------- 摄像头：画面永远用最新原始帧渲染，推理在后台跑 ----------
            if self.start_type == 'camera':
                if self.camera is None:
                    return

                # 1) 有没有新帧？没有就直接返回 —— 避免反复渲染同一帧白烧 CPU
                try:
                    seq = self.camera.seq()
                except Exception:
                    seq = None
                if seq is not None and seq == self._last_display_seq:
                    return

                # 2) 取最新帧：优先零拷贝 peek（只读引用，自己不改写）
                frame = None
                peek = getattr(self.camera, 'peek', None)
                if callable(peek):
                    try:
                        frame = peek()
                    except Exception:
                        frame = None
                if frame is None:
                    ok, frame = self.camera.read()          # 兜底：拷贝一份
                    if not ok:
                        frame = None

                if frame is None:
                    # 拿不到帧：不要静默什么都不做，否则用户看到的就是"画面卡住不动"
                    st = self.camera.state()
                    if st.get('state') != 'ok':
                        if not self._cam_open_error:
                            self._cam_open_error = st.get('last_error') or '无帧'
                        self.label_camera_path.setText(" 摄像头无画面")
                        self._start_camera_probe()
                    return

                # 3) 渲染节流：预览最多 DISPLAY_FPS 次/秒
                now = time.time()
                if now - self._last_display_ts < 1.0 / max(DISPLAY_FPS, 1):
                    return
                self._last_display_ts = now
                if seq is not None:
                    self._last_display_seq = seq

                # 4) 投递最新一帧给推理线程（线程内部自动丢帧，不会积压）
                if self.is_running and self.worker is not None:
                    self.worker.submit(frame.copy(), LIVE_FRAME_NAME)

                # 5) 渲染：没有检测框时**不拷贝**，直接显示原帧（省一次 2.7MB 拷贝）
                if self.results:
                    display = draw_info(frame.copy(), self.results)
                else:
                    display = frame
                self.img = frame
                self.img_show = display
                self.img_name = LIVE_FRAME_NAME
                self.show_frame(display)

                # 偶尔在状态栏回显实测 fps/帧龄，方便判断"是相机慢还是界面慢"
                if now - self._last_cam_stat_ts >= 2.0:
                    self._last_cam_stat_ts = now
                    st = self.camera.state()
                    age = st.get('frame_age')
                    if age is not None and age > max(CAMERA_STALE_SEC, 2.0):
                        # 相机卡住/断流：明确告警，而不是"画面静静不动"
                        self.label_camera_path.setText(f" 摄像头卡顿 {age:.1f}s")
                        print(f"[CAM] ⚠ 画面已 {age:.1f}s 没有更新"
                              f"（state={st.get('state')} reopens={st.get('reopens')}）"
                              f"；看门狗会自动重开，也可点「⏹ 关闭摄像头」再打开", flush=True)
                    else:
                        self.label_camera_path.setText(
                            f"{self._camera_label(st)} {st.get('fps') or 0}fps"
                            + (f" 帧龄{age}s" if age else ''))
                return

            # ---------- 文件夹 / 视频：等上一帧结果回来再读下一帧（背压） ----------
            if not self.is_running or self.awaiting_result:
                return

            if self.start_type == 'dir':
                if not self.image_files:
                    return
                if self.current_index >= len(self.image_files):
                    # 全部检测完毕，自动停止，不再循环
                    self.stop()
                    return

                self.img_name = self.image_files[self.current_index]
                self.img_path = os.path.join(self.img_path_dir, self.img_name)
                self.img = cv2.imdecode(np.fromfile(self.img_path, dtype=np.uint8), -1)
                self.current_index += 1
                if self.img is None:
                    return
                self.awaiting_result = True
                self.worker.submit(self.img, self.img_name)
                return

            if self.start_type == 'video':
                ret, frame = self.video.read()
                if not ret:
                    # 视频播完，自动停止，不再循环
                    self.stop()
                    return
                self.img = frame
                self.frame_number += 1
                frame_number = int(self.video.get(cv2.CAP_PROP_POS_FRAMES))
                self.img_name = f"{self.video_name}_{frame_number}.jpg"
                self.img_path = self.video_path
                self.awaiting_result = True
                self.worker.submit(frame, self.img_name)
                return
        except Exception:
            traceback.print_exc()

    def _ensure_worker(self):
        """惰性创建后台推理线程（只创建一次）。"""
        if self.worker is None:
            self.worker = InferenceWorker(self.run_inference)
            self.worker.result_ready.connect(self.on_detect_result)
            self.worker.start()

    def run_inference(self, img):
        """纯推理：不接触任何 Qt 控件，可安全地在后台线程调用。"""
        t1 = time.time()

        # 模型识别
        raw = yolo.predict(img, imgsz=imgsz, conf=conf_thres, device=device,
                           classes=classes, verbose=False)
        results = format_data(raw)

        # 一帧里可能同时检出多个目标：按置信度降序，保证 results[0] 就是"最可信的那个"。
        # 右侧「类别/置信度」和表格「识别结果」的第一条都取它 ——
        # 对应要求"同一张照片取置信度最高的进行展示"。
        results.sort(key=lambda r: float(r[1]), reverse=True)

        consum_time = str(round(time.time() - t1, 2)) + 's'
        input_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if len(results) > 0:
            box = results[0][2]
            score = results[0][1]
            cls_name = results[0][0]
        else:
            box = [0, 0, 0, 0]
            score = 0
            cls_name = '无目标'

        result_info = result_info_format({}, box, score, cls_name)

        # 病害风险等级评估
        risk_level, risk_color, risk_detail = assess_risk(results, img.shape[:2])
        result_info['risk_level'] = risk_level
        result_info['risk_color'] = risk_color
        result_info['risk_detail'] = risk_detail

        return results, result_info, consum_time, input_time

    def _throttle_ok(self):
        """视频模式限制写表格的频率，避免表格无限膨胀拖垮界面。"""
        if self.start_type == 'video':
            now = time.time()
            if now - self._last_camera_log < CAMERA_LOG_INTERVAL:
                return False
            self._last_camera_log = now
        return True

    def _record_detection(self, results, result_info, consum_time, input_time, frame):
        """把一个**样本**记进底部表格。规则（2026-10-11 现场口径）：

        1. **实时检测不写日志** —— 摄像头实时流只在预览上画框，
           评测的 20 个样本必须干净，不能被实时流刷进来；
        2. 写日志的只有"样本采集"：拍照识别 / 打开图片 / 打开文件夹；
        3. **没检测到目标不写行**（没检出就没有可评判的样本）；
        4. 一次采集 = 一行，**不再做同目标合并** —— 每个样本都要被评委单独打分，
           合并会把样本数吃掉；
        5. 带框快照落盘，点该行即可回看（见 `cell_clicked`）。
        """
        # 先取出并清掉"样本采集"标记：必须放在"无目标直接返回"之前，
        # 否则一次没检出的拍照会把标记留给下一帧。
        is_sample = bool(self._sample_capture)
        self._sample_capture = False

        # ---- 规则 1：实时流一律不写日志 ----
        # 视频逐帧检测和摄像头实时流是同一性质（连续帧，不是"一个样本"），
        # 写进去会把 20 个评测样本的表格刷满、还会提前触发"满 20 个自动停表"。
        if self.start_type in ('camera', 'video') and not is_sample:
            return

        if not results:
            if is_sample:
                print('[GUI] 本次采集未检测到目标，按规则不写入日志', flush=True)
            return

        annotated = draw_info(frame.copy(), results) if frame is not None else None

        # 图片来源：
        # * 图片/文件夹模式 —— 原图本来就在磁盘上，直接引用，不再复制一份
        #   （否则扫一遍 2000 张文件夹会白白多出 2000 个快照文件）。
        #   代价是回看时要按 results 重画框，所以 annotated=False 让 cell_clicked 知道。
        # * 摄像头/视频 —— 没有"单张原图文件"可引用，必须把带框图落盘。
        src_ref = None
        if (self.start_type in ('img', 'dir')
                and self.img_path and os.path.exists(self.img_path)):
            src_ref = self.img_path
        if src_ref:
            image_path, is_annotated = src_ref, False
        else:
            image_path = self._save_snapshot(annotated)
            is_annotated = image_path is not None

        now = time.time()
        if not self._throttle_ok():
            return
        # 计时打点：第一行样本落表时开始（图片/文件夹模式走这里；
        # 拍照模式在 snap_and_detect 里"拍下那一刻"就打点，更准）
        if self.eval_t0 is None:
            self._eval_begin(now=now)

        self._record_uid += 1
        rec = {
            'uid': self._record_uid,   # 稳定标识：查行/回写都靠它（见 _find_row_by_record）
            'no': None,          # 行号统一由 show_table 分配
            'image_path': image_path,
            'annotated': is_annotated,
            'time': input_time,
            'results': [list(r) for r in results],
            'result_info': dict(result_info or {}),
            'consum': consum_time,
            'saved': '待保存',
            'verdict': None,                                   # 评委判分：None/正确/错误
            'elapsed': round(max(0.0, now - self.eval_t0), 1),  # 该样本距计时起点的秒数
        }
        self._current_record = rec
        self.show_table(rec)

        # 满 20 个样本 → 自动停表（对应"180s 内完成 20 个样本"）
        if self.eval_running and self.RowLength >= EVAL_TARGET:
            self._eval_finish()
        self._eval_refresh()

    def apply_results(self, results, result_info, consum_time, input_time,
                      img_name, frame=None):
        """在 Qt 主线程中根据推理结果刷新界面。"""
        self.results = results
        self.consum_time = consum_time
        self.input_time = input_time
        if img_name:
            self.img_name = img_name

        self.get_comboBox_value(self.results)
        self.show_info(result_info)

        # 进识别日志：只有样本采集（拍照/图片/文件夹）才写，实时流不写
        self._record_detection(results, result_info, consum_time, input_time, frame)

        # 触发信号反馈：语音播报 + 声光报警
        _names = []
        for _r in self.results:
            _n = _r[0]
            _names.append(str(self.chinese_name.get(_n, _n)) if isinstance(self.chinese_name, dict) else str(_n))
        if hasattr(self, 'feedback'):
            self.feedback.trigger(result_info.get('risk_level', '无风险'), _names,
                                  result_info.get('risk_detail', ''))

    def on_detect_result(self, results, result_info, consum_time, input_time,
                         img_name, frame=None):
        """后台推理线程回调（在 Qt 主线程执行）。frame 是当时推理的那一帧。

        ⚠️ 拍照/实时竞态：实时检测在跑时点「拍照识别」，推理线程里往往**还有一帧实时帧
        正在推理**。它的结果会先回来 —— 旧代码会把它当成"拍下的那张"：
        定格错帧、消耗掉"样本采集"标记写进评测日志，真正拍的那张反而不记。
        180 秒 20 样本的计分就直接被污染。现在按帧名区分：拍照等待期间到达的
        实时帧结果一律丢弃（不定格、不写日志、不清 awaiting_result）。
        """
        is_live = (self.start_type == 'camera' and img_name == LIVE_FRAME_NAME)
        if is_live and (self._snap_pending or self._sample_capture):
            return
        self.awaiting_result = False
        self.apply_results(results, result_info, consum_time, input_time, img_name, frame)

        # 图片 / 文件夹 / 视频模式：结果图直接显示
        if self.start_type in ('dir', 'video', 'img'):
            base = self.img if self.img is not None else frame
            if base is not None:
                self.img_show = draw_info(base.copy(), results)
                self.show_frame(self.img_show)
            if self.start_type == 'img':
                self.is_running = False
                self.pushButton_start.setText('开始运行 >')

        # 拍照定格：把检测框画在"拍下的那一帧"上并冻住显示
        # （实时画面已停，这里显示的就是用户拍的那张，不会再被新帧覆盖）
        if self._snap_pending:
            self._snap_pending = False
            base = frame if frame is not None else self.img
            if base is not None:
                self.img = base
                self.img_show = draw_info(base.copy(), results)
                self._display_image(self.img_show)
                print('[CAM] 拍照定格完成，已写入历史记录（点该行可回看）', flush=True)
            # 看完这张就自动回到实时画面 —— 不用再点「开始运行 >」
            self._schedule_snap_resume()

    def _schedule_snap_resume(self):
        """拍照结果已显示 → 稍后自动回到实时画面。

        现场痛点：原来拍完就永久定格，必须再点一次「开始运行 >」才能继续看实时，
        180 秒拍 20 张根本来不及。这里用一个令牌（token）来处理竞态：
        期间用户又拍了一张、或主动点了停止、或切了输入源，旧回调就自动作废。
        """
        if self.start_type != 'camera' or self.camera is None:
            return
        self._snap_resume_token += 1
        token = self._snap_resume_token
        QtCore.QTimer.singleShot(max(0, SNAP_RESUME_MS),
                                 lambda: self._resume_live(token))

    def _resume_live(self, token):
        """恢复实时预览（只恢复画面/推理节奏，不碰相机设备）。"""
        if token != self._snap_resume_token:
            return                      # 又被拍了一次 / 用户点了停止 → 交给新的回调
        if self.start_type != 'camera' or self.camera is None:
            return                      # 相机已关 或 切成了图片/视频
        if self.awaiting_result:
            # 还有一次推理在路上，等它回来再恢复，别和它的定格显示打架
            QtCore.QTimer.singleShot(100, lambda: self._resume_live(token))
            return
        if self.is_running and self.timer.isActive():
            return                      # 已经在跑了，别重复启动
        self.is_running = True
        self.pushButton_start.setText('停止检测')
        self.timer.start(DISPLAY_INTERVAL_MS)
        print('[CAM] 已自动恢复实时画面', flush=True)

    # ==================== 现场评测（准确度 20 + 时效性 10 = 30）====================
    def _eval_elapsed(self, now=None):
        """本轮用时（秒）；还没开始计时返回 0.0。"""
        if self.eval_t0 is None:
            return 0.0
        end = self.eval_t_stop if self.eval_t_stop is not None else (now or time.time())
        return max(0.0, end - self.eval_t0)

    def _eval_begin(self, now=None):
        """开始计时（第一张样本时自动调用）。"""
        self.eval_t0 = float(now or time.time())
        self.eval_t_stop = None
        self.eval_running = True
        self._eval_exported = False
        if not self.eval_timer.isActive():
            self.eval_timer.start(EVAL_TICK_MS)

    def _eval_finish(self):
        """停表：记录最终用时，冻结得分。"""
        if self.eval_t0 is not None and self.eval_t_stop is None:
            self.eval_t_stop = time.time()
        self.eval_running = False
        self.eval_timer.stop()

    def _eval_tick(self):
        """倒计时刷新（独立定时器，与画面帧率解耦）。"""
        if self.eval_running and self.RowLength >= EVAL_TARGET:
            self._eval_finish()
        self._eval_refresh()

    @staticmethod
    def _fmt_mmss(seconds):
        seconds = max(0, int(round(seconds)))
        return f'{seconds // 60:02d}:{seconds % 60:02d}'

    def _eval_refresh(self):
        """把会话状态画到右侧面板上。"""
        try:
            elapsed = self._eval_elapsed()
            records = self._all_records()
            score = compute_eval_score(records, elapsed)

            # ---- 倒计时 / 超时 ----
            if self.eval_t0 is None:
                self.label_eval_timer.setText(self._fmt_mmss(EVAL_TIME_LIMIT))
                self.label_eval_timer.setStyleSheet('color: rgb(40,40,40);')
            elif self.eval_running and elapsed > EVAL_TIME_LIMIT:
                # 超时后转正计时，扣分实时可见
                self.label_eval_timer.setText(f'+{elapsed - EVAL_TIME_LIMIT:.0f}s')
                self.label_eval_timer.setStyleSheet(
                    'color: rgb(200,30,30); font-weight: bold;')
            elif self.eval_running:
                self.label_eval_timer.setText(self._fmt_mmss(EVAL_TIME_LIMIT - elapsed))
                self.label_eval_timer.setStyleSheet('color: rgb(40,40,40);')
            else:
                self.label_eval_timer.setText(self._fmt_mmss(elapsed))
                self.label_eval_timer.setStyleSheet(
                    'color: rgb(30,110,200); font-weight: bold;')

            # ---- 进度 / 得分 ----
            self.label_eval_progress.setText(
                f'样本 {score["total"]} / {EVAL_TARGET}    已评判 {score["judged"]}')
            self.label_eval_acc.setText(
                f'准确度   {score["acc"]:.1f} / {EVAL_ACC_FULL:.0f}')
            if self.eval_t0 is None:
                self.label_eval_time.setText(f'时效性   --  / {EVAL_TIME_FULL:.0f}')
            else:
                extra = f'（超时 {score["over"]:.0f}s）' if score['over'] else ''
                self.label_eval_time.setText(
                    f'时效性   {score["time_score"]:.1f} / {EVAL_TIME_FULL:.0f}{extra}')
            # 还没开始计时：时效性显示 "--"，总分也不能先把 10 分时效性算进去
            #（原来零样本时面板显示"总分 10.0 / 30"，看着像已经得了分）
            shown_total = score['acc'] if self.eval_t0 is None else score['score']
            self.label_eval_total.setText(
                f'总分    {shown_total:.1f} / {EVAL_TOTAL_FULL:.0f}')

            # ---- 按钮文案随状态切换 ----
            if hasattr(self, 'pushButton_eval_start'):
                if self.eval_running:
                    self.pushButton_eval_start.setText('■ 结束本轮评测')
                else:
                    self.pushButton_eval_start.setText('↻ 新一轮评测（清空日志）')
            self._update_verdict_buttons()
        except Exception:
            traceback.print_exc()

    def _update_verdict_buttons(self):
        """只有选中了某一行历史记录，判分按钮才可用。"""
        can = isinstance(self._current_record, dict)
        for name in ('pushButton_verdict_ok', 'pushButton_verdict_bad'):
            if hasattr(self, name):
                getattr(self, name).setEnabled(can)

    def set_verdict(self, verdict):
        """评委判分：给当前回看的那一行标 ✓ 正确 / ✗ 错误，并立即重算得分。

        再点一次同一个按钮 = 取消该判决（手滑可撤）。
        """
        rec = self._current_record
        if not isinstance(rec, dict):
            QMessageBox.information(self, '提示',
                                    '请先在下面的日志表里点一行（回看照片），再评判。',
                                    QMessageBox.Yes)
            return
        new_verdict = None if rec.get('verdict') == verdict else verdict
        stored = self._update_record(rec, verdict=new_verdict)
        if stored is None:
            print('[EVAL] 判分失败：没在日志表里找到该行', flush=True)
            return
        print(f'[EVAL] 第 {stored.get("no")} 行 判为 {new_verdict or "未评判"}', flush=True)
        self._eval_refresh()

    def on_eval_start_clicked(self):
        """一轮评测的开始/结束。"""
        if self.eval_running:
            self._eval_finish()
            self._eval_refresh()
            print('[EVAL] 本轮评测已结束并锁定得分', flush=True)
            return
        if self.RowLength:
            ans = QMessageBox.question(
                self, '开始新一轮评测',
                f'将清空当前日志表里的 {self.RowLength} 条记录并重新计时。\n'
                f'（如需留档请先点「导出评分报告」）\n\n确定继续？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        self._eval_reset()

    def _eval_reset(self):
        """清空日志与评测状态，等待第一张样本自动起表。"""
        self.tableWidget_info.setRowCount(0)
        self.RowLength = 0
        self.number = 0
        self._current_record = None
        self.eval_t0 = None
        self.eval_t_stop = None
        self.eval_running = False
        self._eval_exported = False
        self.eval_timer.stop()
        self._last_camera_log = 0.0
        self._update_verdict_buttons()
        self._eval_refresh()
        print('[EVAL] 日志已清空，等待第一张样本自动开始计时', flush=True)

    def _eval_report_rows(self):
        """评分报告内容：逐样本 + 末尾汇总（导出与自动导出共用）。"""
        elapsed = self._eval_elapsed()
        records = self._all_records()
        score = compute_eval_score(records, elapsed)

        rows = [['编号', '图片路径', '输入时间', '识别结果(中文)', '目标数量',
                 '推理耗时', '本轮用时(s)', '评判']]
        for rec in records:
            names = []
            for r in (rec.get('results') or []):
                en = r[0]
                cn = self.chinese_name.get(en, en) if isinstance(self.chinese_name, dict) else en
                names.append(f'{cn}({r[1]:.2f})')
            rows.append([
                rec.get('no'),
                rec.get('image_path') or '',
                rec.get('time') or '',
                '、'.join(names) or '无目标',
                len(rec.get('results') or []),
                rec.get('consum') or '',
                rec.get('elapsed') if rec.get('elapsed') is not None else '',
                rec.get('verdict') or '未评判',
            ])

        rows.append([])
        rows.append(['===== 现场评测汇总（口径：准确度 20 + 时效性 10 = 30）====='])
        rows.append(['采集样本数', f'{score["total"]} / {EVAL_TARGET}'])
        rows.append(['已评判', score['judged']])
        rows.append(['识别正确', score['correct']])
        rows.append(['准确度得分', f'{score["acc"]:.1f} / {EVAL_ACC_FULL:.0f}'])
        rows.append(['本轮总用时(s)', f'{score["elapsed"]:.1f}'])
        rows.append(['基准时间(s)', f'{EVAL_TIME_LIMIT:.0f}'])
        rows.append(['超时(s)', f'{score["over"]:.1f}'])
        rows.append(['时效性得分', f'{score["time_score"]:.1f} / {EVAL_TIME_FULL:.0f}'])
        rows.append(['总分', f'{score["score"]:.1f} / {EVAL_TOTAL_FULL:.0f}'])
        rows.append(['说明', '未评判不计入正确；时效性每超时 1 秒扣 '
                             f'{EVAL_OVERTIME_PENALTY}，扣完为止。'])
        return rows, score

    def export_eval_report(self, silent=False):
        """导出评分报告（csv + xls）。silent=True 时不弹提示（关窗自动导出用）。"""
        try:
            records = self._all_records()
            if not records:
                if not silent:
                    QMessageBox.information(self, '信息', '还没有样本，无法导出评分报告。',
                                            QMessageBox.Yes)
                return None
            rows, score = self._eval_report_rows()
            os.makedirs(self.output_dir, exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_path = os.path.join(self.output_dir, f'eval_report_{stamp}.csv')
            xls_path = os.path.join(self.output_dir, f'eval_report_{stamp}.xls')
            writecsv(rows, csv_path)
            writexls(rows, xls_path)
            self._eval_exported = True
            print(f'[EVAL] 评分报告已导出：{csv_path} | {xls_path} '
                  f'（总分 {score["score"]:.1f} / {EVAL_TOTAL_FULL:.0f}）', flush=True)
            if not silent:
                QMessageBox.information(
                    self, '成功',
                    f'评分报告已导出：\n{csv_path}\n{xls_path}\n\n'
                    f'正确 {score["correct"]} / {score["total"]}，'
                    f'用时 {score["elapsed"]:.1f}s，'
                    f'总分 {score["score"]:.1f} / {EVAL_TOTAL_FULL:.0f}', QMessageBox.Yes)
            return csv_path
        except Exception:
            traceback.print_exc()
            if not silent:
                QMessageBox.warning(self, '错误', '导出评分报告失败，详情见控制台。')
            return None

    def closeEvent(self, event):
        """窗口关闭时释放摄像头、推理线程与串口，避免设备被一直占用。"""
        try:
            # 评测还没导出就关窗 → 先静默存一份，别把现场成绩弄丢
            if (EVAL_AUTO_EXPORT_ON_CLOSE and not self._eval_exported
                    and self.RowLength > 0):
                self.export_eval_report(silent=True)
            self.eval_timer.stop()
            self.timer.stop()
            self.bg_timer.stop()
            self._cam_open_token += 1
            if self.camera is not None:
                self.camera.stop()
                self.camera = None
            # 还在跑的大模型线程：给一点时间收尾，避免进程退出时析构运行中的 QThread
            for th in list(self._live_threads):
                th.wait(1500)
            if self.worker is not None:
                self.worker.stop()
                self.worker = None
            if hasattr(self, 'video') and self.video is not None:
                self.video.release()
                self.video = None
            if hasattr(self, 'feedback'):
                self.feedback.close()
        except Exception:
            traceback.print_exc()
        super().closeEvent(event)

    def get_advice(self):
        """获取防治建议：知识库秒出兜底 + 本地大模型异步增强（离线）"""
        try:
            if not hasattr(self, 'results') or not self.results:
                self.textEdit_advice.setText("请先进行病虫害检测！")
                return

            # 构建检测结果文本
            pest_names = []
            for result in self.results:
                if len(result) >= 3:
                    pest_name = result[0]
                    confidence = result[1]
                    chinese_pest_name = self.chinese_name.get(pest_name, pest_name)
                    pest_names.append(f"{chinese_pest_name}(置信度:{confidence:.2f})")
            pest_text = "、".join(pest_names)

            # 获取风险等级
            risk_info = ""
            risk_level = '中风险'
            risk_detail = ''
            if self.results:
                risk_level, _, risk_detail = assess_risk(self.results)
                risk_info = f"\n病害风险等级：{risk_level}\n风险详情：{risk_detail}"

            # 1) 知识库立即秒出，保证离线稳定
            self.textEdit_advice.setText(
                knowledge_base.get_advice(self.results, risk_level, risk_detail)
            )

            # 2) 本地大模型异步增强，成功后覆盖知识库版本
            #    在跑的时候再点只刷新知识库版本，不再叠一个大模型线程
            if self.advice_thread is not None and self.advice_thread.isRunning():
                return
            self.advice_thread = LocalAdviceWorker(
                self.results, pest_text, risk_info, self.chinese_name)
            self.advice_thread.success.connect(self.on_advice_success)
            self._start_thread(self.advice_thread)

        except Exception as e:
            self.textEdit_advice.setText(f"获取防治建议出错：\n{str(e)}")

    def get_comboBox_value(self, results):
        '''
        获取当前所有的类别和ID，点击下拉列表时，使用
        '''
        lst = ["所有目标"]
        for bbox in results:
            cls_name = bbox[0]
            lst.append(str(cls_name))
        self.comboBox_value = lst

    def show_info(self, result):
        try:
            if not result:
                return
            # 用 .get 而不是 []：result_info 由多处拼装，历史上漏字段会把整个
            # 信息栏刷成空白（异常被吞掉，界面上看着像"检测没反应"）。
            cls_name = result.get('cls_name') or '无目标'
            if isinstance(self.chinese_name, dict) and cls_name in self.chinese_name:
                cls_name = self.chinese_name[cls_name]
            if len(str(cls_name)) > 10:
                lst_cls_name = str(cls_name).split('_')
                cls_name = lst_cls_name[0][:10] + '...'

            self.label_class.setText(str(cls_name))
            self.label_score.setText(str(result.get('score', '--')))

            # 显示风险等级
            risk_level = result.get('risk_level', '--')
            risk_color = result.get('risk_color', (0, 0, 0))
            self.label_risk.setText(str(risk_level))
            self.label_risk.setStyleSheet(
                f"color: rgb({risk_color[2]},{risk_color[1]},{risk_color[0]}); font-weight: bold;"
            )

            self.label_xmin_v.setText("xmin: " + str(result.get('label_xmin_v', '--')))
            self.label_ymin_v.setText("ymin: " + str(result.get('label_ymin_v', '--')))
            self.label_xmax_v.setText("xmax: " + str(result.get('label_xmax_v', '--')))
            self.label_ymax_v.setText("ymax: " + str(result.get('label_ymax_v', '--')))
        except Exception as e:
            traceback.print_exc()

    def update_comboBox_default(self):
        """
        将下拉列表更新为 所有目标 默认状态
        """
        self.comboBox.clear()
        self.comboBox.addItems([self.comboBox_text])

    def show_table(self, rec=None):
        """把一条记录追加成新的一行。

        只在**样本采集且检测到目标**时被 `_record_detection` 调用；行号在这里统一分配。
        新增后自动滚到底并高亮该行 —— 这就是"拍照确认感"：日志里立刻多出一行。
        （用 selectRow 只高亮，不触发 cellClicked，所以不会改动正在显示的检测图。）
        """
        try:
            rec = rec if rec is not None else self._current_record
            if rec is None:
                return
            self.number += 1
            rec['no'] = self.number
            self.RowLength += 1
            self.tableWidget_info.setRowCount(self.RowLength)
            row = self.RowLength - 1
            self._write_row(row, rec)
            # 限制最大行数：长时间运行不会让表格无限膨胀拖垮界面
            if self.RowLength > MAX_TABLE_ROWS:
                self.tableWidget_info.removeRow(0)
                self.RowLength -= 1
                row = max(0, row - 1)
            self.tableWidget_info.scrollToBottom()
            self.tableWidget_info.selectRow(row)
        except Exception:
            traceback.print_exc()

    def write_files(self):
        """
        导出 excel、csv 数据
        """
        try:
            if self.RowLength == 0:
                QMessageBox.information(self, "信息", "没有数据可导出！", QMessageBox.Yes)
                return

            # 从**记录**收集数据（表格只是视图；记录里才有完整路径、评判与用时）
            all_data = []
            headers = ["编号", "图片路径", "输入时间", "识别结果", "目标数量", "耗时",
                       "评判", "保存状态"]
            all_data.append(headers)

            for rec in self._all_records():
                all_data.append([
                    rec.get('no'),
                    rec.get('image_path') or '',
                    rec.get('time') or '',
                    str(rec.get('results') or []),
                    len(rec.get('results') or []),
                    rec.get('consum') or '',
                    rec.get('verdict') or '未评判',
                    rec.get('saved') or '待保存',
                ])

            # 保存到output目录
            if not os.path.exists(self.output_dir):
                os.mkdir(self.output_dir)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # 导出Excel
            xls_path = os.path.join(self.output_dir, f"detection_results_{timestamp}.xls")
            writexls(all_data, xls_path)

            # 导出CSV
            csv_path = os.path.join(self.output_dir, f"detection_results_{timestamp}.csv")
            writecsv(all_data, csv_path)

            QMessageBox.information(self, "成功",
                                    f"数据已导出到：\n{xls_path}\n{csv_path}",
                                    QMessageBox.Yes)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.warning(self, "错误", f"导出失败：{str(e)}")

    def save_current_result(self):
        """保存：每次点击在 output/类别名/ 生成新的检测结果图与防治方案；无检测不保存"""
        try:
            if self.img_show is None or self.img_name is None or not self.results:
                QMessageBox.information(self, "信息", "未检测到目标，\n未保存。", QMessageBox.Yes)
                return

            if not os.path.exists(self.output_dir):
                os.mkdir(self.output_dir)

            self.result_session_dir = None
            self.result_txt = None
            self.result_img_path = None

            advice_txt = self.textEdit_advice.toPlainText().strip() if hasattr(self, 'textEdit_advice') else ''
            detected_names = []
            try:
                for r in (self.results or []):
                    name = r[0]
                    mapped = self.chinese_name.get(name, name) if hasattr(self, 'chinese_name') else name
                    detected_names.append(str(mapped))
            except Exception:
                pass

            if detected_names:
                seen = set()
                ordered = []
                for n in detected_names:
                    if n not in seen:
                        seen.add(n)
                        ordered.append(n)
                base_name = '+'.join(ordered)
            else:
                QMessageBox.information(self, "信息", "未检测到目标，\n未保存。", QMessageBox.Yes)
                return

            for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
                base_name = base_name.replace(ch, '_')
            if len(base_name) > 60:
                base_name = base_name[:60]

            base_dir_name = base_name
            category_dir = os.path.join(self.output_dir, base_dir_name)
            suffix = 1
            while os.path.exists(category_dir):
                base_dir_name = f"{base_name}({suffix})"
                category_dir = os.path.join(self.output_dir, base_dir_name)
                suffix += 1

            os.mkdir(category_dir)

            # 保存检测结果图片
            img_path = os.path.join(category_dir, f"{base_dir_name}.jpg")
            cv2.imencode('.jpg', self.img_show)[1].tofile(img_path)
            self.result_img_path = img_path

            # 保存防治方案文本
            if advice_txt and '请先进行病虫害检测' not in advice_txt and '未配置' not in advice_txt:
                txt_path = os.path.join(category_dir, f"{base_dir_name}_防治方案.txt")
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(advice_txt)

            # 回写表格「保存状态」列：历史行能一眼看出哪些已经导出过
            # （走 _update_record：PyQt 取回的是深拷贝，直接改 self._current_record 不会落表）
            rec = self._current_record
            if isinstance(rec, dict):
                self._update_record(rec, saved='已保存')

            QMessageBox.information(self, "成功", f"已保存：\n{base_dir_name}\n检测结果图与防治方案。", QMessageBox.Yes)
        except Exception:
            traceback.print_exc()

    def on_advice_success(self, advice: str):
        """AI建议获取成功回调"""
        self.textEdit_advice.setText(advice)
        if hasattr(self, 'pushButton_advice'):
            self.pushButton_advice.setEnabled(True)

    def on_advice_error(self, msg: str):
        """AI建议获取失败回调"""
        current_model_display = AI_MODEL_DISPLAY_NAMES.get(active_model, active_model)

        error_text = f"""❌ 获取防治建议失败

错误信息：{msg}

💡 可能的解决方案：
1. 检查网络连接是否正常
2. 确认API Key是否有效
3. 尝试重新点击"获取防治建议"按钮
4. 如果问题持续，请检查配置文件中的API设置

🔧 技术支持：
- 当前模型：{current_model_display}
- 超时设置：{ai_timeout}秒
- 最大Token：{ai_max_tokens}"""

        self.textEdit_advice.setText(error_text)
        if hasattr(self, 'pushButton_advice'):
            self.pushButton_advice.setEnabled(True)

    # ==================== 大模型自由问答（RAG + 防幻觉） ====================
    def _current_context(self):
        """把当前检测结果整理成问答上下文（让大模型结合当前虫情回答）。"""
        try:
            if not self.results:
                return None
            dets = []
            for r in self.results:
                name = r[0]
                dets.append({
                    'class_name': name,
                    'chinese_name': self.chinese_name.get(name, name)
                    if isinstance(self.chinese_name, dict) else name,
                    'confidence': float(r[1]) if len(r) > 1 else 0.0,
                })
            risk_level, _, risk_detail = assess_risk(self.results)
            return {'detections': dets,
                    'risk': {'level': risk_level, 'detail': risk_detail}}
        except Exception:
            return None

    def ask_chat(self):
        """提交问题：工作线程里跑 RAG + 本地大模型，界面不卡。"""
        try:
            if not hasattr(self, 'lineEdit_chat'):
                return
            q = self.lineEdit_chat.text().strip()
            if not q:
                self.textEdit_advice.append('请输入问题后再提问。')
                return
            self.lineEdit_chat.clear()
            self.textEdit_advice.append(f'\n你：{q}\n正在检索知识库并调用本地大模型…')
            self._last_chat_question = q

            self.chat_thread = ChatWorker(q, history=list(self.chat_history),
                                          context=self._current_context())
            # 问题随线程走：连问两句时，回答要配对到各自的问题上
            self.chat_thread.done.connect(lambda res, _q=q: self.on_chat_done(res, _q))
            self._start_thread(self.chat_thread)
        except Exception as exc:
            self.textEdit_advice.append(f'提问失败：{exc}')

    def _start_thread(self, th):
        """启动 QThread 并持有引用直到它结束（防"连点即崩溃"，见 __init__ 说明）。"""
        self._live_threads.add(th)
        th.finished.connect(lambda t=th: self._live_threads.discard(t))
        th.start()

    def on_chat_done(self, res: dict, question=None):
        """问答返回：显示回答 + 依据 + 防幻觉校验结果。"""
        try:
            res = res or {}
            answer = res.get('answer') or '（无回答）'
            src_txt = {'llm': '本地大模型（已接地）',
                       'knowledge_base': '知识库（大模型不可用，原文兜底）',
                       'refused': '拒答（知识库未涵盖）',
                       'error': '错误'}.get(res.get('source', ''), res.get('source', ''))

            guard = res.get('guard') or {}
            tips = []
            if guard.get('top_score') is not None:
                tips.append(f"召回分 {guard.get('top_score')}")
            if guard.get('unsupported'):
                tips.append('已标注可疑药名：' + '、'.join(guard['unsupported']))
            if guard.get('dosage_unsupported'):
                tips.append('已标注可疑剂量')
            if res.get('elapsed') is not None:
                tips.append(f"耗时 {res.get('elapsed')}s")

            self.textEdit_advice.append(f'AI（{src_txt}）：\n{answer}')
            cites = res.get('citations') or []
            if cites:
                self.textEdit_advice.append(
                    '依据：' + '、'.join(c.get('title', c.get('id', '')) for c in cites))
            if tips:
                self.textEdit_advice.append('［' + ' | '.join(tips) + '］')

            q = question if question is not None else getattr(self, '_last_chat_question', '')
            if q:
                self.chat_history.append({'role': 'user', 'content': q})
            self.chat_history.append({'role': 'assistant', 'content': answer})
            self.chat_history = self.chat_history[-12:]     # 只留最近若干轮
        except Exception:
            traceback.print_exc()


# ==================== 程序入口 ====================
if __name__ == "__main__":
    # 配置已在文件顶部统一读入全局变量（含 CUDA 不可用时的 CPU 回退）。
    # 这里只做"依赖配置的运行时准备"：权重检查、模型加载预热。
    # 注意：别再把 UI/相机等配置在这里重读一遍 —— 那样 import 本模块时
    # MyMainWindow 会因为拿不到这些全局变量而 NameError（历史 bug，已修）；
    # 也别在这里写 `global yolo`，那会和模块级赋值冲突（SyntaxError）。
    if not os.path.exists(weights_path):
        print(f"警告: 权重文件 {weights_path} 不存在")
        # 兜底权重 yolov8s.pt（COCO 预训练）已在 2026-10-08 清理时删除。
        # 需要它就从 ultralytics 重新下载放到项目根目录；更推荐直接改
        # config/configs.yaml 的 MODEL.WEIGHT 指回 runs/train/pest27_final4/weights/best.pt
        print("      请修改 config/configs.yaml 的 MODEL.WEIGHT 指向现有权重")
    yolo = None
    try:
        yolo = YOLO(weights_path)
        # 模型预热
        yolo.predict(np.zeros((300, 300, 3), dtype='uint8'), device=device)
    except Exception:
        traceback.print_exc()
        QMessageBox.critical(None, '模型加载失败',
                             f'无法加载权重：{weights_path}\n请检查 config/configs.yaml 的 MODEL.WEIGHT。')
        sys.exit(1)

    # 创建QApplication实例
    app = QApplication([])
    # 创建自定义的主窗口对象
    window = MyMainWindow(config)
    # 显示窗口
    window.show()
    # 运行应用程序
    app.exec_()
