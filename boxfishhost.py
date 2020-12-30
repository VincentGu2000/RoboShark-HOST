import sys
import time
import serial
import struct
import copy
import platform

from PyQt5 import QtCore,QtGui,QtWidgets

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar

###
### 自定义模块
import childwindows.analysis_btn_win # 解析数据窗口
import childwindows.storage_btn_win # 储存数据窗口
import childwindows.sendback_btn_win # 回传数据窗口
import childwindows.gimbal_control_btn_win # 云台控制窗口
import childwindows.depth_control_btn_win # 深度控制窗口

import rflink # Robotic Fish 通讯协议
import serctl # 串口控制工具
import robotstate # 机器人状态
import sensor_data_canvas

import ctypes
if(platform.system()=='Windows'):
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("myappid")

###
### 类对象
# 机器人状态
boxfishstate = robotstate.RobotState()
# 串口类
send_sertool = serctl.RobotSerial()
recv_sertool = serctl.RobotSerial()
# rf通讯协议类
rftool = rflink.RFLink()



###
### 多线程变量
# 机器人状态线程锁
rm_mutex = QtCore.QMutex()
# 串口线程锁
ser_mutex = QtCore.QMutex()
# 通讯线程锁
rf_mutex = QtCore.QMutex()
rf_cond = QtCore.QWaitCondition()
# 绘图线程锁
plt_mutex = QtCore.QMutex()


#########################################################################################################
def analysis_data(databytes,datalen): # 分析串口接收到的rflink数据,更新boxfishstate的状态
    """
    本函数将串口接收到的rflink数据进行分析,解码出收到的Command,更新boxfishstate的状态
    :param databytes: byte类型数据串
    :param datalen: 数据串长度
    :return: 收到Command的ID
    """
    global boxfishstate

    try:
        command_id = databytes[0]
    except IndexError:
        return rflink.Command.LAST_COMMAND_FLAG.value

    command = rflink.Command(command_id)
    if command is rflink.Command.READ_ROBOT_STATUS:
        boxfishstate.swim_state = robotstate.SwimState((databytes[1]>>6)&3)
        boxfishstate.gimbal_state = robotstate.GimbalState((databytes[1]>>4)&3)

    elif command is rflink.Command.READ_SINE_MOTION_PARAM:
        datatuple = struct.unpack('fff', databytes[1:])
        boxfishstate.motion_amp = datatuple[0]
        boxfishstate.motion_freq = datatuple[1]
        boxfishstate.motion_offset = datatuple[2]

    return command_id

#########################################################################################################
class PollingStateThread(QtCore.QThread): # 轮询线程
    """
    本类创建一个轮询线程,每隔一段时间,通过串口发送获取机器人状态的指令
    """
    def __init__(self,parent=None):
        super(PollingStateThread, self).__init__(parent)
        self.is_running = False
        self.is_pause = False
        self._sync = QtCore.QMutex()
        self._pause_cond = QtCore.QWaitCondition()
        self._count = 0

    def run(self):
        """
        本线程运行的主要循环
        """
        self.is_running = True
        while self.is_running == True:

            self._sync.lock()
            if self.is_pause:
                self._pause_cond.wait(self._sync)
            self._sync.unlock()

            # 这段代码就是在轮询,获取下位机信息,注释掉就没有了
            datapack = rftool.RFLink_packdata(rflink.Command.READ_ROBOT_STATUS.value, 0)

            # 通过串口发送数据
            ser_mutex.lock()
            send_sertool.write_cmd(datapack)
            ser_mutex.unlock()

            # 间隔1s,轮询一次
            self.sleep(1)

    def pause(self):
        """
        暂停线程
        """
        self._sync.lock()
        self.is_pause = True
        self._sync.unlock()

    def resume(self):
        """
        恢复线程
        """
        self._sync.lock()
        self.is_pause = False
        self._sync.unlock()
        self._pause_cond.wakeAll()

    def stop(self):
        """
        终止线程,一旦调用,本线程将无法再打开
        """
        self.is_running = False
        self.terminate()

#########################################################################################################
class ReceiveDataThread(QtCore.QThread): # 数据接收线程
    """
    本类创建一个数据接收线程
    通过串口等待数据,每接收到一个数据,就使用RFLink的接收状态机RFLink_receivedata进行分析
    每次接收到一帧完整的消息后,唤醒AnalysisDataThread线程
    """
    def __init__(self,parent=None):
        super(ReceiveDataThread, self).__init__(parent)
        self.is_running = False
        self.is_pause = False
        self._sync = QtCore.QMutex()
        self._pause_cond = QtCore.QWaitCondition()

    def run(self):
        """
        本线程运行的主要循环
        """
        self.is_running = True
        global rftool
        while self.is_running == True:

            self._sync.lock()
            if self.is_pause:
                self._pause_cond.wait(self._sync)
            self._sync.unlock()

            # 接收数据
            rx_data = recv_sertool.read_data()

            # 数据送入状态机
            rf_mutex.lock()
            if rftool.RFLink_receivedata(rx_data): # 如果返回True,那么通知数据分析线程
                rf_cond.wakeAll() # 通知等待rf_cond的线程
            rf_mutex.unlock()


    def pause(self):
        """
        暂停线程
        """
        self._sync.lock()
        self.is_pause = True
        self._sync.unlock()

    def resume(self):
        """
        恢复线程
        """
        self._sync.lock()
        self.is_pause = False
        self._sync.unlock()
        self._pause_cond.wakeAll()

    def stop(self):
        """
        终止线程,一旦调用,本线程将无法再打开
        """
        self.is_running = False
        self.terminate()

#########################################################################################################
class AnalysisDataThread(QtCore.QThread): # 数据分析线程
    """
    本类创建一个数据分析线程
    每当ReceiveDataThread接收到一帧完整消息后,本线程被唤醒
    本线程分析消息中的Command以及机器人的数据
    """
    # 信号量,用于传递Command的ID
    command_id_out = QtCore.pyqtSignal(int)

    def __init__(self,parent=None):
        super(AnalysisDataThread, self).__init__(parent)
        self.command_id = 0
        self.is_running = False
        self.is_pause = False
        self._sync = QtCore.QMutex()
        self._pause_cond = QtCore.QWaitCondition()

    def run(self):
        """
        本线程运行的主要循环
        """
        self.is_running = True
        global rftool
        while self.is_running == True:

            self._sync.lock()
            if self.is_pause:
                self._pause_cond.wait(self._sync)
            self._sync.unlock()

            # 获取消息
            rf_mutex.lock()
            rf_cond.wait(rf_mutex) # 等待数据接收线程唤醒,一旦唤醒,说明rftool已经接收到了一帧完整的消息
            # 拿到数据
            databytes = rftool.message
            datalen = rftool.length
            rf_mutex.unlock()

            # 分析消息,更新机器人状态
            rm_mutex.lock()
            self.command_id = analysis_data(databytes,datalen)
            rm_mutex.unlock()

            # 通知Main Window
            self.command_id_out.emit(self.command_id)

    def pause(self):
        """
        暂停线程
        """
        self._sync.lock()
        self.is_pause = True
        self._sync.unlock()

    def resume(self):
        """
        恢复线程
        """
        self._sync.lock()
        self.is_pause = False
        self._sync.unlock()
        self._pause_cond.wakeAll()

    def stop(self):
        """
        终止线程,一旦调用,本线程将无法再打开
        """
        self.is_running = False
        self.terminate()

