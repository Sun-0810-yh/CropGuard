# -*- coding: utf-8 -*-
"""
农作物害虫智能检测识别与大模型防控决策系统 - UI界面
由 Qt Designer 生成，手工转换为 Python 代码
"""

from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(1280, 820)
        MainWindow.setMinimumSize(1024, 700)

        # 中央控件
        self.centralwidget = QtWidgets.QWidget(MainWindow)
        self.centralwidget.setObjectName("centralwidget")

        # ==================== 顶部标题 ====================
        self.label_title = QtWidgets.QLabel(self.centralwidget)
        self.label_title.setGeometry(QtCore.QRect(10, 5, 1260, 36))
        font_title = QtGui.QFont()
        font_title.setFamily("Arial")
        font_title.setPointSize(16)
        font_title.setBold(True)
        self.label_title.setFont(font_title)
        self.label_title.setAlignment(QtCore.Qt.AlignCenter)
        self.label_title.setObjectName("label_title")

        # ==================== 左侧控制面板区域 ====================
        self.label_control = QtWidgets.QLabel(self.centralwidget)
        self.label_control.setGeometry(QtCore.QRect(10, 50, 280, 730))
        self.label_control.setObjectName("label_control")

        # ---- 源选择按钮区域 ----
        # 打开图片按钮
        self.pushButton_img = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_img.setGeometry(QtCore.QRect(30, 70, 110, 36))
        font_btn = QtGui.QFont()
        font_btn.setFamily("Arial")
        font_btn.setPointSize(10)
        self.pushButton_img.setFont(font_btn)
        self.pushButton_img.setText("打开图片")
        self.pushButton_img.setStyleSheet("background-color: rgb(48,77,49); color: rgb(255,255,255); border-radius: 10px;")
        self.pushButton_img.setObjectName("pushButton_img")

        # 打开文件夹按钮
        self.pushButton_dir = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_dir.setGeometry(QtCore.QRect(160, 70, 110, 36))
        self.pushButton_dir.setFont(font_btn)
        self.pushButton_dir.setText("打开文件夹")
        self.pushButton_dir.setStyleSheet("background-color: rgb(48,77,49); color: rgb(255,255,255); border-radius: 10px;")
        self.pushButton_dir.setObjectName("pushButton_dir")

        # 打开视频按钮
        self.pushButton_video = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_video.setGeometry(QtCore.QRect(30, 120, 110, 36))
        self.pushButton_video.setFont(font_btn)
        self.pushButton_video.setText("打开视频")
        self.pushButton_video.setStyleSheet("background-color: rgb(48,77,49); color: rgb(255,255,255); border-radius: 10px;")
        self.pushButton_video.setObjectName("pushButton_video")

        # 打开摄像头按钮
        self.pushButton_camera = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_camera.setGeometry(QtCore.QRect(160, 120, 110, 36))
        self.pushButton_camera.setFont(font_btn)
        self.pushButton_camera.setText("打开摄像头")
        self.pushButton_camera.setStyleSheet("background-color: rgb(48,77,49); color: rgb(255,255,255); border-radius: 10px;")
        self.pushButton_camera.setObjectName("pushButton_camera")

        # 开始运行按钮
        self.pushButton_start = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_start.setGeometry(QtCore.QRect(30, 180, 240, 40))
        font_start = QtGui.QFont()
        font_start.setFamily("Arial")
        font_start.setPointSize(12)
        font_start.setBold(True)
        self.pushButton_start.setFont(font_start)
        self.pushButton_start.setText("开始运行 >")
        self.pushButton_start.setStyleSheet("background-color: rgb(48,77,49); color: rgb(255,255,255); border-radius: 15px;")
        self.pushButton_start.setObjectName("pushButton_start")

        # ---- 路径显示区域 ----
        font_path = QtGui.QFont()
        font_path.setFamily("Arial")
        font_path.setPointSize(8)

        self.label_img_path = QtWidgets.QLabel(self.centralwidget)
        self.label_img_path.setGeometry(QtCore.QRect(30, 240, 240, 20))
        self.label_img_path.setFont(font_path)
        self.label_img_path.setText(" 选择图片文件")
        self.label_img_path.setStyleSheet("color: rgb(60,60,60);")
        self.label_img_path.setObjectName("label_img_path")

        self.label_dir_path = QtWidgets.QLabel(self.centralwidget)
        self.label_dir_path.setGeometry(QtCore.QRect(30, 265, 240, 20))
        self.label_dir_path.setFont(font_path)
        self.label_dir_path.setText(" 选择图片文件夹")
        self.label_dir_path.setStyleSheet("color: rgb(60,60,60);")
        self.label_dir_path.setObjectName("label_dir_path")

        self.label_video_path = QtWidgets.QLabel(self.centralwidget)
        self.label_video_path.setGeometry(QtCore.QRect(30, 290, 240, 20))
        self.label_video_path.setFont(font_path)
        self.label_video_path.setText(" 选择视频文件")
        self.label_video_path.setStyleSheet("color: rgb(60,60,60);")
        self.label_video_path.setObjectName("label_video_path")

        self.label_camera_path = QtWidgets.QLabel(self.centralwidget)
        self.label_camera_path.setGeometry(QtCore.QRect(30, 315, 240, 20))
        self.label_camera_path.setFont(font_path)
        self.label_camera_path.setText(" 打开摄像头")
        self.label_camera_path.setStyleSheet("color: rgb(60,60,60);")
        self.label_camera_path.setObjectName("label_camera_path")

        # ---- 目标选择器 ----
        self.comboBox = QtWidgets.QComboBox(self.centralwidget)
        self.comboBox.setGeometry(QtCore.QRect(30, 350, 240, 28))
        font_combo = QtGui.QFont()
        font_combo.setFamily("Arial")
        font_combo.setPointSize(10)
        self.comboBox.setFont(font_combo)
        self.comboBox.setObjectName("comboBox")
        self.comboBox.addItems(["所有目标"])

        # ---- 检测信息显示 ----
        font_info = QtGui.QFont()
        font_info.setFamily("Arial")
        font_info.setPointSize(9)

        y_info = 400

        # 类别
        label_cls_title = QtWidgets.QLabel(self.centralwidget)
        label_cls_title.setGeometry(QtCore.QRect(30, y_info, 60, 24))
        label_cls_title.setFont(font_info)
        label_cls_title.setText("类别:")
        label_cls_title.setStyleSheet("font-weight: bold;")

        self.label_class = QtWidgets.QLabel(self.centralwidget)
        self.label_class.setGeometry(QtCore.QRect(90, y_info, 170, 24))
        self.label_class.setFont(font_info)
        self.label_class.setText("--")
        self.label_class.setObjectName("label_class")

        y_info += 30
        # 置信度
        label_score_title = QtWidgets.QLabel(self.centralwidget)
        label_score_title.setGeometry(QtCore.QRect(30, y_info, 60, 24))
        label_score_title.setFont(font_info)
        label_score_title.setText("置信度:")
        label_score_title.setStyleSheet("font-weight: bold;")

        self.label_score = QtWidgets.QLabel(self.centralwidget)
        self.label_score.setGeometry(QtCore.QRect(90, y_info, 170, 24))
        self.label_score.setFont(font_info)
        self.label_score.setText("--")
        self.label_score.setObjectName("label_score")

        y_info += 30
        # 风险等级
        label_risk_title = QtWidgets.QLabel(self.centralwidget)
        label_risk_title.setGeometry(QtCore.QRect(30, y_info, 60, 24))
        label_risk_title.setFont(font_info)
        label_risk_title.setText("风险等级:")
        label_risk_title.setStyleSheet("font-weight: bold;")

        self.label_risk = QtWidgets.QLabel(self.centralwidget)
        self.label_risk.setGeometry(QtCore.QRect(90, y_info, 170, 24))
        font_risk = QtGui.QFont()
        font_risk.setFamily("Arial")
        font_risk.setPointSize(11)
        font_risk.setBold(True)
        self.label_risk.setFont(font_risk)
        self.label_risk.setText("--")
        self.label_risk.setObjectName("label_risk")

        y_info += 30
        # 边界框坐标
        label_coords_title = QtWidgets.QLabel(self.centralwidget)
        label_coords_title.setGeometry(QtCore.QRect(30, y_info, 60, 24))
        label_coords_title.setFont(font_info)
        label_coords_title.setText("坐标:")
        label_coords_title.setStyleSheet("font-weight: bold;")

        y_info += 25
        self.label_xmin_v = QtWidgets.QLabel(self.centralwidget)
        self.label_xmin_v.setGeometry(QtCore.QRect(30, y_info, 100, 20))
        self.label_xmin_v.setFont(font_info)
        self.label_xmin_v.setText("xmin: --")
        self.label_xmin_v.setObjectName("label_xmin_v")

        self.label_ymin_v = QtWidgets.QLabel(self.centralwidget)
        self.label_ymin_v.setGeometry(QtCore.QRect(140, y_info, 100, 20))
        self.label_ymin_v.setFont(font_info)
        self.label_ymin_v.setText("ymin: --")
        self.label_ymin_v.setObjectName("label_ymin_v")

        y_info += 22
        self.label_xmax_v = QtWidgets.QLabel(self.centralwidget)
        self.label_xmax_v.setGeometry(QtCore.QRect(30, y_info, 100, 20))
        self.label_xmax_v.setFont(font_info)
        self.label_xmax_v.setText("xmax: --")
        self.label_xmax_v.setObjectName("label_xmax_v")

        self.label_ymax_v = QtWidgets.QLabel(self.centralwidget)
        self.label_ymax_v.setGeometry(QtCore.QRect(140, y_info, 100, 20))
        self.label_ymax_v.setFont(font_info)
        self.label_ymax_v.setText("ymax: --")
        self.label_ymax_v.setObjectName("label_ymax_v")

        # ---- 操作按钮 ----
        y_btn = y_info + 40

        self.pushButton_save = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_save.setGeometry(QtCore.QRect(30, y_btn, 110, 36))
        self.pushButton_save.setFont(font_btn)
        self.pushButton_save.setText("保存结果")
        self.pushButton_save.setStyleSheet("background-color: rgb(48,77,49); color: rgb(255,255,255); border-radius: 10px;")
        self.pushButton_save.setObjectName("pushButton_save")

        self.pushButton_export = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_export.setGeometry(QtCore.QRect(160, y_btn, 110, 36))
        self.pushButton_export.setFont(font_btn)
        self.pushButton_export.setText("导出数据")
        self.pushButton_export.setStyleSheet("background-color: rgb(48,77,49); color: rgb(255,255,255); border-radius: 10px;")
        self.pushButton_export.setObjectName("pushButton_export")

        y_btn += 50
        self.pushButton_advice = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_advice.setGeometry(QtCore.QRect(30, y_btn, 240, 36))
        self.pushButton_advice.setFont(font_btn)
        self.pushButton_advice.setText("获取防治建议(AI)")
        self.pushButton_advice.setStyleSheet("background-color: rgb(48,77,49); color: rgb(255,255,255); border-radius: 10px;")
        self.pushButton_advice.setObjectName("pushButton_advice")

        # ---- AI建议文本框 ----
        y_text = y_btn + 50
        self.textEdit_advice = QtWidgets.QTextEdit(self.centralwidget)
        self.textEdit_advice.setGeometry(QtCore.QRect(30, y_text, 240, 130))
        font_advice = QtGui.QFont()
        font_advice.setFamily("Arial")
        font_advice.setPointSize(8)
        self.textEdit_advice.setFont(font_advice)
        self.textEdit_advice.setReadOnly(True)
        self.textEdit_advice.setPlaceholderText("AI防治建议将显示在这里...")
        self.textEdit_advice.setObjectName("textEdit_advice")

        # ==================== 右侧主图显示区域 ====================
        self.label_img = QtWidgets.QLabel(self.centralwidget)
        self.label_img.setGeometry(QtCore.QRect(300, 50, 660, 460))
        self.label_img.setAlignment(QtCore.Qt.AlignCenter)
        self.label_img.setScaledContents(False)
        self.label_img.setObjectName("label_img")

        # ==================== 底部结果表格 ====================
        self.tableWidget_info = QtWidgets.QTableWidget(self.centralwidget)
        self.tableWidget_info.setGeometry(QtCore.QRect(300, 520, 960, 260))
        self.tableWidget_info.setColumnCount(7)
        self.tableWidget_info.setHorizontalHeaderLabels([
            "编号", "图片路径", "输入时间", "识别结果", "目标数量", "耗时", "保存状态"
        ])
        # 设置列宽
        column_widths = [50, 220, 120, 200, 80, 80, 140]
        for i, w in enumerate(column_widths):
            self.tableWidget_info.setColumnWidth(i, w)
        self.tableWidget_info.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tableWidget_info.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tableWidget_info.setAlternatingRowColors(True)
        self.tableWidget_info.setObjectName("tableWidget_info")

        MainWindow.setCentralWidget(self.centralwidget)

        QtCore.QMetaObject.connectSlotsByName(MainWindow)
