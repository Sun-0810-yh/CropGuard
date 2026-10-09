import traceback
from datetime import datetime
import time
import os
import sys
import shutil
import threading

# CRITICAL: torch must be imported before PyQt5 to avoid DLL conflicts
import cv2
import numpy as np
import torch

from ultralytics import YOLO

from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import QApplication, QMainWindow, QComboBox, QMessageBox, QFileDialog
from PyQt5 import QtCore, QtWidgets, QtGui
from PyQt5.QtGui import QColor, QPixmap

from UI import Ui_MainWindow

from tool.parser import get_config
from tool.tools import draw_info, result_info_format, format_data, writexls, writecsv, resize_with_padding, assess_risk
from prompts.core.prompt_manager import prompt_manager
from llm import local_llm, knowledge_base
from feedback.feedback import Feedback

import winsound


# ==================== 从配置文件加载全局变量 ====================
config = get_config('./config/configs.yaml')

# UI配置
title = config.get('UI', {}).get('title', '农作物虫害检测识别系统')
label_title = config.get('UI', {}).get('label_title', '农作物虫害检测识别系统')
background = config.get('UI', {}).get('background', './icon/background.jpg')
zhutu2 = config.get('UI', {}).get('zhutu2', './icon/zhutu2.png')

# 摄像头配置
camera_num = config.get('CONFIG', {}).get('camera_num', 0)
# 中文名称映射
chinese_name = config.get('CONFIG', {}).get('chinese_name', {})

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
AI_CONFIG = config.get('AI', {})
active_model = AI_CONFIG.get('active_model', 'openai')
ai_timeout = AI_CONFIG.get('timeout', 60)
ai_max_tokens = AI_CONFIG.get('max_tokens', 2000)
ai_temperature = AI_CONFIG.get('temperature', 0.7)

# 根据active_model获取当前模型配置
model_configs = AI_CONFIG.get('models', {})
current_model_config = model_configs.get(active_model, {})


# ==================== 摄像头 / 性能参数 ====================
# 摄像头采集与推理全部放到后台线程，Qt 主线程只负责显示，避免「跑一会就卡死」
CAMERA_WIDTH = 1280         # 摄像头采集宽度, 0=使用驱动默认
CAMERA_HEIGHT = 720         # 摄像头采集高度, 0=使用驱动默认
# 强制 MJPG：很多 USB 摄像头默认走 YUY2，640x480 下只有 5~10fps，切 MJPG 才能上 30fps
CAMERA_FOURCC = 'MJPG'      # 设为 None 则不强制
DISPLAY_INTERVAL_MS = 30    # 画面刷新定时器间隔(ms)，越小越跟手
MAX_TABLE_ROWS = 500        # 表格最大行数，防止长时间运行内存/绘制开销无限增长
CAMERA_LOG_INTERVAL = 1.0   # 摄像头/视频模式每隔多少秒才往表格记一行


# ==================== 本地建议工作线程 ====================
class LocalAdviceWorker(QThread):
    """本地大模型建议生成线程：优先本地 Ollama，失败静默由知识库兜底。"""
    success = pyqtSignal(str)

    def __init__(self, results, pest_text, risk_info, chinese_name):
        super().__init__()
        self.results = results
        self.pest_text = pest_text
        self.risk_info = risk_info
        self.chinese_name = chinese_name

    def run(self):
        try:
            system_prompt = prompt_manager.get_prompt('deepseek')
            user_prompt = (
                f"检测到的病虫害：{self.pest_text}{self.risk_info}\n\n"
                f"请生成简洁的诊断结论与综合防治要点（300字以内），"
                f"分点列出农业、物理、生物、化学防治措施。"
            )
            advice = local_llm.generate(system_prompt, user_prompt)
            self.success.emit(advice)
        except Exception:
            # 本地大模型不可用，静默保留知识库兜底版本
            pass


