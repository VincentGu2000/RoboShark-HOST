import os
import sys
import struct
import datetime
from PyQt5 import QtCore,QtGui,QtWidgets


class GimbalControlBtnWin(QtWidgets.QWidget):
    _signal = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super(GimbalControlBtnWin, self).__init__(parent)
        self.init_ui()

 
    def init_ui(self):

        # 窗口设置
        self.setFixedSize(380, 220)  # 设置窗体大小
        self.setWindowTitle('云台控制')  # 设置窗口标题

        # 布局
        self.main_layout = QtWidgets.QGridLayout()
        self.setLayout(self.main_layout)

        # 布局
        self.gimbal_status_layout = QtWidgets.QGridLayout()
        self.gimbal_status_frame = QtWidgets.QFrame()
        self.main_layout.addWidget(self.gimbal_status_frame, 1,0,1,1, QtCore.Qt.AlignCenter)
        self.gimbal_status_frame.setLayout(self.gimbal_status_layout)
        self.gimbal_status_frame.setFrameShape(QtWidgets.QFrame.Box)
        self.gimbal_status_frame.setFrameShadow(QtWidgets.QFrame.Raised)
        self.gimbal_status_frame.setLineWidth(1)
        self.gimbal_status_frame.setFixedSize(320, 50)

        self.gimbal_control_layout = QtWidgets.QGridLayout()
        self.gimbal_control_frame = QtWidgets.QFrame()
        self.main_layout.addWidget(self.gimbal_control_frame, 3,0,1,1, QtCore.Qt.AlignCenter)
        self.gimbal_control_frame.setLayout(self.gimbal_control_layout)
        self.gimbal_control_frame.setFrameShape(QtWidgets.QFrame.Box)
        self.gimbal_control_frame.setFrameShadow(QtWidgets.QFrame.Raised)
        self.gimbal_control_frame.setLineWidth(1)
        self.gimbal_control_frame.setFixedSize(320, 50)

        # 创建对象
        pal = QtGui.QPalette()
        ## 吸附状态面板
        self.gimbal_status_fixed_label = QtWidgets.QLabel('云台状态')
        self.gimbal_status_fixed_label.setFont(QtGui.QFont('Microsoft YaHei', 14, QtGui.QFont.Bold))
        self.main_layout.addWidget(self.gimbal_status_fixed_label, 0, 0, 1, 1, QtCore.Qt.AlignLeft)
        pal.setColor(QtGui.QPalette.WindowText, QtCore.Qt.red)
        self.gimbal_status_fixed_label.setPalette(pal)

        self.gimbalstate_fixed_label = QtWidgets.QLabel('运行状态')
        self.gimbalstate_fixed_label.setFont(QtGui.QFont('SimSun', 12, QtGui.QFont.Bold))
        self.gimbal_status_layout.addWidget(self.gimbalstate_fixed_label, 0, 0, 1, 1, QtCore.Qt.AlignCenter)

        self.gimbalstate_label = QtWidgets.QLabel('未开启')
        self.gimbalstate_label.setFont(QtGui.QFont('SimSun', 12))
        self.gimbal_status_layout.addWidget(self.gimbalstate_label, 0, 1, 1, 1, QtCore.Qt.AlignCenter)


        self.gimbal_control_fixed_label = QtWidgets.QLabel('云台控制')
        self.gimbal_control_fixed_label.setFont(QtGui.QFont('Microsoft YaHei', 14, QtGui.QFont.Bold))
        self.main_layout.addWidget(self.gimbal_control_fixed_label, 2, 0, 1, 1, QtCore.Qt.AlignLeft)
        pal.setColor(QtGui.QPalette.WindowText, QtCore.Qt.blue)
        self.gimbal_control_fixed_label.setPalette(pal)
        
        self.gimbalcc_start_button = QtWidgets.QPushButton('启动')
        self.gimbal_control_layout.addWidget(self.gimbalcc_start_button, 0, 0, 1, 1, QtCore.Qt.AlignCenter)
        self.gimbalcc_start_button.setObjectName("SET_GIMBAL_RUN")

        self.gimbalcc_stop_button = QtWidgets.QPushButton('停止')
        self.gimbal_control_layout.addWidget(self.gimbalcc_stop_button, 0, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.gimbalcc_stop_button.setObjectName("SET_GIMBAL_STOP")

        self.gimbalcc_zero_button = QtWidgets.QPushButton('归中')
        self.gimbal_control_layout.addWidget(self.gimbalcc_zero_button, 0, 2, 1, 1, QtCore.Qt.AlignCenter)
        self.gimbalcc_zero_button.setObjectName("SET_GIMBAL_ZERO")


    def handle_click(self):
        if not self.isVisible():
            self.show()


    def handle_close(self):
        self.close()
