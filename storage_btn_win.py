import os
import sys
import struct
import datetime
from PyQt5 import QtCore,QtGui,QtWidgets

class StorageBtnWin(QtWidgets.QWidget):
    _signal = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super(StorageBtnWin, self).__init__(parent)
        self.filename = None # 数据文件的名字
        self.init_ui()

    def init_ui(self):
        # 窗口设置
        self.setFixedSize(480, 80)  # 设置窗体大小
        self.setWindowTitle('储存数据——设置文件名')  # 设置窗口标题

        # 控件初始化
        self.filename_label = QtWidgets.QLabel('文件名：')
        self.filename_editor = QtWidgets.QLineEdit()
        self.save_button = QtWidgets.QPushButton('储存')

        # 布局
        self.main_layout = QtWidgets.QGridLayout()
        self.setLayout(self.main_layout)
        self.main_layout.addWidget(self.filename_label, 0, 0, 1, 2, QtCore.Qt.AlignLeft)
        self.main_layout.addWidget(self.filename_editor, 1, 0, 1, 2)
        self.main_layout.addWidget(self.save_button, 1, 2, 1, 1)

        # 链接控件
        self.save_button.clicked.connect(self.save_data)

        # 给定预设
        now_time = datetime.datetime.now()
        filename = str(now_time.year-2000).zfill(2) + str(now_time.month).zfill(2) + str(now_time.day).zfill(2) + str(now_time.hour).zfill(2) + str(now_time.minute).zfill(2) + '.bin'
        self.filename_editor.setText(filename)

    def save_data(self):
        filename = self.filename_editor.text()
        if(len(filename)==0 or len(filename)>16):
            self.filename_editor.setText('字符串需大于0，小于16位英文字符，需以.bin结尾')
            return
        idx = filename.find('.bin')
        a = len(filename)
        if(idx == -1 or idx != len(filename)-4):
            self.filename_editor.setText('字符串需大于0，小于16位英文字符，需以.bin结尾')
            return
        self._signal.emit(filename)
        self.close()

    def handle_click(self):
        if not self.isVisible():
            self.show()


    def handle_close(self):
        self.close()