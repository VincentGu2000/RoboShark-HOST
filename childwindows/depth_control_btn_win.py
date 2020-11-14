import os
import sys
from PyQt5 import QtCore,QtGui,QtWidgets

### 子界面类
class DepthControlBtnWin(QtWidgets.QWidget):
    _signal = QtCore.pyqtSignal(str)

    ## 初始化函数
    def __init__(self, parent=None):
        super(DepthControlBtnWin, self).__init__(parent)
        self.init_ui()

    ## 初始化串口UI界面
    def init_ui(self):

        # 窗口设置
        self.setFixedSize(380, 220)  # 设置窗体大小
        self.setWindowTitle('深度控制')  # 设置窗口标题

        # 布局
        self.main_layout = QtWidgets.QGridLayout()
        self.setLayout(self.main_layout)
        
        # 定义UI界面，这里由用户自己定义
        self.depthctl_start_button = QtWidgets.QPushButton('开启控制')
        self.main_layout.addWidget(self.depthctl_start_button, 0, 0, 1, 1, QtCore.Qt.AlignCenter)
        # 这里需要给按钮设置一个名字，因为之后需要将这个按钮与一个函数链接起来，那个函数会根据这个名字给下位机发送指令
        # 所以这个按钮的名字，应该作为一条指令添加到RFLink的Command中
        self.depthctl_start_button.setObjectName("SET_DEPTHCTL_START") 

        self.depthctl_stop_button = QtWidgets.QPushButton('关闭控制')
        self.main_layout.addWidget(self.depthctl_stop_button, 0, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.depthctl_stop_button.setObjectName("SET_DEPTHCTL_STOP")

        self.depthctl_param_kp_label = QtWidgets.QLabel('Kp')
        self.depthctl_param_kp_label.setFont(QtGui.QFont('Arial', 12))
        self.main_layout.addWidget(self.depthctl_param_kp_label, 1, 0, 1, 1, QtCore.Qt.AlignCenter)

        self.depthctl_param_kp_edit = QtWidgets.QLineEdit()
        self.main_layout.addWidget(self.depthctl_param_kp_edit, 1, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.depthctl_param_kp_edit.setText('0.0')

        self.depthctl_param_ki_label = QtWidgets.QLabel('Ki')
        self.depthctl_param_ki_label.setFont(QtGui.QFont('Arial', 12))
        self.main_layout.addWidget(self.depthctl_param_ki_label, 2, 0, 1, 1, QtCore.Qt.AlignCenter)

        self.depthctl_param_ki_edit = QtWidgets.QLineEdit()
        self.main_layout.addWidget(self.depthctl_param_ki_edit, 2, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.depthctl_param_ki_edit.setText('0.0')

        self.depthctl_param_kd_label = QtWidgets.QLabel('Kd')
        self.depthctl_param_kd_label.setFont(QtGui.QFont('Arial', 12))
        self.main_layout.addWidget(self.depthctl_param_kd_label, 3, 0, 1, 1, QtCore.Qt.AlignCenter)

        self.depthctl_param_kd_edit = QtWidgets.QLineEdit()
        self.main_layout.addWidget(self.depthctl_param_kd_edit, 3, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.depthctl_param_kd_edit.setText('0.0')

        self.depthctl_writeparam_button = QtWidgets.QPushButton('写入参数')
        self.main_layout.addWidget(self.depthctl_writeparam_button, 4, 0, 1, 2, QtCore.Qt.AlignCenter)
        self.depthctl_writeparam_button.setObjectName("SET_DEPTHCTL_PARAM")

    def handle_click(self):
        if not self.isVisible():
            self.show()


    def handle_close(self):
        self.close()