# ==================== 摄像头采集线程 ====================
class CameraCapture:
    """后台线程持续读取摄像头，只保留最新一帧。

    卡死根因：原来 cv2.VideoCapture.read() 跑在 Qt 主线程，推理期间没人读帧，
    摄像头驱动缓冲区被塞满旧帧，表现为「跑一会画面就卡死」。
    独立线程持续排空缓冲即可根治，同时主线程永远拿到最新帧、无累积延迟。
    """

    def __init__(self, index=0, width=0, height=0, fourcc=None):
        self.index = index
        self.width = width
        self.height = height
        self.fourcc = fourcc
        self._cap = None
        self._frame = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread = None
        self._fail = 0

    def _open_cap(self):
        cap = None
        if os.name == 'nt':
            # Windows 下 MSMF 后端长跑容易卡死(cap_msmf OnReadSample 报错后彻底读不到帧)，
            # 必须优先用 DirectShow
            cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if cap is None or not cap.isOpened():
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            cap = cv2.VideoCapture(self.index)
        if cap is not None and cap.isOpened():
            # 顺序要紧：先定 FOURCC(MJPG)，再定分辨率，否则可能拿到不支持的组合
            if self.fourcc:
                try:
                    cap.set(cv2.CAP_PROP_FOURCC,
                            cv2.VideoWriter_fourcc(*self.fourcc))
                except Exception:
                    pass
            if self.width:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            if self.height:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # 只留 1 帧缓冲，避免积压
            except Exception:
                pass
        return cap

    def start(self):
        self._cap = self._open_cap()
        if self._cap is None or not self._cap.isOpened():
            return False
        self._thread = threading.Thread(target=self._loop, name='CameraCapture', daemon=True)
        self._thread.start()
        return True

    def _loop(self):
        while not self._stopped.is_set():
            cap = self._cap
            if cap is None or not cap.isOpened():
                time.sleep(0.1)
                continue
            try:
                ok, frame = cap.read()
            except Exception:
                ok, frame = False, None
            if not ok or frame is None:
                self._fail += 1
                time.sleep(0.01)
                # 连续读不到（约 1 秒）就重开摄像头，从断流中自愈
                if self._fail >= 60:
                    self._fail = 0
                    self._reopen()
                continue
            self._fail = 0
            with self._lock:
                self._frame = frame

    def _reopen(self):
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = self._open_cap()

    def read(self):
        """非阻塞返回 (ok, 最新一帧)。"""
        with self._lock:
            return (self._frame is not None), self._frame

    def stop(self):
        self._stopped.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = None
        with self._lock:
            self._frame = None