#########################################################################################################
class BoxFishWindow(QtWidgets.QMainWindow): # 主窗口
    """
    boxfishstate Qt 主窗口
    函数大致分为四块:
    第一部分:关于UI定义
    第二部分:关于Slot和Signal的
    第三部分:下位机数据处理
    """
    close_signal = QtCore.pyqtSignal() # 同步关闭主窗口和子窗口
    
    # 初始化
    def __init__(self):
        """
        初始化
        创建三大线程
        初始化UI
        初始化信号和槽的连接
        """
        super(BoxFishWindow, self).__init__()
        # 创建线程
        self.receive_data_thread = ReceiveDataThread()
        self.polling_state_thread = PollingStateThread()
        self.analysis_data_thread = AnalysisDataThread()
        
        # 初始化UI
        self.button_height = 30
        self.init_ui()

        # 初始化控件间信号和槽的连接
        self.widgets_connect()
        self.analysis_data_thread.command_id_out.connect(self.newdata_comming_slot) # 处理下位机数据

        # 子窗口初始化
        ## 储存数据子窗口
        self.STBW = childwindows.storage_btn_win.StorageBtnWin()
        self.datashow_storage_button.clicked.connect(self.STBW.handle_click)
        self.STBW._signal.connect(self.datashow_storage_button_clicked)
        self.close_signal.connect(self.STBW.handle_close)

        ## 回传数据子窗口
        self.SBBW = childwindows.sendback_btn_win.SendbackBtnWin()
        self.datashow_save_button.clicked.connect(self.SBBW.handle_click)
        self.SBBW._signal.connect(self.datashow_save_button_clicked)
        self.close_signal.connect(self.SBBW.handle_close)

        ## 云台控制子窗口
        self.GCBW = childwindows.gimbal_control_btn_win.GimbalControlBtnWin()
        self.open_gimbal_control_button.clicked.connect(self.GCBW.handle_click)
        self.GCBW.gimbalcc_start_button.clicked.connect(self.console_button_clicked)
        self.GCBW.gimbalcc_stop_button.clicked.connect(self.console_button_clicked)
        self.GCBW.gimbalcc_zero_button.clicked.connect(self.console_button_clicked)
        self.close_signal.connect(self.GCBW.handle_close)

        ## 深度控制子窗口
        self.DCBW = childwindows.depth_control_btn_win.DepthControlBtnWin()
        self.open_depth_control_button.clicked.connect(self.DCBW.handle_click)
        self.DCBW.depthctl_start_button.clicked.connect(self.console_button_clicked)
        self.DCBW.depthctl_stop_button.clicked.connect(self.console_button_clicked)
        self.DCBW.depthctl_writeparam_button.clicked.connect(self.console_button_clicked)
        self.close_signal.connect(self.DCBW.handle_close)

        # 绘图部分变量初始化
        self.showtime = 0
        self.timelist = [] # x轴数据,时间
        self.datalist = [] # y轴数据,传感器数据
        self.yaxis_lowbound = -1
        self.yaxis_upbound = 1
        self.datashow_running_flag = False

        self.datashow_sensor_type = 1 # 选择显示哪个传感器
        self.datashow_sensor_id = 1
        self.datashow_sensor_datatype = 1
        self.datashow_sensor_dataaxis = 1

        # 保存数据的文件名
        self.savefile_name = "data.bin"

    #####################################################################################################
    #####################################################################################################
    ## 第一部分:关于UI定义
    #####################################################################################################
    #####################################################################################################
    # 初始化UI界面
    def init_ui(self):
        """
        初始化UI
        :return:
        """
        self.init_layout()
        self.statusBar().showMessage('串口未打开')
        self.setFixedSize(1640,800)# 设置窗体大小
        self.setWindowTitle('Box Fish Host')  # 设置窗口标题
        self.setWindowOpacity(0.98)
        self.setWindowIcon(QtGui.QIcon('icon/my/fish.ico'))
        self.show()  # 窗口显示

    # 初始化layout界面
    def init_layout(self):
        """
        初始化UI界面布局
        UI界面主要分为三个部分:
        self.stateshow_frame:状态显示区
        self.datashow_frame:传感器数据显示区
        self.console_frame:控制台
        self.cmdshell_frame:指令shell区
        :return:
        """

        self.main_widget = QtWidgets.QWidget()
        self.main_widget.setObjectName('main_widget')
        self.main_layout = QtWidgets.QGridLayout()
        self.main_widget.setLayout(self.main_layout)

        # 状态显示区
        self.stateshow_frame = QtWidgets.QFrame()
        self.stateshow_frame.setObjectName('stateshow_frame')
        self.stateshow_layout = QtWidgets.QGridLayout()
        self.stateshow_frame.setLayout(self.stateshow_layout)
        self.stateshow_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.stateshow_frame.setFrameShadow(QtWidgets.QFrame.Raised)
        self.stateshow_frame.setLineWidth(1)

        # 传感器数据显示区
        self.datashow_frame = QtWidgets.QFrame()
        self.datashow_layout = QtWidgets.QGridLayout()
        self.datashow_frame.setLayout(self.datashow_layout)
        self.datashow_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.datashow_frame.setFrameShadow(QtWidgets.QFrame.Raised)
        self.datashow_frame.setLineWidth(1)

        # 控制台
        self.console_frame = QtWidgets.QFrame()
        self.console_layout = QtWidgets.QGridLayout()
        self.console_frame.setLayout(self.console_layout)
        self.console_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.console_frame.setFrameShadow(QtWidgets.QFrame.Raised)
        self.console_frame.setLineWidth(1)

        # shell区
        self.cmdshell_frame = QtWidgets.QFrame()
        self.cmdshell_layout = QtWidgets.QGridLayout()
        self.cmdshell_frame.setLayout(self.cmdshell_layout)
        self.cmdshell_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.cmdshell_frame.setFrameShadow(QtWidgets.QFrame.Raised)
        self.cmdshell_frame.setLineWidth(1)

        # 布局,15行17列
        self.main_layout.addWidget(self.stateshow_frame, 0, 0, 8, 2)
        self.main_layout.addWidget(self.datashow_frame, 0, 2, 8, 13)
        self.main_layout.addWidget(self.console_frame, 8, 0, 5, 15)
        self.main_layout.addWidget(self.cmdshell_frame, 0, 16, 13, 10)

        self.setCentralWidget(self.main_widget)  # 设置窗口主部件

        self.init_stateshow_panel()
        self.init_console_panel()
        self.init_datashow_panel()
        self.init_cmdshell_panel()
        

    # 初始化状态显示区面板
    def init_stateshow_panel(self):
        """
        初始化状态显示区面板
        :return:
        """

        self.stateshow_title_label = QtWidgets.QLabel('状态显示区')
        self.stateshow_title_label.setObjectName('stateshow_title_label')
        self.stateshow_layout.addWidget(self.stateshow_title_label, 0, 0, 1, 2, QtCore.Qt.AlignCenter)

        # 图像显示部分
        self.stateshow_subframe = QtWidgets.QFrame()
        self.stateshow_subframe.setObjectName('stateshow_subframe')
        self.stateshowsubframe_layout = QtWidgets.QGridLayout()
        self.stateshow_subframe.setLayout(self.stateshowsubframe_layout)
        self.stateshow_layout.addWidget(self.stateshow_subframe, 1, 0, 10, 2)

        self.swimstate_fixed_label = QtWidgets.QLabel('游动状态')
        self.swimstate_fixed_label.setObjectName('swimstate_fixed_label')
        self.stateshowsubframe_layout.addWidget(self.swimstate_fixed_label, 1, 0, 1, 1, QtCore.Qt.AlignLeft)

        self.swimstate_label = QtWidgets.QLabel('停止')
        self.swimstate_label.setObjectName('swimstate_label')
        self.stateshowsubframe_layout.addWidget(self.swimstate_label, 1, 1, 1, 1, QtCore.Qt.AlignCenter)

        self.cpgstate_fixed_label = QtWidgets.QLabel('运动参数')
        self.cpgstate_fixed_label.setObjectName('cpgstate_fixed_label')
        self.stateshowsubframe_layout.addWidget(self.cpgstate_fixed_label, 2, 0, 1, 2, QtCore.Qt.AlignLeft)

        self.cpgamp_fixed_label = QtWidgets.QLabel('幅度')
        self.cpgamp_fixed_label.setObjectName('cpgamp_fixed_label')
        self.stateshowsubframe_layout.addWidget(self.cpgamp_fixed_label, 3, 0, 1, 1, QtCore.Qt.AlignRight)

        self.cpgamp_label = QtWidgets.QLabel('0.0')
        self.cpgamp_label.setObjectName('cpgamp_label')
        self.stateshowsubframe_layout.addWidget(self.cpgamp_label, 3, 1, 1, 1, QtCore.Qt.AlignCenter)

        self.cpgfreq_fixed_label = QtWidgets.QLabel('频率')
        self.cpgfreq_fixed_label.setObjectName('cpgfreq_fixed_label')
        self.stateshowsubframe_layout.addWidget(self.cpgfreq_fixed_label, 4, 0, 1, 1, QtCore.Qt.AlignRight)

        self.cpgfreq_label = QtWidgets.QLabel('0.0')
        self.cpgfreq_label.setObjectName('cpgfreq_label')
        self.stateshowsubframe_layout.addWidget(self.cpgfreq_label, 4, 1, 1, 1, QtCore.Qt.AlignCenter)

        self.cpgoffset_fixed_label = QtWidgets.QLabel('偏移')
        self.cpgoffset_fixed_label.setObjectName('cpgoffset_fixed_label')
        self.stateshowsubframe_layout.addWidget(self.cpgoffset_fixed_label, 5, 0, 1, 1, QtCore.Qt.AlignRight)

        self.cpgoffset_label = QtWidgets.QLabel('0.0')
        self.cpgoffset_label.setObjectName('cpgoffset_label')
        self.stateshowsubframe_layout.addWidget(self.cpgoffset_label, 5, 1, 1, 1, QtCore.Qt.AlignCenter)

        self.read_robot_state_button = QtWidgets.QPushButton('读取状态')
        self.stateshowsubframe_layout.addWidget(self.read_robot_state_button, 6, 0, 1, 2, QtCore.Qt.AlignCenter)
        self.read_robot_state_button.setObjectName("READ_ROBOT_STATUS")
        self.read_robot_state_button.setFixedSize(140, self.button_height)
        
        
    # 初始化控制台面板
    def init_console_panel(self):
        """
        初始化控制台面板
        控制台面板分为五大板块
        self.swimcc_frame:游动控制
        self.cpgcc_frame:CPG参数控制
        self.advancedcc_frame:高级控制
        :return:
        """
        self.console_title_label = QtWidgets.QLabel('机器鱼控制台')
        self.console_title_label.setObjectName('console_title_label')
        self.console_layout.addWidget(self.console_title_label, 0, 0, 1, 10, QtCore.Qt.AlignCenter)

        # 游动控制
        self.swimcc_frame = QtWidgets.QFrame()
        self.swimcc_frame.setObjectName('swimcc_frame')
        self.swimcc_layout = QtWidgets.QGridLayout()
        self.swimcc_frame.setLayout(self.swimcc_layout)
        self.console_layout.addWidget(self.swimcc_frame, 1, 0, 10, 5)

        self.swimcc_fixed_label = QtWidgets.QLabel('基础运动控制')
        self.swimcc_fixed_label.setObjectName('swimcc_fixed_label')
        self.swimcc_layout.addWidget(self.swimcc_fixed_label, 1, 0, 1, 4, QtCore.Qt.AlignCenter)

        self.swimcc_start_button = QtWidgets.QPushButton('启动(q)')
        self.swimcc_layout.addWidget(self.swimcc_start_button, 2, 0, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_start_button.setObjectName("SET_SWIM_RUN")
        self.swimcc_start_button.setShortcut('q')
        self.swimcc_start_button.setFixedSize(110, self.button_height)

        self.swimcc_stop_button = QtWidgets.QPushButton('暂停(w)')
        self.swimcc_layout.addWidget(self.swimcc_stop_button, 2, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_stop_button.setObjectName("SET_SWIM_STOP")
        self.swimcc_stop_button.setShortcut('w')
        self.swimcc_stop_button.setFixedSize(110, self.button_height)

        self.swimcc_forcestop_button = QtWidgets.QPushButton('停止(e)')
        self.swimcc_layout.addWidget(self.swimcc_forcestop_button, 2, 2, 1,  1, QtCore.Qt.AlignCenter)
        self.swimcc_forcestop_button.setObjectName("SET_SWIM_FORCESTOP")
        self.swimcc_forcestop_button.setShortcut('e')
        self.swimcc_forcestop_button.setFixedSize(110, self.button_height)

        self.swimcc_turnleft_button = QtWidgets.QPushButton('左转(a)')
        self.swimcc_layout.addWidget(self.swimcc_turnleft_button, 3, 0, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_turnleft_button.setObjectName("SET_SWIM_LEFT")
        self.swimcc_turnleft_button.setShortcut('a')
        self.swimcc_turnleft_button.setFixedSize(110, self.button_height)

        self.swimcc_straight_button = QtWidgets.QPushButton('直游(s)')
        self.swimcc_layout.addWidget(self.swimcc_straight_button, 3, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_straight_button.setObjectName("SET_SWIM_STRAIGHT")
        self.swimcc_straight_button.setShortcut('s')
        self.swimcc_straight_button.setFixedSize(110, self.button_height)

        self.swimcc_turnright_button = QtWidgets.QPushButton('右转(d)')
        self.swimcc_layout.addWidget(self.swimcc_turnright_button, 3, 2, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_turnright_button.setObjectName("SET_SWIM_RIGHT")
        self.swimcc_turnright_button.setShortcut('d')
        self.swimcc_turnright_button.setFixedSize(110, self.button_height)

        self.swimcc_speedup_button = QtWidgets.QPushButton('加速(z)')
        self.swimcc_layout.addWidget(self.swimcc_speedup_button, 4, 0, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_speedup_button.setObjectName("SET_SWIM_SPEEDUP")
        self.swimcc_speedup_button.setShortcut('z')
        self.swimcc_speedup_button.setFixedSize(110, self.button_height)

        self.swimcc_speeddown_button = QtWidgets.QPushButton('减速(x)')
        self.swimcc_layout.addWidget(self.swimcc_speeddown_button, 5, 0, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_speeddown_button.setObjectName("SET_SWIM_SPEEDDOWN")
        self.swimcc_speeddown_button.setShortcut('x')
        self.swimcc_speeddown_button.setFixedSize(110, self.button_height)

        self.swimcc_raise_button = QtWidgets.QPushButton('上浮(↑)')
        self.swimcc_layout.addWidget(self.swimcc_raise_button, 2, 3, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_raise_button.setObjectName("SET_SWIM_UP")
        self.swimcc_raise_button.setShortcut(QtCore.Qt.Key_Up)
        self.swimcc_raise_button.setFixedSize(110, self.button_height)

        self.swimcc_dive_button = QtWidgets.QPushButton('下潜(↓)')
        self.swimcc_layout.addWidget(self.swimcc_dive_button, 3, 3, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_dive_button.setObjectName("SET_SWIM_DOWN")
        self.swimcc_dive_button.setShortcut(QtCore.Qt.Key_Down)
        self.swimcc_dive_button.setFixedSize(110, self.button_height)

        self.swimcc_leftfinzero_button = QtWidgets.QPushButton('左胸鳍回中(i)')
        self.swimcc_layout.addWidget(self.swimcc_leftfinzero_button, 4, 2, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_leftfinzero_button.setObjectName("SET_LEFTPECFIN_ZERO")
        self.swimcc_leftfinzero_button.setShortcut('i')
        self.swimcc_leftfinzero_button.setFixedSize(110, self.button_height)

        self.swimcc_rightfinzero_button = QtWidgets.QPushButton('右胸鳍回中(k)')
        self.swimcc_layout.addWidget(self.swimcc_rightfinzero_button, 5, 2, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_rightfinzero_button.setObjectName("SET_RIGHTPECFIN_ZERO")
        self.swimcc_rightfinzero_button.setShortcut('k')
        self.swimcc_rightfinzero_button.setFixedSize(110, self.button_height)

        self.swimcc_leftfinup_button = QtWidgets.QPushButton('左胸鳍+(u)')
        self.swimcc_layout.addWidget(self.swimcc_leftfinup_button, 4, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_leftfinup_button.setObjectName("SET_LEFTPECFIN_UP")
        self.swimcc_leftfinup_button.setShortcut('u')
        self.swimcc_leftfinup_button.setFixedSize(110, self.button_height)

        self.swimcc_leftfindown_button = QtWidgets.QPushButton('左胸鳍-(o)')
        self.swimcc_layout.addWidget(self.swimcc_leftfindown_button, 4, 3, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_leftfindown_button.setObjectName("SET_LEFTPECFIN_DOWN")
        self.swimcc_leftfindown_button.setShortcut('o')
        self.swimcc_leftfindown_button.setFixedSize(110, self.button_height)

        self.swimcc_rightfinup_button = QtWidgets.QPushButton('右胸鳍+(j)')
        self.swimcc_layout.addWidget(self.swimcc_rightfinup_button, 5, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_rightfinup_button.setObjectName("SET_RIGHTPECFIN_UP")
        self.swimcc_rightfinup_button.setShortcut('j')
        self.swimcc_rightfinup_button.setFixedSize(110, self.button_height)

        self.swimcc_rightfindown_button = QtWidgets.QPushButton('右胸鳍-(l)')
        self.swimcc_layout.addWidget(self.swimcc_rightfindown_button, 5, 3, 1, 1, QtCore.Qt.AlignCenter)
        self.swimcc_rightfindown_button.setObjectName("SET_RIGHTPECFIN_DOWN")
        self.swimcc_rightfindown_button.setShortcut('l')
        self.swimcc_rightfindown_button.setFixedSize(110, self.button_height)

        # CPG参数设置
        self.cpgcc_frame = QtWidgets.QFrame()
        self.cpgcc_frame.setObjectName('cpgcc_frame')
        self.cpgcc_layout = QtWidgets.QGridLayout()
        self.cpgcc_frame.setLayout(self.cpgcc_layout)
        self.console_layout.addWidget(self.cpgcc_frame, 1, 5, 10, 2)

        self.cpgcc_fixed_label = QtWidgets.QLabel('运动参数设置')
        self.cpgcc_fixed_label.setObjectName('cpgcc_fixed_label')
        self.cpgcc_layout.addWidget(self.cpgcc_fixed_label, 1, 0, 1, 3, QtCore.Qt.AlignCenter)

        self.cpgcc_amp_label = QtWidgets.QLabel('幅度')
        self.cpgcc_amp_label.setObjectName('cpgcc_amp_label')
        self.cpgcc_amp_label.setFixedSize(50, self.button_height)
        self.cpgcc_layout.addWidget(self.cpgcc_amp_label, 2, 0, 1, 1, QtCore.Qt.AlignCenter)

        self.cpgcc_amp_edit = QtWidgets.QLineEdit()
        self.cpgcc_amp_edit.setFixedSize(100, self.button_height)
        self.cpgcc_amp_edit.setPlaceholderText('0~30')
        double_validator1 = QtGui.QDoubleValidator()
        double_validator1.setRange(0, 30)
        double_validator1.setNotation(QtGui.QDoubleValidator.StandardNotation)
        double_validator1.setDecimals(3)
        self.cpgcc_amp_edit.setValidator(double_validator1)
        self.cpgcc_layout.addWidget(self.cpgcc_amp_edit, 2, 1, 1, 1, QtCore.Qt.AlignCenter)

        self.cpgcc_amp_button = QtWidgets.QPushButton('写入')
        self.cpgcc_layout.addWidget(self.cpgcc_amp_button, 2, 2, 1, 1, QtCore.Qt.AlignCenter)
        self.cpgcc_amp_button.setObjectName("SET_SINE_MOTION_AMP")
        self.cpgcc_amp_button.setFixedSize(60, self.button_height)

        self.cpgcc_freq_label = QtWidgets.QLabel('频率')
        self.cpgcc_freq_label.setObjectName('cpgcc_freq_label')
        self.cpgcc_freq_label.setFixedSize(50, self.button_height)
        self.cpgcc_layout.addWidget(self.cpgcc_freq_label, 3, 0, 1, 1, QtCore.Qt.AlignCenter)

        self.cpgcc_freq_edit = QtWidgets.QLineEdit()
        self.cpgcc_freq_edit.setFixedSize(100, self.button_height)
        self.cpgcc_freq_edit.setPlaceholderText('0~3.0')
        double_validator2 = QtGui.QDoubleValidator()
        double_validator2.setRange(0, 3.0)
        double_validator2.setNotation(QtGui.QDoubleValidator.StandardNotation)
        double_validator2.setDecimals(2)
        self.cpgcc_freq_edit.setValidator(double_validator2)
        self.cpgcc_layout.addWidget(self.cpgcc_freq_edit, 3, 1, 1, 1, QtCore.Qt.AlignCenter)

        self.cpgcc_freq_button = QtWidgets.QPushButton('写入')
        self.cpgcc_layout.addWidget(self.cpgcc_freq_button, 3, 2, 1, 1, QtCore.Qt.AlignCenter)
        self.cpgcc_freq_button.setObjectName("SET_SINE_MOTION_FREQ")
        self.cpgcc_freq_button.setFixedSize(60, self.button_height)

        self.cpgcc_offset_label = QtWidgets.QLabel('偏移')
        self.cpgcc_offset_label.setObjectName('cpgcc_offset_label')
        self.cpgcc_offset_label.setFixedSize(50, self.button_height)
        self.cpgcc_layout.addWidget(self.cpgcc_offset_label, 4, 0, 1, 1, QtCore.Qt.AlignCenter)

        self.cpgcc_offset_edit = QtWidgets.QLineEdit()
        self.cpgcc_offset_edit.setFixedSize(100, self.button_height)
        self.cpgcc_offset_edit.setPlaceholderText('-30~30')
        double_validator3 = QtGui.QDoubleValidator()
        double_validator3.setRange(-30, 30)
        double_validator3.setNotation(QtGui.QDoubleValidator.StandardNotation)
        double_validator3.setDecimals(2)
        self.cpgcc_offset_edit.setValidator(double_validator3)
        self.cpgcc_layout.addWidget(self.cpgcc_offset_edit, 4, 1, 1, 1, QtCore.Qt.AlignCenter)

        self.cpgcc_offset_button = QtWidgets.QPushButton('写入')
        self.cpgcc_layout.addWidget(self.cpgcc_offset_button, 4, 2, 1, 1, QtCore.Qt.AlignCenter)
        self.cpgcc_offset_button.setObjectName("SET_SINE_MOTION_OFFSET")
        self.cpgcc_offset_button.setFixedSize(60, self.button_height)

        self.cpgcc_readparam_button = QtWidgets.QPushButton('读取参数(r)')
        self.cpgcc_layout.addWidget(self.cpgcc_readparam_button, 5, 0, 1, 3, QtCore.Qt.AlignCenter)
        self.cpgcc_readparam_button.setObjectName("READ_SINE_MOTION_PARAM")
        self.cpgcc_readparam_button.setShortcut('r')
        self.cpgcc_readparam_button.setFixedSize(180, self.button_height)

        # 高级控制选项
        self.advancedcc_frame = QtWidgets.QFrame()
        self.advancedcc_frame.setObjectName('advancedcc_frame')
        self.advancedcc_layout = QtWidgets.QGridLayout()
        self.advancedcc_frame.setLayout(self.advancedcc_layout)
        self.console_layout.addWidget(self.advancedcc_frame, 1, 7, 10, 3)

        self.advancedcc_fixed_label = QtWidgets.QLabel('高级控制功能')
        self.advancedcc_fixed_label.setObjectName('advancedcc_fixed_label')
        self.advancedcc_layout.addWidget(self.advancedcc_fixed_label, 1, 0, 1, 3, QtCore.Qt.AlignCenter)
        
        # 云台控制按钮
        self.open_gimbal_control_button = QtWidgets.QPushButton('云台控制')
        self.advancedcc_layout.addWidget(self.open_gimbal_control_button, 2, 0, 1, 1, QtCore.Qt.AlignCenter)
        self.open_gimbal_control_button.setFixedSize(100, self.button_height)
        # 深度控制按钮
        self.open_depth_control_button = QtWidgets.QPushButton('深度控制')
        self.advancedcc_layout.addWidget(self.open_depth_control_button, 3, 0, 1, 1, QtCore.Qt.AlignCenter)
        self.open_depth_control_button.setFixedSize(100, self.button_height)
        # 位置控制按钮
        self.open_position_control_button = QtWidgets.QPushButton('位置控制')
        self.advancedcc_layout.addWidget(self.open_position_control_button, 3, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.open_position_control_button.setFixedSize(100, self.button_height)
        # 速度控制按钮
        self.open_velocity_control_button = QtWidgets.QPushButton('速度控制')
        self.advancedcc_layout.addWidget(self.open_velocity_control_button, 3, 2, 1, 1, QtCore.Qt.AlignCenter)
        self.open_velocity_control_button.setFixedSize(100, self.button_height)
        # 跟踪控制按钮
        self.open_targettracking_control_button = QtWidgets.QPushButton('跟踪控制')
        self.advancedcc_layout.addWidget(self.open_targettracking_control_button, 4, 0, 1, 1, QtCore.Qt.AlignCenter)
        self.open_targettracking_control_button.setFixedSize(100, self.button_height)


    # 初始化command shell面板
    def init_cmdshell_panel(self):
        """
        初始化command shell面板
        主要分为:输出窗口,输入命令和串口控制部分
        :return:
        """
        self.cmdshell_title_label = QtWidgets.QLabel('Command Shell')
        self.cmdshell_title_label.setObjectName('cmdshell_title_label')
        self.cmdshell_layout.addWidget(self.cmdshell_title_label, 0, 0, 1, 10, QtCore.Qt.AlignCenter)

        # 输出窗口和输入命令
        self.cmdshell_text_frame = QtWidgets.QFrame()
        self.cmdshell_text_frame.setObjectName('cmdshell_text_frame')
        self.cmdshell_text_layout = QtWidgets.QGridLayout()
        self.cmdshell_text_frame.setLayout(self.cmdshell_text_layout)
        self.cmdshell_layout.addWidget(self.cmdshell_text_frame, 1, 0, 10, 10)

        self.cmdshell_browser_label = QtWidgets.QLabel('输出窗口')
        self.cmdshell_browser_label.setObjectName('cmdshell_browser_label')
        self.cmdshell_text_layout.addWidget(self.cmdshell_browser_label, 0, 0, 1, 10, QtCore.Qt.AlignLeft)
        self.cmdshell_text_browser = QtWidgets.QTextBrowser()
        self.cmdshell_text_browser.setObjectName('cmdshell_text_browser')
        self.cmdshell_text_browser.setFixedSize(400, 300)
        self.cmdshell_text_layout.addWidget(self.cmdshell_text_browser, 1, 0, 8, 10, QtCore.Qt.AlignCenter)
        self.cmdshell_text_browser.append("<font color='Cyan'>boxfishstate-host:~$&nbsp;</font> ")

        self.cmdshell_editor_label = QtWidgets.QLabel('输入命令')
        self.cmdshell_editor_label.setObjectName('cmdshell_editor_label')
        self.cmdshell_text_layout.addWidget(self.cmdshell_editor_label, 9, 0, 1, 2, QtCore.Qt.AlignLeft)
        self.cmdshell_text_editor = QtWidgets.QLineEdit()
        self.cmdshell_text_editor.setObjectName('cmdshell_text_editor')
        self.cmdshell_text_editor.setFixedSize(300, 30)
        self.cmdshell_text_layout.addWidget(self.cmdshell_text_editor, 9, 2, 1, 8, QtCore.Qt.AlignCenter)

        # 串口控制
        self.serial_frame = QtWidgets.QFrame()
        self.serial_frame.setObjectName('serial_frame')
        self.serial_layout = QtWidgets.QGridLayout()
        self.serial_frame.setLayout(self.serial_layout)
        self.cmdshell_layout.addWidget(self.serial_frame, 11, 0, 4, 10)

        self.serial_fixed_label = QtWidgets.QLabel('串口控制')
        self.serial_fixed_label.setObjectName('serial_fixed_label')
        self.serial_layout.addWidget(self.serial_fixed_label, 0, 0, 1, 4, QtCore.Qt.AlignCenter)

        # 串口1--发送串口
        self.serial1_com_label = QtWidgets.QLabel('发送COM')
        self.serial1_com_label.setObjectName('serial1_com_label')
        self.serial_layout.addWidget(self.serial1_com_label, 1, 0, 1, 1, QtCore.Qt.AlignLeft)

        self.serial1_com_combo = QtWidgets.QComboBox()
        self.serial1_com_combo.addItem('ttyUSB0')
        self.serial1_com_combo.addItem('ttyUSB1')
        self.serial1_com_combo.addItem('ttyUSB2')
        self.serial1_com_combo.addItem('ttyUSB3')
        self.serial1_com_combo.addItem('COM3')
        self.serial1_com_combo.addItem('COM5')
        self.serial1_com_combo.addItem('COM6')
        self.serial1_com_combo.addItem('COM7')
        self.serial1_com_combo.addItem('COM9')
        self.serial1_com_combo.addItem('COM10')
        self.serial1_com_combo.addItem('COM11')
        self.serial1_com_combo.addItem('COM12')
        self.serial1_com_combo.addItem('COM13')
        self.serial1_com_combo.addItem('COM18')
        self.serial1_com_combo.addItem('COM19')
        self.serial1_com_combo.addItem('COM20')
        self.serial1_com_combo.setFixedSize(140, 30)
        self.serial_layout.addWidget(self.serial1_com_combo, 2, 0, 1, 1, QtCore.Qt.AlignLeft)

        self.serial1_bps_label = QtWidgets.QLabel('BPS')
        self.serial1_bps_label.setObjectName('serial1_bps_label')
        self.serial_layout.addWidget(self.serial1_bps_label, 1, 1, 1, 1, QtCore.Qt.AlignLeft)

        self.serial1_bps_combo = QtWidgets.QComboBox()
        self.serial1_bps_combo.addItem('9600')
        self.serial1_bps_combo.addItem('14400')
        self.serial1_bps_combo.addItem('19200')
        self.serial1_bps_combo.addItem('38400')
        self.serial1_bps_combo.addItem('56000')
        self.serial1_bps_combo.addItem('57600')
        self.serial1_bps_combo.addItem('115200')
        self.serial1_bps_combo.setFixedSize(120, self.button_height)
        self.serial_layout.addWidget(self.serial1_bps_combo, 2, 1, 1, 1, QtCore.Qt.AlignLeft)

       

        self.serial1_open_button = QtWidgets.QPushButton('打开')
        self.serial1_open_button.setFixedSize(60, self.button_height)
        self.serial_layout.addWidget(self.serial1_open_button, 2, 2, 1, 1, QtCore.Qt.AlignCenter)

        self.serial1_close_button = QtWidgets.QPushButton('关闭')
        self.serial1_close_button.setFixedSize(60, self.button_height)
        self.serial_layout.addWidget(self.serial1_close_button, 2, 3, 1, 1, QtCore.Qt.AlignCenter)

        # 串口2--接收串口
        self.serial2_com_label = QtWidgets.QLabel('接收COM')
        self.serial2_com_label.setObjectName('serial2_com_label')
        self.serial_layout.addWidget(self.serial2_com_label, 3, 0, 1, 1, QtCore.Qt.AlignLeft)

        self.serial2_com_combo = QtWidgets.QComboBox()
        self.serial2_com_combo.addItem('ttyUSB1')
        self.serial2_com_combo.addItem('ttyUSB0')
        self.serial2_com_combo.addItem('ttyUSB2')
        self.serial2_com_combo.addItem('ttyUSB3')
        self.serial2_com_combo.addItem('COM3')
        self.serial2_com_combo.addItem('COM5')
        self.serial2_com_combo.addItem('COM6')
        self.serial2_com_combo.addItem('COM7')
        self.serial2_com_combo.addItem('COM9')
        self.serial2_com_combo.addItem('COM10')
        self.serial2_com_combo.addItem('COM11')
        self.serial2_com_combo.addItem('COM12')
        self.serial2_com_combo.addItem('COM13')
        self.serial2_com_combo.addItem('COM18')
        self.serial2_com_combo.addItem('COM19')
        self.serial2_com_combo.addItem('COM20')
        self.serial2_com_combo.setFixedSize(140, self.button_height)
        self.serial_layout.addWidget(self.serial2_com_combo, 4, 0, 1, 1, QtCore.Qt.AlignLeft)

        self.serial2_bps_label = QtWidgets.QLabel('BPS')
        self.serial2_bps_label.setObjectName('serial2_bps_label')
        self.serial_layout.addWidget(self.serial2_bps_label, 3, 1, 1, 1, QtCore.Qt.AlignLeft)

        self.serial2_bps_combo = QtWidgets.QComboBox()
        self.serial2_bps_combo.addItem('19200')
        self.serial2_bps_combo.addItem('9600')
        self.serial2_bps_combo.addItem('14400')
        self.serial2_bps_combo.addItem('38400')
        self.serial2_bps_combo.addItem('56000')
        self.serial2_bps_combo.addItem('57600')
        self.serial2_bps_combo.addItem('115200')
        self.serial2_bps_combo.setFixedSize(120, self.button_height)
        self.serial_layout.addWidget(self.serial2_bps_combo, 4, 1, 1, 1, QtCore.Qt.AlignLeft)


        self.serial2_open_button = QtWidgets.QPushButton('打开')
        self.serial2_open_button.setFixedSize(60, self.button_height)
        self.serial_layout.addWidget(self.serial2_open_button, 4, 2, 1, 1, QtCore.Qt.AlignCenter)

        self.serial2_close_button = QtWidgets.QPushButton('关闭')
        self.serial2_close_button.setFixedSize(60, self.button_height)
        self.serial_layout.addWidget(self.serial2_close_button, 4, 3, 1, 1, QtCore.Qt.AlignCenter)


        self.fishid_label = QtWidgets.QLabel('机器鱼编号:')
        self.fishid_label.setObjectName('fishid_label')
        self.serial_layout.addWidget(self.fishid_label, 5, 0, 1, 1, QtCore.Qt.AlignCenter)

        self.fishid_combo = QtWidgets.QComboBox()
        for robot_id in rflink.BoxFishID:
            self.fishid_combo.addItem(robot_id.name)
        self.fishid_combo.setCurrentText('BoxFish_1')
        self.fishid_combo.setFixedSize(120, self.button_height)
        self.serial_layout.addWidget(self.fishid_combo, 5, 1, 1, 1, QtCore.Qt.AlignLeft)

        self.serial_shakehand_button = QtWidgets.QPushButton('握手/同步')
        self.serial_shakehand_button.setFixedSize(120, self.button_height)
        self.serial_shakehand_button.setObjectName("SHAKING_HANDS")
        self.serial_layout.addWidget(self.serial_shakehand_button, 5, 2, 1, 2, QtCore.Qt.AlignCenter)


    # 初始化传感器数据显示区面板
    def init_datashow_panel(self):
        """
        初始化传感器数据显示区面板
        :return:...........................
        """
        self.datashow_title_label = QtWidgets.QLabel('传感器数据显示区')
        self.datashow_title_label.setObjectName('datashow_title_label')
        self.datashow_layout.addWidget(self.datashow_title_label, 0, 0, 1, 15, QtCore.Qt.AlignCenter)

        # 图像显示部分
        self.canvas_frame = QtWidgets.QFrame()
        self.canvas_frame.setObjectName('canvas_frame')
        self.canvas_layout = QtWidgets.QVBoxLayout()
        self.canvas_frame.setLayout(self.canvas_layout)

        self.datashow_layout.addWidget(self.canvas_frame, 1, 0, 10, 12)

        self.sensor_data_canvas = sensor_data_canvas.SensorDataCanvas()
        self.navigationbar = NavigationToolbar(self.sensor_data_canvas,self.canvas_frame)
        self.canvas_layout.addWidget(self.navigationbar, QtCore.Qt.AlignCenter)
        self.canvas_layout.addWidget(self.sensor_data_canvas)

        self.datasc_frame = QtWidgets.QFrame()
        self.datasc_frame.setObjectName('datasc_frame')
        self.datasc_layout = QtWidgets.QGridLayout()
        self.datasc_frame.setLayout(self.datasc_layout)
        self.datashow_layout.addWidget(self.datasc_frame, 1, 12, 10, 3)

        # 数据显示控制台
        self.datasc_label = QtWidgets.QLabel("数据显示控制台")
        self.datasc_label.setObjectName('datasc_label')
        self.datasc_layout.addWidget(self.datasc_label, 1, 0, 1, 3, QtCore.Qt.AlignCenter)

        self.imu_checkbox = QtWidgets.QCheckBox("IMU")
        self.imu_checkbox.setObjectName('imu_checkbox')
        self.imu_checkbox.setChecked(True)
        self.datasc_layout.addWidget(self.imu_checkbox, 2, 0, 1, 3, QtCore.Qt.AlignCenter)

        self.imu1_checkbox = QtWidgets.QCheckBox("IMU1")
        self.imu1_checkbox.setObjectName('imu1_checkbox')
        self.imu1_checkbox.setChecked(True)
        self.datasc_layout.addWidget(self.imu1_checkbox, 3, 0, 1, 1, QtCore.Qt.AlignLeft)

        self.imu2_checkbox = QtWidgets.QCheckBox("IMU2")
        self.imu2_checkbox.setObjectName('imu2_checkbox')
        self.datasc_layout.addWidget(self.imu2_checkbox, 3, 1, 1, 1, QtCore.Qt.AlignLeft)

        self.accel_checkbox = QtWidgets.QCheckBox("加速度")
        self.accel_checkbox.setObjectName('accel_checkbox')
        self.datasc_layout.addWidget(self.accel_checkbox, 4, 1, 1, 1, QtCore.Qt.AlignLeft)

        self.gyro_checkbox = QtWidgets.QCheckBox("角速度")
        self.gyro_checkbox.setObjectName('gyro_checkbox')
        self.datasc_layout.addWidget(self.gyro_checkbox, 4, 2, 1, 1, QtCore.Qt.AlignLeft)

        self.angle_checkbox = QtWidgets.QCheckBox("角度")
        self.angle_checkbox.setObjectName('angle_checkbox')
        self.angle_checkbox.setChecked(True)
        self.datasc_layout.addWidget(self.angle_checkbox, 4, 0, 1, 1, QtCore.Qt.AlignLeft)

        self.x_checkbox = QtWidgets.QCheckBox("X轴")
        self.x_checkbox.setObjectName('x_checkbox')
        self.x_checkbox.setChecked(True)
        self.datasc_layout.addWidget(self.x_checkbox, 5, 0, 1, 1, QtCore.Qt.AlignLeft)

        self.y_checkbox = QtWidgets.QCheckBox("Y轴")
        self.y_checkbox.setObjectName('y_checkbox')
        self.datasc_layout.addWidget(self.y_checkbox, 5, 1, 1, 1, QtCore.Qt.AlignLeft)

        self.z_checkbox = QtWidgets.QCheckBox("Z轴")
        self.z_checkbox.setObjectName('z_checkbox')
        self.datasc_layout.addWidget(self.z_checkbox, 5, 2, 1, 1, QtCore.Qt.AlignLeft)

        self.anglesensor_checkbox = QtWidgets.QCheckBox("云台角度传感器")
        self.anglesensor_checkbox.setObjectName('anglesensor_checkbox')
        self.datasc_layout.addWidget(self.anglesensor_checkbox, 6, 0, 1, 3, QtCore.Qt.AlignCenter)

        self.ang1_checkbox = QtWidgets.QCheckBox("传感器1")
        self.ang1_checkbox.setObjectName('ang1_checkbox')
        self.datasc_layout.addWidget(self.ang1_checkbox, 7, 0, 1, 1, QtCore.Qt.AlignLeft)

        self.ang2_checkbox = QtWidgets.QCheckBox("传感器2")
        self.ang2_checkbox.setObjectName('ang2_checkbox')
        self.datasc_layout.addWidget(self.ang2_checkbox, 7, 1, 1, 1, QtCore.Qt.AlignLeft)

        self.anglesensor_checkbox.setChecked(False)
        self.ang1_checkbox.setEnabled(False)
        self.ang2_checkbox.setEnabled(False)

        self.depthsensor_checkbox = QtWidgets.QCheckBox("深度传感器")
        self.depthsensor_checkbox.setObjectName('depthsensor_checkbox')
        self.datasc_layout.addWidget(self.depthsensor_checkbox, 8, 0, 1, 3, QtCore.Qt.AlignCenter)

        self.depth_checkbox = QtWidgets.QCheckBox("深度")
        self.depth_checkbox.setObjectName('depth_checkbox')
        self.datasc_layout.addWidget(self.depth_checkbox, 9, 0, 1, 1, QtCore.Qt.AlignLeft)

        self.depth_checkbox.setChecked(False)
        self.depth_checkbox.setEnabled(False)

        self.datashow_start_button = QtWidgets.QPushButton('开始显示')
        self.datashow_start_button.setFixedSize(80, self.button_height)
        self.datasc_layout.addWidget(self.datashow_start_button, 10, 0, 1, 1, QtCore.Qt.AlignCenter)

        self.datashow_stop_button = QtWidgets.QPushButton('停止显示')
        self.datashow_stop_button.setFixedSize(80, self.button_height)
        self.datasc_layout.addWidget(self.datashow_stop_button, 10, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.datashow_stop_button.setObjectName("SET_DATASHOW_OVER")

        self.datashow_clear_button = QtWidgets.QPushButton('清空界面')
        self.datashow_clear_button.setFixedSize(80, self.button_height)
        self.datasc_layout.addWidget(self.datashow_clear_button, 10, 2, 1, 1, QtCore.Qt.AlignCenter)

        self.datashow_storage_button = QtWidgets.QPushButton('记录数据')
        self.datashow_storage_button.setFixedSize(80, self.button_height)
        self.datasc_layout.addWidget(self.datashow_storage_button, 11, 0, 1, 1, QtCore.Qt.AlignCenter)
        self.datashow_storage_button.setObjectName("GOTO_STORAGE_DATA")

        self.datashow_stopstorage_button = QtWidgets.QPushButton('停止记录')
        self.datashow_stopstorage_button.setFixedSize(80, self.button_height)
        self.datasc_layout.addWidget(self.datashow_stopstorage_button, 11, 1, 1, 1, QtCore.Qt.AlignCenter)
        self.datashow_stopstorage_button.setObjectName("GOTO_STOP_STORAGE")

        self.datashow_save_button = QtWidgets.QPushButton('回传数据')
        self.datashow_save_button.setFixedSize(80, self.button_height)
        self.datasc_layout.addWidget(self.datashow_save_button, 11, 2, 1, 1, QtCore.Qt.AlignCenter)
        self.datashow_save_button.setObjectName("GOTO_SEND_DATA")


    def closeEvent(self, event):
        self.close_signal.emit()
        self.close()

    #####################################################################################################
    #####################################################################################################
    ## 第二部分:关于Slot和Signal的
    #####################################################################################################
    #####################################################################################################
    # 信号连接
    def widgets_connect(self):
        """
        本函数将按钮发送信号与对应槽函数构建连接
        :return:
        """
        # 按钮
        self.swimcc_start_button.clicked.connect(self.console_button_clicked)
        self.swimcc_stop_button.clicked.connect(self.console_button_clicked)
        self.swimcc_forcestop_button.clicked.connect(self.console_button_clicked)
        self.swimcc_speedup_button.clicked.connect(self.console_button_clicked)
        self.swimcc_speeddown_button.clicked.connect(self.console_button_clicked)
        self.swimcc_turnleft_button.clicked.connect(self.console_button_clicked)
        self.swimcc_straight_button.clicked.connect(self.console_button_clicked)
        self.swimcc_turnright_button.clicked.connect(self.console_button_clicked)
        self.swimcc_dive_button.clicked.connect(self.console_button_clicked)
        self.swimcc_raise_button.clicked.connect(self.console_button_clicked)
        self.swimcc_leftfinzero_button.clicked.connect(self.console_button_clicked)
        self.swimcc_leftfinup_button.clicked.connect(self.console_button_clicked)
        self.swimcc_leftfindown_button.clicked.connect(self.console_button_clicked)
        self.swimcc_rightfinzero_button.clicked.connect(self.console_button_clicked)
        self.swimcc_rightfinup_button.clicked.connect(self.console_button_clicked)
        self.swimcc_rightfindown_button.clicked.connect(self.console_button_clicked)
        self.cpgcc_amp_button.clicked.connect(self.console_button_clicked)
        self.cpgcc_freq_button.clicked.connect(self.console_button_clicked)
        self.cpgcc_offset_button.clicked.connect(self.console_button_clicked)
        self.cpgcc_readparam_button.clicked.connect(self.console_button_clicked)
        self.serial_shakehand_button.clicked.connect(self.console_button_clicked)
        self.read_robot_state_button.clicked.connect(self.console_button_clicked)

        # 数据显示
        self.datashow_start_button.clicked.connect(self.datashow_start_button_clicked)
        self.datashow_stop_button.clicked.connect(self.datashow_stop_button_clicked)
        self.datashow_clear_button.clicked.connect(self.datashow_clear_button_clicked)
        self.datashow_stopstorage_button.clicked.connect(self.console_button_clicked)

        # 串口
        self.serial1_open_button.clicked.connect(self.serial1_open_button_clicked)
        self.serial1_close_button.clicked.connect(self.serial1_close_button_clicked)
        self.serial2_open_button.clicked.connect(self.serial2_open_button_clicked)
        self.serial2_close_button.clicked.connect(self.serial2_close_button_clicked)

        # Command Shell
        self.cmdshell_text_editor.returnPressed.connect(self.command_shell_backstage)

        # Checkbox
        ## IMU
        self.imu_checkbox.stateChanged.connect(self.imu_checkbox_ctl)
        self.imu1_checkbox.stateChanged.connect(self.imu1_checkbox_ctl)
        self.imu2_checkbox.stateChanged.connect(self.imu2_checkbox_ctl)
        self.accel_checkbox.stateChanged.connect(self.accel_checkbox_ctl)
        self.gyro_checkbox.stateChanged.connect(self.gyro_checkbox_ctl)
        self.angle_checkbox.stateChanged.connect(self.angle_checkbox_ctl)
        self.x_checkbox.stateChanged.connect(self.x_checkbox_ctl)
        self.y_checkbox.stateChanged.connect(self.y_checkbox_ctl)
        self.z_checkbox.stateChanged.connect(self.z_checkbox_ctl)
        ## 角度传感器
        self.anglesensor_checkbox.stateChanged.connect(self.anglesensor_checkbox_ctl)
        self.ang1_checkbox.stateChanged.connect(self.ang1_checkbox_ctl)
        self.ang2_checkbox.stateChanged.connect(self.ang2_checkbox_ctl)
        ## 深度传感器
        self.depthsensor_checkbox.stateChanged.connect(self.depthsensor_checkbox_ctl)
        self.depth_checkbox.stateChanged.connect(self.depth_checkbox_ctl)

    # 控制台按钮回调函数
    def console_button_clicked(self):
        """
        本函数为控制台按钮按下时,关联的槽函数
        每个控制台的按钮都对应了RFLink通讯协议中的一条Command,所以可以统一用一个函数来处理
        每当按钮按下时,串口将Command发送出去,发给机器人
        :return:
        """
        sender_button = self.sender()
        rftool.FRIEND_ID = rflink.BoxFishID[self.fishid_combo.currentText()].value
        cmd = rflink.Command[sender_button.objectName()].value
        if rflink.Command[sender_button.objectName()] is rflink.Command.SHAKING_HANDS:
            if rflink.BoxFishID[self.fishid_combo.currentText()] is rflink.BoxFishID.BOXFISH_ALL: # BoxFish_all
                cmd = rflink.Command.SYNCHRONIZE_CLOCK.value
            data = 0
        elif rflink.Command[sender_button.objectName()] is rflink.Command.SET_SINE_MOTION_AMP:
            data = (self.cpgcc_amp_edit.text()).encode('ascii')
        elif rflink.Command[sender_button.objectName()] is rflink.Command.SET_SINE_MOTION_FREQ:
            data = (self.cpgcc_freq_edit.text()).encode('ascii')
        elif rflink.Command[sender_button.objectName()] is rflink.Command.SET_SINE_MOTION_OFFSET:
            data = (self.cpgcc_offset_edit.text()).encode('ascii')
        elif rflink.Command[sender_button.objectName()] is rflink.Command.SET_DEPTHCTL_PARAM:
            data = struct.pack('<f', float(self.DCBW.depthctl_param_kp_edit.text())) + \
                struct.pack('<f', float(self.DCBW.depthctl_param_ki_edit.text())) + \
                struct.pack('<f', float(self.DCBW.depthctl_param_kd_edit.text()))
        else:
            data = 0

        # 数据打包
        datapack = rftool.RFLink_packdata(cmd, data)

        # 数据发送
        with QtCore.QMutexLocker(ser_mutex):
            try:
                send_sertool.write_cmd(datapack)
            except serial.serialutil.SerialException:
                self.statusBar().showMessage('串口未打开,无法发送')

    # Command Shell后端函数
    def command_shell_backstage(self):
        """
        本函数为Command Shell的后端函数
        每当输入命令栏,敲击回车键以后,会调用此函数
        :return:
        """
        # 获取用户输入的指令
        prefix = "<font color='Cyan'>boxfishstate-host:~$&nbsp;</font> "
        instr = self.cmdshell_text_editor.text()
        # self.cmdshell_text_editor.clear() # 清除编辑区的文字
        self.cmdshell_text_browser.append(prefix + instr)
        instrlist = instr.split()
        try:
            cmd = instrlist[0]
        except IndexError:
            return

        # 判断指令所属类型
        if cmd == "clear": # 清除Shell显示区
            self.cmdshell_text_browser.clear()
            self.cmdshell_text_browser.append(prefix)

        elif cmd == "help": # 打开帮助
            self.cmdshell_text_browser.append("<font color='DarkOrange'>Help&nbsp;Doc</font>")
            self.cmdshell_text_browser.append("<font color='DeepPink'>Basic&nbsp;operate&nbsp;commands&nbsp;including:</font>")
            self.cmdshell_text_browser.append("<font color='GreenYellow'>(1)&nbsp;ls</font>")
            self.cmdshell_text_browser.append("<font color='GreenYellow'>(1)&nbsp;clear</font>")
            self.cmdshell_text_browser.append("<font color='GreenYellow'>(2)&nbsp;help</font>")
            self.cmdshell_text_browser.append("<font color='GreenYellow'>(2)&nbsp;SET</font>")
            self.cmdshell_text_browser.append("<font color='GreenYellow'>(2)&nbsp;READ</font>")
            self.cmdshell_text_browser.append("<font color='GreenYellow'>(2)&nbsp;GOTO</font>")
            self.cmdshell_text_browser.append("<font color='DeepPink'>Commands&nbsp;consist&nbsp;of&nbsp;four&nbsp;categories,&nbsp;including:</font>")
            self.cmdshell_text_browser.append("<font color='GreenYellow'>(1)&nbsp;SHAKING_HANDS&nbsp;:&nbsp;build&nbsp;communication&nbsp;with&nbsp;slave</font>")
            self.cmdshell_text_browser.append("<font color='GreenYellow'>(2)&nbsp;SET&nbsp;cmd:&nbsp;set&nbsp;parameters&nbsp;of&nbsp;slave</font>")
            self.cmdshell_text_browser.append("<font color='GreenYellow'>(3)&nbsp;READ&nbsp;cmd:&nbsp;read&nbsp;parameters&nbsp;from&nbsp;slave</font>")
            self.cmdshell_text_browser.append("<font color='GreenYellow'>(4)&nbsp;GOTO&nbsp;cmd:&nbsp;goto&nbsp;execute&nbsp;behaviors&nbsp;of&nbsp;slave</font>")
            self.cmdshell_text_browser.append("<font color='DarkOrange'>Further&nbsp;explanation,&nbsp;please&nbsp;type&nbsp;'SET*'&nbsp;or&nbsp;'READ*'&nbsp;or&nbsp;'GOTO*'</font>")

        elif cmd == "SET": # 查询SET相关命令
            self.cmdshell_text_browser.append("<font color='DarkOrange'>" + "Usage&nbsp;:&nbsp;SET*&nbsp;[param1]&nbsp;[param2]&nbsp;..." + "</font>")
            self.cmdshell_text_browser.append("<font color='DeepPink'>" + "Example&nbsp;:&nbsp;SET_SINE_MOTION_AMP&nbsp;0.1" + "</font>")
            for i in range(33):
                self.cmdshell_text_browser.append("<font color='GreenYellow'>"+rflink.Command(i+2).name+"</font>")

        elif cmd == "READ": # 查询READ相关命令
            self.cmdshell_text_browser.append("<font color='DarkOrange'>" + "Usage&nbsp;:&nbsp;READ*" + "</font>")
            self.cmdshell_text_browser.append("<font color='DeepPink'>" + "Example&nbsp;:&nbsp;READ_ROBOT_STATUS" + "</font>")
            for i in range(15):
                self.cmdshell_text_browser.append("<font color='GreenYellow'>" + rflink.Command(i + 35).name + "</font>")

        elif cmd == "GOTO": # 查询GOTO相关命令
            self.cmdshell_text_browser.append("<font color='DarkOrange'>" + "Usage&nbsp;:&nbsp;GOTO*" + "</font>")
            self.cmdshell_text_browser.append("<font color='DeepPink'>" + "Example&nbsp;:&nbsp;GOTO_SEND_DATA" + "</font>")
            for i in range(4):
                self.cmdshell_text_browser.append("<font color='GreenYellow'>" + rflink.Command(i + 50).name + "</font>")

        elif cmd == "ls": # 显示下位机SD卡中的文件名
            # 发送一条读取文件列表的命令,等待下位机响应,并返回文件列表
            datapack = rftool.RFLink_packdata(rflink.Command.READ_FILE_LIST.value, None)
            with QtCore.QMutexLocker(ser_mutex):
                try:
                    send_sertool.write_cmd(datapack)
                except serial.serialutil.SerialException:
                    self.cmdshell_text_browser.append(
                        "<font color='red'>Warning&nbsp;:&nbsp;Serial&nbsp;port&nbsp;not&nbsp;open,&nbsp;false&nbsp;!</font>")


        elif cmd == "save":
            self.cmdshell_text_browser.append("<font color='orange'>(1)GOTO_STORAGE_DATA</font>")
            self.cmdshell_text_browser.append("<font color='orange'>(2)GOTO_SEND_DATA</font>")

        else: # 其他指令,也就是rflink中定义的指令
            if cmd in rflink.Command.__members__:
                self.cmdshell_text_browser.append("<font color='DodgerBlue'>Execute&nbsp;"+ instr + "</font>")
                # 如果是设置运动参数相关的命令
                if rflink.Command[cmd] is rflink.Command.SET_SINE_MOTION_AMP \
                    or rflink.Command[cmd] is rflink.Command.SET_SINE_MOTION_FREQ \
                    or rflink.Command[cmd] is rflink.Command.SET_SINE_MOTION_OFFSET:
                    try:
                        data = (instrlist[1]).encode('ascii')
                    except IndexError:
                        self.cmdshell_text_browser.append("<font color='red'>Error&nbsp;:&nbsp;Command&nbsp;parameters&nbsp;too&nbsp;be&nbsp;less!</font>")
                        self.cmdshell_text_browser.append(
                            "<font color='DarkOrange'>Usage&nbsp;:&nbsp;SET_SINE_MOTION_AMP&nbsp;[float]</font>")
                        return
                # 如果是设置是否读取数据文件相关的命令
                elif rflink.Command[cmd] is rflink.Command.GOTO_STORAGE_DATA \
                  or rflink.Command[cmd] is rflink.Command.GOTO_SEND_DATA:
                    try:
                        filenamelist = instrlist[2].split('.')
                        # 判断是不是bin文件
                        if filenamelist[1] != 'bin':
                            self.cmdshell_text_browser.append(
                                "<font color='DarkOrange'>Usage&nbsp;:&nbsp;GOTO_STORAGE_DATA&nbsp;[int]&nbsp;[(string).bin]</font>")
                            return

                        data = int(instrlist[1]).to_bytes(1,'big') + (instrlist[2]).encode('ascii')
                    except IndexError or ValueError:
                        self.cmdshell_text_browser.append("<font color='red'>Error&nbsp;:&nbsp;Command&nbsp;parameters&nbsp;error!</font>")
                        self.cmdshell_text_browser.append(
                            "<font color='DarkOrange'>Usage&nbsp;:&nbsp;GOTO_STORAGE_DATA&nbsp;[int]&nbsp;[(string).bin]</font>")
                        return


                # 打包成数据包,发送给下位机
                datapack = rftool.RFLink_packdata(rflink.Command[cmd].value, data)

                # self.cmdshell_text_browser.append(str(datapack))
                with QtCore.QMutexLocker(ser_mutex):
                    try:
                        send_sertool.write_cmd(datapack)
                    except serial.serialutil.SerialException:
                        self.cmdshell_text_browser.append(
                            "<font color='red'>Warning&nbsp;:&nbsp;Serial&nbsp;port&nbsp;not&nbsp;open,&nbsp;false&nbsp;!</font>")
            else:
                self.cmdshell_text_browser.append(
                    "<font color='red'>Warning&nbsp;:&nbsp;Command&nbsp;not&nbsp;found&nbsp;!&nbsp;Type&nbsp;'help'&nbsp;for&nbsp;detailed&nbsp;usages.</font>")

    # 有关数据显示的一系列按钮
    def datashow_start_button_clicked(self):
        """
        开始显示数据
        每当输入命令栏,敲击回车键以后,会调用此函数
        :return:
        """
        cmdvalue = None
        data = None
        # 判断传感器类型
        ### IMU
        if self.datashow_sensor_type == 1:
            # 判断传感器ID和数据类型
            if self.datashow_sensor_id == 1: # IMU1
                if self.datashow_sensor_datatype == 1:
                    cmdvalue = rflink.Command["READ_IMU1_ATTITUDE"].value
                elif self.datashow_sensor_datatype == 2:
                    cmdvalue = rflink.Command["READ_IMU1_ACCEL"].value
                elif self.datashow_sensor_datatype == 3:
                    cmdvalue = rflink.Command["READ_IMU1_GYRO"].value
                else:
                    self.statusBar().showMessage('未选定需要显示的数据')
                    return
            elif self.datashow_sensor_id == 2: # IMU2
                if self.datashow_sensor_datatype == 1:
                    cmdvalue = rflink.Command["READ_IMU2_ATTITUDE"].value
                elif self.datashow_sensor_datatype == 2:
                    cmdvalue = rflink.Command["READ_IMU2_ACCEL"].value
                elif self.datashow_sensor_datatype == 3:
                    cmdvalue = rflink.Command["READ_IMU2_GYRO"].value
                else:
                    self.statusBar().showMessage('未选定需要显示的数据')
                    return
            else:
                self.statusBar().showMessage('未选定需要显示的数据')
                return

            # 判断数据的轴向
            if self.datashow_sensor_dataaxis == 1:
                data = 1
            elif self.datashow_sensor_dataaxis == 2:
                data = 2
            elif self.datashow_sensor_dataaxis == 3:
                data = 3
            else:
                self.statusBar().showMessage('未选定需要显示的数据')
                return

        ### 云台角度传感器
        elif self.datashow_sensor_type == 3:
            if self.datashow_sensor_id == 1:
                cmdvalue = rflink.Command["READ_GIMBAL1_ANGLE"].value
            elif self.datashow_sensor_id == 2:
                cmdvalue = rflink.Command["READ_GIMBAL2_ANGLE"].value
            else:
                self.statusBar().showMessage('未选定需要显示的数据')
                return

        
        ### 深度传感器
        elif self.datashow_sensor_type == 5:
            if self.datashow_sensor_id == 1:
                cmdvalue = rflink.Command["READ_DEPTH"].value
            else:
                self.statusBar().showMessage('未选定需要显示的数据')
                return
        else:
            self.statusBar().showMessage('未选定需要显示的数据')
            return

        # 发送信号
        datapack = rftool.RFLink_packdata(cmdvalue, str(data).encode('ascii'))
        with QtCore.QMutexLocker(ser_mutex):
            try:
                send_sertool.write_cmd(datapack)
            except serial.serialutil.SerialException:
                self.statusBar().showMessage('串口未打开,无法发送')
                return
        self.datashow_running_flag = True
        ## 一旦开始显示数据,全部checkbox都会停止
        # IMU
        self.imu_checkbox.setEnabled(False)
        self.imu1_checkbox.setEnabled(False)
        self.imu2_checkbox.setEnabled(False)
        self.accel_checkbox.setEnabled(False)
        self.gyro_checkbox.setEnabled(False)
        self.angle_checkbox.setEnabled(False)
        self.x_checkbox.setEnabled(False)
        self.y_checkbox.setEnabled(False)
        self.z_checkbox.setEnabled(False)
        # 云台角度
        self.anglesensor_checkbox.setEnabled(False)
        self.ang1_checkbox.setEnabled(False)
        self.ang2_checkbox.setEnabled(False)
        # 深度传感器
        self.depthsensor_checkbox.setEnabled(False)
        self.depth_checkbox.setEnabled(False)


    def datashow_stop_button_clicked(self):
        datapack = rftool.RFLink_packdata(rflink.Command["SET_DATASHOW_OVER"].value, None)
        with QtCore.QMutexLocker(ser_mutex):
            try:
                send_sertool.write_cmd(datapack)
            except serial.serialutil.SerialException:
                self.statusBar().showMessage('串口未打开,无法发送')
                return
        self.datashow_running_flag = False

        ### 停止显示后使能Checkbox
        self.imu_checkbox.setEnabled(True)
        self.anglesensor_checkbox.setEnabled(True)
        self.depthsensor_checkbox.setEnabled(True)
        if self.datashow_sensor_type == 1:
            self.imu1_checkbox.setEnabled(True)
            self.imu2_checkbox.setEnabled(True)
            self.accel_checkbox.setEnabled(True)
            self.gyro_checkbox.setEnabled(True)
            self.angle_checkbox.setEnabled(True)
            self.x_checkbox.setEnabled(True)
            self.y_checkbox.setEnabled(True)
            self.z_checkbox.setEnabled(True)
        elif self.datashow_sensor_type == 3:
            self.ang1_checkbox.setEnabled(True)
            self.ang2_checkbox.setEnabled(True)
        elif self.datashow_sensor_type == 5:        # 深度传感器
            self.depth_checkbox.setEnabled(True)
        else:
            return

    def datashow_clear_button_clicked(self):
        if self.datashow_running_flag == False:
            # 停止绘制后的操作
            plt_mutex.lock()
            self.datalist = []
            self.timelist = []
            self.showtime = 0
            self.sensor_data_canvas.clear()
            plt_mutex.unlock()

    def datashow_save_button_clicked(self, filename):
        if self.datashow_running_flag == True:
            self.statusBar().showMessage('停止显示后,方可回传数据')
            self.SBBW.set_lineeditor_text('停止显示后,方可回传数据')
            return
        self.savefile_name = filename
        datapack = rftool.RFLink_packdata(rflink.Command["GOTO_SEND_DATA"].value, b'\x01'+filename.encode('ascii'))
        with QtCore.QMutexLocker(ser_mutex):
            try:
                send_sertool.write_cmd(datapack)
            except serial.serialutil.SerialException:
                self.statusBar().showMessage('串口未打开,无法发送')
                self.SBBW.set_lineeditor_text('串口未打开,无法发送')
            

    def datashow_storage_button_clicked(self, filename):
        datapack = rftool.RFLink_packdata(rflink.Command["GOTO_STORAGE_DATA"].value, b'\x01'+filename.encode('ascii'))
        with QtCore.QMutexLocker(ser_mutex):
            try:
                send_sertool.write_cmd(datapack)
                self.statusBar().showMessage('数据已开始储存')
            except serial.serialutil.SerialException:
                self.statusBar().showMessage('串口未打开,无法发送')

    # 有关串口开关的一系列按钮
    def serial1_open_button_clicked(self):
        """
        串口打开按钮对应的槽函数
        :return:
        """
        global send_sertool

        if(platform.system()=='Windows'):
            port = self.serial1_com_combo.currentText()
        elif(platform.system()=='Linux'):
            port = '/dev/'+self.serial1_com_combo.currentText()

        baud = int(self.serial1_bps_combo.currentText())
        try:
            send_sertool.init_serial(port,baud)
            self.statusBar().showMessage('发送串口已开启')
        except serial.serialutil.SerialException:
            self.statusBar().showMessage('该串口不存在')

    def serial1_close_button_clicked(self):
        """
        串口关闭对应的槽函数
        :return:
        """
        self.polling_state_thread.pause()
        send_sertool.close_serial()
        self.statusBar().showMessage('发送串口已关闭')

    def serial2_open_button_clicked(self):
        """
        接收串口打开按钮对应的槽函数
        :return:
        """
        global recv_sertool

        if(platform.system()=='Windows'):
            port = self.serial2_com_combo.currentText()
        elif(platform.system()=='Linux'):
            port = '/dev/'+self.serial2_com_combo.currentText()

        baud = int(self.serial2_bps_combo.currentText())
        try:
            recv_sertool.init_serial(port,baud)

            if self.receive_data_thread.is_running is False:
                self.receive_data_thread.start()
            else:
                self.receive_data_thread.resume()

            if self.analysis_data_thread.is_running is False:
                self.analysis_data_thread.start()
            else:
                self.analysis_data_thread.resume()

            self.statusBar().showMessage('接收串口已开启')
        except serial.serialutil.SerialException:
            self.statusBar().showMessage('该串口不存在')

    def serial2_close_button_clicked(self):
        """
        接收串口关闭对应的槽函数
        :return:
        """
        self.receive_data_thread.pause()
        self.analysis_data_thread.pause()
        recv_sertool.close_serial()
        self.statusBar().showMessage('接收串口已关闭')

    # 有关Check box 配置的一系列槽函数
    ## IMU部分
    def imu_checkbox_ctl(self):

        if self.imu_checkbox.isChecked():
            # IMU
            self.imu_checkbox.setChecked(True)
            self.imu1_checkbox.setEnabled(True)
            self.imu2_checkbox.setEnabled(True)
            self.accel_checkbox.setEnabled(True)
            self.gyro_checkbox.setEnabled(True)
            self.angle_checkbox.setEnabled(True)
            self.x_checkbox.setEnabled(True)
            self.y_checkbox.setEnabled(True)
            self.z_checkbox.setEnabled(True)

            # 云台角度
            self.anglesensor_checkbox.setChecked(False)
            self.ang1_checkbox.setEnabled(False)
            self.ang2_checkbox.setEnabled(False)
            # 深度传感器
            self.depthsensor_checkbox.setChecked(False)
            self.depth_checkbox.setEnabled(False)

            ## 刷新datashow状态变量
            ### datashow_sensor_type
            self.datashow_sensor_type = 1
            self.datashow_sensor_id = 0
            self.datashow_sensor_datatype = 0
            self.datashow_sensor_dataaxis = 0
            ### datashow_sensor_id
            if self.imu1_checkbox.isChecked():
                self.datashow_sensor_id = 1
            elif self.imu2_checkbox.isChecked():
                self.datashow_sensor_id = 2
            else:
                self.datashow_sensor_id = 0
            ### datashow_sensor_datatype
            if self.angle_checkbox.isChecked():
                self.datashow_sensor_datatype = 1
            elif self.accel_checkbox.isChecked():
                self.datashow_sensor_datatype = 2
            elif self.gyro_checkbox.isChecked():
                self.datashow_sensor_datatype = 3
            else:
                self.datashow_sensor_datatype = 0
            ### datashow_sensor_dataaxis
            if self.x_checkbox.isChecked():
                self.datashow_sensor_dataaxis = 1
            elif self.y_checkbox.isChecked():
                self.datashow_sensor_dataaxis = 2
            elif self.z_checkbox.isChecked():
                self.datashow_sensor_dataaxis = 3
            else:
                self.datashow_sensor_dataaxis = 0

    def imu1_checkbox_ctl(self):

        if self.imu1_checkbox.isChecked():
            self.imu2_checkbox.setChecked(False)
            self.datashow_sensor_type = 1
            self.datashow_sensor_id = 1

    def imu2_checkbox_ctl(self):

        if self.imu2_checkbox.isChecked():
            self.imu1_checkbox.setChecked(False)
            self.datashow_sensor_type = 1
            self.datashow_sensor_id = 2

    def accel_checkbox_ctl(self):

        if self.accel_checkbox.isChecked():
            self.gyro_checkbox.setChecked(False)
            self.angle_checkbox.setChecked(False)
            self.datashow_sensor_type = 1
            self.datashow_sensor_datatype = 2

    def gyro_checkbox_ctl(self):

        if self.gyro_checkbox.isChecked():
            self.accel_checkbox.setChecked(False)
            self.angle_checkbox.setChecked(False)
            self.datashow_sensor_type = 1
            self.datashow_sensor_datatype = 3

    def angle_checkbox_ctl(self):

        if self.angle_checkbox.isChecked():
            self.gyro_checkbox.setChecked(False)
            self.accel_checkbox.setChecked(False)
            self.datashow_sensor_type = 1
            self.datashow_sensor_datatype = 1

    def x_checkbox_ctl(self):

        if self.x_checkbox.isChecked():
            self.y_checkbox.setChecked(False)
            self.z_checkbox.setChecked(False)
            self.datashow_sensor_type = 1
            self.datashow_sensor_dataaxis = 1

    def y_checkbox_ctl(self):

        if self.y_checkbox.isChecked():
            self.x_checkbox.setChecked(False)
            self.z_checkbox.setChecked(False)
            self.datashow_sensor_type = 1
            self.datashow_sensor_dataaxis = 2

    def z_checkbox_ctl(self):

        if self.z_checkbox.isChecked():
            self.x_checkbox.setChecked(False)
            self.y_checkbox.setChecked(False)
            self.datashow_sensor_type = 1
            self.datashow_sensor_dataaxis = 3


    # 角度传感器部分
    def anglesensor_checkbox_ctl(self):

        if self.anglesensor_checkbox.isChecked():
            # IMU
            self.imu_checkbox.setChecked(False)
            self.imu1_checkbox.setEnabled(False)
            self.imu2_checkbox.setEnabled(False)
            self.accel_checkbox.setEnabled(False)
            self.gyro_checkbox.setEnabled(False)
            self.angle_checkbox.setEnabled(False)
            self.x_checkbox.setEnabled(False)
            self.y_checkbox.setEnabled(False)
            self.z_checkbox.setEnabled(False)
            # 云台角度
            self.anglesensor_checkbox.setChecked(True)
            self.ang1_checkbox.setEnabled(True)
            self.ang2_checkbox.setEnabled(True)
            # 深度传感器
            self.depthsensor_checkbox.setChecked(False)
            self.depth_checkbox.setEnabled(False)

            ## 刷新datashow状态变量
            ### datashow_sensor_type
            self.datashow_sensor_type = 3
            self.datashow_sensor_id = 0
            self.datashow_sensor_datatype = 0
            self.datashow_sensor_dataaxis = 0
            ### datashow_sensor_id
            if self.ang1_checkbox.isChecked():
                self.datashow_sensor_id = 1
            elif self.ang2_checkbox.isChecked():
                self.datashow_sensor_id = 2
            else:
                self.datashow_sensor_id = 0

    def ang1_checkbox_ctl(self):

        if self.ang1_checkbox.isChecked():
            self.ang2_checkbox.setChecked(False)
            self.datashow_sensor_type = 3
            self.datashow_sensor_id = 1

    def ang2_checkbox_ctl(self):

        if self.ang2_checkbox.isChecked():
            self.ang1_checkbox.setChecked(False)
            self.datashow_sensor_type = 3
            self.datashow_sensor_id = 2

    # 深度传感器部分
    def depthsensor_checkbox_ctl(self):
        if self.depthsensor_checkbox.isChecked():
            # IMU
            self.imu_checkbox.setChecked(False)
            self.imu1_checkbox.setEnabled(False)
            self.imu2_checkbox.setEnabled(False)
            self.accel_checkbox.setEnabled(False)
            self.gyro_checkbox.setEnabled(False)
            self.angle_checkbox.setEnabled(False)
            self.x_checkbox.setEnabled(False)
            self.y_checkbox.setEnabled(False)
            self.z_checkbox.setEnabled(False)

            # 云台角度
            self.anglesensor_checkbox.setChecked(False)
            self.ang1_checkbox.setEnabled(False)
            self.ang2_checkbox.setEnabled(False)
            # 深度传感器
            self.depthsensor_checkbox.setChecked(True)
            self.depth_checkbox.setEnabled(True)

            ## 刷新datashow状态变量
            ### datashow_sensor_type
            self.datashow_sensor_type = 5
            self.datashow_sensor_id = 0
            self.datashow_sensor_datatype = 0
            self.datashow_sensor_dataaxis = 0
            ### datashow_sensor_datatype
            if self.depth_checkbox.isChecked():
                self.datashow_sensor_id = 1
            else:
                self.datashow_sensor_id = 0

    def depth_checkbox_ctl(self):
        if self.depth_checkbox.isChecked():
            self.datashow_sensor_type = 5
            self.datashow_sensor_id = 1

    #####################################################################################################
    #####################################################################################################
    ## 第三部分:下位机数据处理,就一个函数
    #####################################################################################################
    #####################################################################################################
    def newdata_comming_slot(self,command_id):
        """
        窗口更新槽函数
        每当接收到来自AnalysisDataThread的Command的ID,开始刷新窗口界面
        :param command_id:接收的Command的ID
        :return:
        """
        global boxfishstate
        global rftool

        if rflink.Command(command_id) is rflink.Command.SHAKING_HANDS:
            # 握手成功,打开轮询线程,不要采用轮询的方式
            # if self.polling_state_thread.is_running is False:
            #     self.polling_state_thread.start()
            # else:
            #     self.polling_state_thread.resume()
            # 刷新cmdshell
            prefix = "<font color='red'>slave:~$&nbsp;</font> "
            self.cmdshell_text_browser.append(prefix + "Shaking&nbsp;hands&nbsp;succeed&nbsp;!")

        elif rflink.Command(command_id) is rflink.Command.READ_ROBOT_STATUS:
            # 更新状态栏
            rm_mutex.lock()
            pal = QtGui.QPalette()
            self.swimstate_label.setAutoFillBackground(True)
            if boxfishstate.swim_state is robotstate.SwimState.SWIM_FORCESTOP:
                self.swimstate_label.setText('停止')
                pal.setColor(QtGui.QPalette.WindowText, QtCore.Qt.red)
                self.swimstate_label.setPalette(pal)
            elif boxfishstate.swim_state is robotstate.SwimState.SWIM_STOP:
                self.swimstate_label.setText('暂停')
                pal.setColor(QtGui.QPalette.WindowText, QtCore.Qt.blue)
                self.swimstate_label.setPalette(pal)
            elif boxfishstate.swim_state is robotstate.SwimState.SWIM_RUN:
                self.swimstate_label.setText('运行')
                pal.setColor(QtGui.QPalette.WindowText, QtCore.Qt.green)
                self.swimstate_label.setPalette(pal)
            elif boxfishstate.swim_state is robotstate.SwimState.SWIM_INIT:
                self.swimstate_label.setText('初始化')
                pal.setColor(QtGui.QPalette.WindowText, QtCore.Qt.gray)
                self.swimstate_label.setPalette(pal)

            if boxfishstate.gimbal_state is robotstate.GimbalState.GIMBAL_STOP:
                self.GCBW.gimbalstate_label.setText('停止')
                pal.setColor(QtGui.QPalette.WindowText, QtCore.Qt.red)
                self.GCBW.gimbalstate_label.setPalette(pal)
            elif boxfishstate.gimbal_state is robotstate.GimbalState.GIMBAL_RUN:
                self.GCBW.gimbalstate_label.setText('运行')
                pal.setColor(QtGui.QPalette.WindowText, QtCore.Qt.green)
                self.GCBW.gimbalstate_label.setPalette(pal)
            elif boxfishstate.gimbal_state is robotstate.GimbalState.GIMBAL_ZERO:
                self.GCBW.gimbalstate_label.setText('归中')
                pal.setColor(QtGui.QPalette.WindowText, QtCore.Qt.yellow)
                self.GCBW.gimbalstate_label.setPalette(pal)

            rm_mutex.unlock()

        elif rflink.Command(command_id) is rflink.Command.READ_SINE_MOTION_PARAM:
            rm_mutex.lock()
            self.cpgamp_label.setText(str(round(boxfishstate.motion_amp,2)))
            self.cpgfreq_label.setText(str(round(boxfishstate.motion_freq,2)))
            self.cpgoffset_label.setText(str(round(boxfishstate.motion_offset,2)))
            rm_mutex.unlock()
            pal = QtGui.QPalette()
            pal.setColor(QtGui.QPalette.WindowText, QtCore.Qt.blue)
            self.cpgamp_label.setPalette(pal)
            self.cpgfreq_label.setPalette(pal)
            self.cpgoffset_label.setPalette(pal)

        elif command_id >= rflink.Command.READ_IMU1_ATTITUDE.value and \
            command_id <= rflink.Command.READ_DEPTH.value:

            rf_mutex.lock() 
            try:
                if rftool.length == 4:
                    showdata = struct.unpack('f', rftool.message[1:])[0]
                elif rftool.length == 2:
                    showdata = struct.unpack('H', rftool.message[1:])[0]
                else:
                    showdata = 0
            except:
                 showdata = 0
            rf_mutex.unlock()

            plt_mutex.lock()
            self.datalist.append(showdata)
            self.timelist.append(self.showtime)
            self.showtime = self.showtime + 1.0
            self.sensor_data_canvas.plot(self.timelist, self.datalist)
            if showdata > self.yaxis_upbound:
                self.yaxis_upbound = showdata + showdata*0.2
            elif showdata < self.yaxis_lowbound:
                self.yaxis_lowbound = showdata - abs(showdata) * 0.2
            self.sensor_data_canvas.set_ylim(self.yaxis_lowbound, self.yaxis_upbound)

            if len(self.datalist) > 100:
                self.timelist.pop(0)
                self.datalist.pop(0)
            plt_mutex.unlock()

        elif rflink.Command(command_id) is rflink.Command.PRINT_SYS_MSG:
            rf_mutex.lock()
            # 读取当前消息
            mes = rftool.message
            rf_mutex.unlock()
            # 刷新cmdshell
            self.cmdshell_text_browser.append("<font color='orange'>"+str(mes[1:],'ascii')+"</font>")

        # 记录数据到文件中
        elif rflink.Command(command_id) is rflink.Command.GOTO_SEND_DATA:
            # 读取当前消息
            rf_mutex.lock()
            mes = rftool.message
            meslen = rftool.length
            rf_mutex.unlock()
            
            if meslen == 1:
                if mes[1] == 1:
                    self.SBBW.set_lineeditor_text('回传中，请耐心等待~~~')
                    filename = 'data/' + self.savefile_name
                    # filename = 'data/'+time.strftime('%Y-%m-%d-%H-%M-%S',time.localtime(time.time()))+'.bin'
                    self.datafile = open(filename,'ab+')
                    prefix = "<font color='red'>slave:~$&nbsp;</font> "
                    self.cmdshell_text_browser.append(prefix + "Transfer Beginning!")
                elif mes[1] == 2:
                    filename = 'data/' + self.savefile_name
                    # filename = 'data/' + time.strftime('traindata-%Y-%m-%d-%H-%M-%S', time.localtime(time.time())) + '.bin'
                    self.datafile = open(filename, 'ab+')
                    prefix = "<font color='red'>slave:~$&nbsp;</font> "
                    self.cmdshell_text_browser.append(prefix + "Transfer Beginning!")
                elif mes[1] == 239:# mes[1]=b'\xef'
                    self.SBBW.set_lineeditor_text('回传成功！')
                    self.datafile.close()
                    prefix = "<font color='red'>slave:~$&nbsp;</font> "
                    self.cmdshell_text_browser.append(prefix + "Transfer Succeed!")
            else:
                self.datafile.write(mes[2:])

        elif rflink.Command(command_id) is not rflink.Command.LAST_COMMAND_FLAG:

            # 读取当前消息
            rf_mutex.lock()
            mes = rftool.message
            meslen = rftool.length
            rf_mutex.unlock()

            # 刷新cmdshell
            prefix = "<font color='red'>slave:~$&nbsp;</font> "
            self.cmdshell_text_browser.append(prefix + rflink.Command(command_id).name)
            # self.cmdshell_text_browser.append(str(mes))


        QtWidgets.QApplication.processEvents()





if __name__ == '__main__':
     # 创建QApplication对象是必须，管理整个程序，参数可有可无，有的话可接收命令行参数
    app = QtWidgets.QApplication(sys.argv) 

    # 创建窗体对象
    RRW = BoxFishWindow()  
    
    # 美化窗体对象
    with open('boxfishhost.qss') as f:
        qss = f.read()
    RRW.setStyleSheet(qss)

    #
    sys.exit(app.exec_())
