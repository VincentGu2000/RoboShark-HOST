import os
import sys
import struct
from PyQt5 import QtCore,QtGui,QtWidgets

class AnalysisBtnWin(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super(AnalysisBtnWin, self).__init__(parent)
        self.filename = None # 数据文件的名字
        self.txtfilename = None # 待保存文件的名字
        self.init_ui()

    def init_ui(self):
        # 窗口设置
        self.setFixedSize(480, 200)  # 设置窗体大小
        self.setWindowTitle('解析数据')  # 设置窗口标题

        # 控件初始化
        self.open_file_button = QtWidgets.QPushButton('打开文件')
        self.file_name_label = QtWidgets.QLabel('未选择文件')
        self.file_name_label.setFont(QtGui.QFont('Microsoft YaHei', 16, QtGui.QFont.Bold))
        self.analy_begin_button = QtWidgets.QPushButton('开始分析')
        self.save_data_button = QtWidgets.QPushButton('保存数据')

        # 布局
        self.main_layout = QtWidgets.QGridLayout()
        self.setLayout(self.main_layout)
        self.main_layout.addWidget(self.file_name_label, 0, 0, 1, 1, QtCore.Qt.AlignCenter)
        self.main_layout.addWidget(self.open_file_button, 1, 0, 1, 1)
        self.main_layout.addWidget(self.analy_begin_button, 2, 0, 1, 1)
        self.main_layout.addWidget(self.save_data_button, 3, 0, 1, 1)

        # 链接控件
        self.open_file_button.clicked.connect(self.get_file)
        self.analy_begin_button.clicked.connect(self.analysis_data)
        self.save_data_button.clicked.connect(self.save_data)


    def get_file(self):
        """
        打开文件函数,与打开文件按钮链接
        只允许打开bin文件
        :param
        :return:
        """
        filepath,_ = QtWidgets.QFileDialog.getOpenFileName(self, '打开文件', "./data", "BIN (*.bin)")
        filepathstr = filepath.split('/')
        self.filename = filepathstr[-1]
        self.file_name_label.setText(self.filename + ' is opened.')
        self.binfile = open('data/'+self.filename,'rb')


    def analysis_data(self):
        """
        数据分析函数,与数据分析按钮链接
        :param
        :return:
        """
        if self.filename is None:
            return
        self.txtfilename = 'data/'+self.filename.replace('.bin', '.txt')
        self.txtfile = open(self.txtfilename, 'a')
        while 1:
            filenameprefix = (self.filename.split('-'))[0]

            if filenameprefix == "traindata":
                databag = self.binfile.read(28)
                if not databag:
                    break
                # 数据格式:系统时间(4bytes float)+roll(4bytes float)+gyrox(4bytes float)+gyroz(4bytes float)+angle1(4bytes float)+angle2(4bytes float)+angle3(4bytes float)
                datatuple = struct.unpack('fffffff', databag)
                datastr = ''
                for data in datatuple:
                    datastr = datastr + ' ' + str(data)
                datastr = datastr + '\n'
                self.txtfile.write(datastr)
            else:
                databag = self.binfile.read(20)
                if not databag:
                    break
                # 数据格式:系统时间(4bytes uint32)+欧拉角1(4bytes float)+欧拉角2(4bytes float)+欧拉角3(4bytes float)+压敏电阻1(2bytes uint16)+压敏电阻2(2bytes uint16)
                datatuple = struct.unpack('ffffHH', databag)
                datastr = ''
                for data in datatuple:
                    datastr = datastr + ' ' + str(data)
                datastr = datastr + '\n'
                self.txtfile.write(datastr)
            print(datastr)
        self.file_name_label.setText(self.filename + ' is analysised.')


    def save_data(self):
        """
        数据保存函数,与保存数据按钮链接
        关闭文件
        :param
        :return:
        """
        if self.txtfilename is None:
            return
        self.txtfile.close()
        self.txtfilename = None
        self.file_name_label.setText(self.filename + ' is saved.')


    def handle_click(self):
        if not self.isVisible():
            self.show()


    def handle_close(self):
        self.close()