# ==================== 后台推理线程 ====================
class InferenceWorker(QThread):
    """在后台线程执行 YOLO 推理，Qt 主线程只刷新界面。

    单槽位 + 自动丢帧：推理跟不上采集时旧帧直接丢弃，永远只处理最新一帧，
    所以画面延迟不会随时间累积，界面也不会被推理阻塞。
    """
    result_ready = pyqtSignal(object, object, object, object, object)

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
            self.result_ready.emit(results, result_info, consum, input_time, img_name)

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

        self.number = 1
        self.RowLength = 0
        self.consum_time = 0
        self.input_time = 0

        # 打开图片
        self.pushButton_img.clicked.connect(self.open_img)

        # 异步建议线程w
        self.advice_thread = None
        # 打开文件夹
        self.pushButton_dir.clicked.connect(self.open_dir)
        # 打开视频
        self.pushButton_video.clicked.connect(self.open_video)
        # 打开摄像头
        self.pushButton_camera.clicked.connect(self.open_camera)
        # 绑定开始运行
        self.pushButton_start.clicked.connect(self.start)
        # 导出数据
        self.pushButton_export.clicked.connect(self.write_files)

        # 保存结果
        self.pushButton_save.clicked.connect(self.save_current_result)

        # 获取防治建议
        self.pushButton_advice.clicked.connect(self.get_advice)

        self.comboBox.activated.connect(self.onComboBoxActivated)
        self.comboBox.mousePressEvent = self.handle_mouse_press

        # 表格点击事件绑定
        self.tableWidget_info.cellClicked.connect(self.cell_clicked)

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_frame)

        # 摄像头采集线程 / 后台推理线程
        self.camera = None
        self.worker = None
        self.awaiting_result = False
        self._last_camera_log = 0.0

        self.image_files = []
        self.current_index = 0
        self.is_running = False
        self.sign = False

        # 信号反馈：语音播报 + 可选 Arduino 声光报警（离线）
        _fb = config.get('FEEDBACK', {}) if config else {}
        self.feedback = Feedback(
            serial_port=_fb.get('serial_port') or None,
            baud=int(_fb.get('baud', 9600)),
            enabled=bool(_fb.get('enabled', True)),
            speak_interval=float(_fb.get('speak_interval', 3)),
        )

    def init_UI_config(self):
        """
        根据config.yaml中的配置，更新界面
        """
        self.setWindowTitle(title)
        self.label_title.setText(label_title)
        self.setStyleSheet("#centralwidget {background-image: url('%s')}" % background_img)
        self.label_img.setPixmap(QtGui.QPixmap(zhutu2))
        self.label_control.setStyleSheet("background-color: rgba(%s); border-radius: 15px;" % label_control_color)
        self.label_img.setStyleSheet("background-color: rgba(%s); border-radius: 15px;" % label_img_color)
        # 设置 tableWidget 表头的样式
        header_style_sheet = """
                    QHeaderView::section {{
                        background-color: rgb({header_background_color});
                        color: {header_color};
                    }}
                    """.format(
            header_background_color=header_background_color,
            header_color=header_color
        )
        self.tableWidget_info.horizontalHeader().setStyleSheet(header_style_sheet)

    def cell_clicked(self, row, column):
        """
        列表 单元格点击事件
        """
        self.update_comboBox_default()

        result_info = {}
        if self.tableWidget_info.item(row, 1) is None:
            return

        self.img_path = self.tableWidget_info.item(row, 1).text()
        try:
            self.results = eval(self.tableWidget_info.item(row, 3).text())
        except Exception:
            self.results = []
        self.result_img_name = self.tableWidget_info.item(row, 6).text()

        try:
            if self.result_img_name and os.path.exists(self.result_img_name):
                self.img_show = cv2.imdecode(np.fromfile(self.result_img_name, dtype=np.uint8), -1)
            else:
                src_img = cv2.imdecode(np.fromfile(self.img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
                self.img_show = draw_info(src_img, self.results)
        except Exception:
            self.img_show = None

        if len(self.results) > 0:
            box = self.results[0][2]
            score = self.results[0][1]
            cls_name = self.results[0][0]
            result_info = result_info_format({}, box, score, cls_name)
            self.show_info(result_info)

        if self.img_show is not None:
            self._display_image(self.img_show)

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

        self.img = cv2.imdecode(np.fromfile(self.img_path, dtype=np.uint8), cv2.IMREAD_COLOR)

        self.img_show = draw_info(self.img, lst_info)
        self.show_all(self.img_show, result_info)

    def _display_image(self, img):
        """在label_img上显示图像"""
        if img is None:
            return
        # 调整图像大小以适应label
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

    def open_img(self):
        """打开单张图片"""
        try:
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
        try:
            self.update_comboBox_default()
            text = self.label_camera_path.text()
            if text in (' 打开摄像头', ' 摄像头已关闭'):
                self.start_type = 'camera'
                self.label_img_path.setText(" 选择图片文件")
                self.label_dir_path.setText(" 选择图片文件夹")
                self.label_video_path.setText(" 选择视频文件")
                self.label_camera_path.setText(" 摄像头已打开")

                self.video_name = camera_num

                # 先释放上一个采集线程，避免设备被重复占用导致打不开/卡死
                if self.camera is not None:
                    self.camera.stop()
                    self.camera = None

                self.camera = CameraCapture(camera_num, CAMERA_WIDTH, CAMERA_HEIGHT,
                                            CAMERA_FOURCC)
                if not self.camera.start():
                    self.camera = None
                    self.start_type = None
                    self.label_camera_path.setText(" 打开摄像头")
                    QMessageBox.warning(
                        self, "错误",
                        f"无法打开摄像头 {camera_num}，请检查设备是否被占用，"
                        f"或修改 config/configs.yaml 中的 camera_num")
                    return

                # 等第一帧（最长约 1 秒）
                ok, frame = self.camera.read()
                for _ in range(20):
                    if ok:
                        break
                    time.sleep(0.05)
                    ok, frame = self.camera.read()
                if ok:
                    self.img = frame
                    self.img_name = "camera_frame.jpg"
                    self.show_frame(frame)
            elif text == ' 摄像头已打开':
                self.pushButton_start.setText("开始运行 >")
                self.label_camera_path.setText(" 摄像头已关闭")
                self.start_type = None
                self.is_running = False
                self.awaiting_result = False
                self.timer.stop()
                # 关闭摄像头时必须 release，否则设备一直被占用
                if self.camera is not None:
                    self.camera.stop()
                    self.camera = None
        except Exception as e:
            traceback.print_exc()

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
            # ---------- 摄像头：画面永远用最新原始帧渲染，推理在后台跑 ----------
            if self.start_type == 'camera':
                if self.camera is None:
                    return
                ok, frame = self.camera.read()
                if not ok or frame is None:
                    return

                # 投递最新一帧给推理线程（线程内部自动丢帧，不会积压）
                if self.is_running and self.worker is not None:
                    self.worker.submit(frame, 'camera_frame.jpg')

                # 用「最新原始帧 + 上一次检测框」渲染，预览流畅且不会闪
                display = frame.copy()
                if self.results:
                    draw_info(display, self.results)
                self.img = frame
                self.img_show = display
                self.img_name = 'camera_frame.jpg'
                self.show_frame(display)
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

    def _allow_table_log(self):
        """摄像头/视频模式限制写入表格的频率，避免表格无限膨胀拖垮界面。"""
        if self.start_type in ('camera', 'video'):
            now = time.time()
            if now - self._last_camera_log < CAMERA_LOG_INTERVAL:
                return False
            self._last_camera_log = now
        return True

    def apply_results(self, results, result_info, consum_time, input_time, img_name):
        """在 Qt 主线程中根据推理结果刷新界面。"""
        self.results = results
        self.consum_time = consum_time
        self.input_time = input_time
        if img_name:
            self.img_name = img_name

        self.get_comboBox_value(self.results)
        self.show_info(result_info)

        if self._allow_table_log():
            self.show_table()

        # 触发信号反馈：语音播报 + 声光报警
        _names = []
        for _r in self.results:
            _n = _r[0]
            _names.append(str(self.chinese_name.get(_n, _n)) if isinstance(self.chinese_name, dict) else str(_n))
        if hasattr(self, 'feedback'):
            self.feedback.trigger(result_info.get('risk_level', '无风险'), _names,
                                  result_info.get('risk_detail', ''))

    def on_detect_result(self, results, result_info, consum_time, input_time, img_name):
        """后台推理线程回调（在 Qt 主线程执行）。"""
        self.awaiting_result = False
        self.apply_results(results, result_info, consum_time, input_time, img_name)

        # 图片 / 文件夹 / 视频模式：结果图直接显示
        if self.start_type in ('dir', 'video', 'img'):
            if self.img is not None:
                self.img_show = draw_info(self.img.copy(), results)
                self.show_frame(self.img_show)
            if self.start_type == 'img':
                self.is_running = False
                self.pushButton_start.setText('开始运行 >')

    def closeEvent(self, event):
        """窗口关闭时释放摄像头、推理线程与串口，避免设备被一直占用。"""
        try:
            self.timer.stop()
            if self.camera is not None:
                self.camera.stop()
                self.camera = None
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
            self.advice_thread = LocalAdviceWorker(
                self.results, pest_text, risk_info, self.chinese_name)
            self.advice_thread.success.connect(self.on_advice_success)
            self.advice_thread.start()

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
            if len(result) == 0:
                print("未识别到目标")
                return
            cls_name = result['cls_name']
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

    def show_table(self):
        try:
            self.number += 1
            self.RowLength = self.RowLength + 1
            self.tableWidget_info.setRowCount(self.RowLength)
            row = self.RowLength - 1
            for column, content in enumerate(
                    [self.number, self.img_path, self.input_time, self.results,
                     len(self.results), self.consum_time, "待保存"]):
                item = QtWidgets.QTableWidgetItem(str(content))
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                item.setForeground(QColor.fromRgb(column_color[0], column_color[1], column_color[2]))
                self.tableWidget_info.setItem(row, column, item)
            # 限制最大行数：摄像头/视频长时间运行不会让表格无限膨胀拖垮界面
            if self.RowLength > MAX_TABLE_ROWS:
                self.tableWidget_info.removeRow(0)
                self.RowLength -= 1
            self.tableWidget_info.scrollToBottom()
        except Exception as e:
            traceback.print_exc()

    def write_files(self):
        """
        导出 excel、csv 数据
        """
        try:
            if self.RowLength == 0:
                QMessageBox.information(self, "信息", "没有数据可导出！", QMessageBox.Yes)
                return

            # 从表格收集所有数据
            all_data = []
            headers = ["编号", "图片路径", "输入时间", "识别结果", "目标数量", "耗时", "保存状态"]
            all_data.append(headers)

            for row in range(self.RowLength):
                row_data = []
                for col in range(7):
                    item = self.tableWidget_info.item(row, col)
                    row_data.append(item.text() if item else "")
                all_data.append(row_data)

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

    def show_model_info(self):
        """显示当前模型信息"""
        if not current_model_config:
            self.textEdit_advice.setText("❌ 未配置AI模型！")
            return

        current_model_display = AI_MODEL_DISPLAY_NAMES.get(active_model, active_model)

        info = f"""当前AI模型配置

模型名称: {current_model_display}
API地址: {current_model_config.get('api_base', 'N/A')}
模型: {current_model_config.get('model', 'N/A')}
SSL验证: {current_model_config.get('verify_ssl', True)}
超时时间: {ai_timeout}秒
最大Token: {ai_max_tokens}
温度: {ai_temperature}

要切换模型，请修改 config/configs.yaml 中的 AI 配置：
- active_model 可选值: deepseek, qwen, openai, zhipu, qianfan, doubao, custom
- 在 models 下配置相应的 api_key 和参数
- 重启程序即可生效"""
        self.textEdit_advice.setText(info)


# ==================== 程序入口 ====================
if __name__ == "__main__":
    path_cfg = 'config/configs.yaml'
    cfg = get_config()
    cfg.merge_from_file(path_cfg)

    # 加载模型相关的参数配置
    cfg_model = cfg.MODEL
    weights = cfg_model.WEIGHT
    conf_thres = float(cfg_model.CONF)
    classes = eval(cfg_model.CLASSES)
    imgsz = int(cfg_model.IMGSIZE)
    device = cfg_model.DEVICE
    if str(device).startswith('cuda') and not torch.cuda.is_available():
        print("警告: CUDA 不可用，自动回退到 CPU 推理")
        device = 'cpu'

    # 加载UI界面相关的配置
    cfg_UI = cfg.UI
    background_img = cfg_UI.background
    padvalue = cfg_UI.padvalue
    column_widths = cfg_UI.column_widths
    column_color = cfg_UI.column_color
    title = cfg_UI.title
    label_title = cfg_UI.label_title
    zhutu2 = cfg_UI.zhutu2
    label_info_txt = cfg_UI.label_info_txt
    label_info_color = cfg_UI.label_info_color
    start_button_bg = cfg_UI.start_button_bg
    start_button_font = cfg_UI.start_button_font
    export_button_bg = cfg_UI.export_button_bg
    export_button_font = cfg_UI.export_button_font
    label_control_color = cfg_UI.label_control_color
    label_img_color = cfg_UI.label_img_color
    header_background_color = cfg_UI.table_widget_info_styles.header_background_color
    header_color = cfg_UI.table_widget_info_styles.header_color
    item_hover_background_color = cfg_UI.table_widget_info_styles.item_hover_background_color

    # 加载通用配置
    camera_num = int(cfg.CONFIG.camera_num)
    chinese_name = cfg.CONFIG.chinese_name

    # 模型加载
    if not os.path.exists(weights):
        print(f"警告: 权重文件 {weights} 不存在")
        # 兜底权重 yolov8s.pt（COCO 预训练）已在 2026-10-08 清理时删除。
        # 需要它就从 ultralytics 重新下载放到项目根目录；更推荐直接改
        # config/configs.yaml 的 MODEL.WEIGHT 指回 runs/train/pest27_final4/weights/best.pt
        weights = 'yolov8s.pt'
        print(f"      兜底为 {weights}（该文件当前不在项目里，ultralytics 会尝试联网下载）")
    yolo = YOLO(weights)
    # 模型预热
    yolo.predict(np.zeros((300, 300, 3), dtype='uint8'), device=device)

    # 创建QApplication实例
    app = QApplication([])
    # 创建自定义的主窗口对象
    window = MyMainWindow(cfg)
    # 显示窗口
    window.show()
    # 运行应用程序
    app.exec_()
