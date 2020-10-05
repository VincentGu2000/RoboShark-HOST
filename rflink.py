from enum import Enum

MY_ID = b'\x11'
FRIEND_ID = b'\x01'

# Recstate枚举类型
Recstate = Enum('Recstate',(\
	'WAITING_FF1',\
    'WAITING_FF2',\
	'SENDER_ID',\
	'RECEIVER_ID',\
	'RECEIVE_LEN_H',\
	'RECEIVE_LEN_L',\
	'RECEIVE_PACKAGE',\
	'RECEIVE_CHECK'))


# Command枚举类型
Command = Enum('Command',(\
    'SHAKING_HANDS',\
    'SET_SWIM_RUN',\
    'SET_SWIM_STOP',\
    'SET_SWIM_FORCESTOP',\
    'SET_SWIM_SPEEDUP',\
    'SET_SWIM_SPEEDDOWN',\
    'SET_SWIM_LEFT',\
    'SET_SWIM_RIGHT',\
    'SET_SWIM_STRAIGHT',\
    'SET_SWIM_UP',\
    'SET_SWIM_DOWN',\
    'SET_CPG_AMP',\
    'SET_CPG_FREQ',\
    'SET_CPG_OFFSET',\
    'SET_SINE_MOTION_AMP',\
	'SET_SINE_MOTION_FREQ',\
	'SET_SINE_MOTION_OFFSET',\
    'SET_LEFTPECFIN_UP',\
    'SET_LEFTPECFIN_ZERO',\
    'SET_LEFTPECFIN_DOWN',\
    'SET_RIGHTPECFIN_UP',\
    'SET_RIGHTPECFIN_ZERO',\
    'SET_RIGHTPECFIN_DOWN',\
    'SET_VALVE1_ON',\
    'SET_VALVE1_OFF',\
    'SET_VALVE2_ON',\
    'SET_VALVE2_OFF',\
    'SET_PUMP_ON',\
    'SET_PUMP_OFF',\
    'SET_PUMP_IN_ON',\
    'SET_PUMP_IN_OFF',\
    'SET_PUMP_OUT_ON',\
    'SET_PUMP_OUT_OFF',\
    'SET_GIMBAL_RUN',\
    'SET_GIMBAL_STOP',\
    'SET_GIMBAL_ZERO',\
    'SET_FLYWHEEL_RUN',\
    'SET_FLYWHEEL_STOP',\
    'SET_FLYWHEEL_DATA',\
    'SET_FLYWHEEL_CMD',\
    'SET_TARGET_POS',\
    'SET_DATASHOW_OVER',\
    'READ_ROBOT_STATUS',\
    'READ_CPG_PARAM',\
    'READ_SINE_MOTION_PARAM',\
    'READ_IMU1_ATTITUDE',\
    'READ_IMU1_ACCEL',\
    'READ_IMU1_GYRO',\
    'READ_IMU2_ATTITUDE',\
    'READ_IMU2_ACCEL',\
    'READ_IMU2_GYRO',\
    'READ_VARISTOR1_VAL',\
    'READ_VARISTOR2_VAL',\
    'READ_GIMBAL1_ANGLE',\
    'READ_GIMBAL2_ANGLE',\
    'READ_FLYWHEEL_ANGLE',\
    'READ_FLYWHEEL_VEL',\
    'READ_DEPTH',\
    'READ_FILE_LIST',\
    'GOTO_ATTACH',\
    'GOTO_DETACH',\
    'GOTO_STORAGE_DATA',\
    'GOTO_STOP_STORAGE',\
    'GOTO_SEND_DATA',\
    'PRINT_SYS_MSG',\
    'LAST_COMMAND_FLAG'))


class RFLink():
    """
    Robotic Fish 通讯协议类
    通讯协议规范:(一帧完整数据如下:)
    0xFF, 0xFF, SENDER_ID, RECEIVER_ID, MESSAGE_LEN_H, MESSAGE_LEN_L, COMMAND, MESSAGE, CHECKNUM

    :arg sender_id: 发送者ID
    :arg receiver_id: 接收者ID
    :arg length: 消息长度
    :arg message: 消息(byte类型)

    :attributes RFLink_receivedata:接收状态机,解码RFLink通讯协议
    :attributes RFLink_packdata:将待发送数据按RFLink通讯协议打包
    """
    def __init__(self):
        self.sender_id = b''
        self.receiver_id = b''
        self.length = 0
        self.message = b''
        self._receive_state = Recstate.WAITING_FF1
        self._checksum = 0
        self._byte_count = 0
        


    def RFLink_receivedata(self, rx_data):
        """
        RFLink接收状态机
        :param rx_data: 串口接收到的数据
        :return: 当接收到一帧完整数据时,返回1;否则,返回0.
        """

        if self._receive_state==Recstate.WAITING_FF1:
            if rx_data==b'\xff':
                self._receive_state = Recstate.WAITING_FF2
                self._checksum = ord(rx_data)
                self.message = b''
                self.length = 0
                self._byte_count = 0

        elif self._receive_state==Recstate.WAITING_FF2:
            if rx_data == b'\xff':
                self._receive_state = Recstate.SENDER_ID
                self._checksum += ord(rx_data)
            else:
                self._receive_state = Recstate.WAITING_FF1

        elif self._receive_state==Recstate.SENDER_ID:
            if rx_data == FRIEND_ID:
                self._receive_state = Recstate.RECEIVER_ID
                self._checksum += ord(rx_data)
            else:
                self._receive_state = Recstate.WAITING_FF1

        elif self._receive_state==Recstate.RECEIVER_ID:
            if rx_data == MY_ID:
                self._receive_state = Recstate.RECEIVE_LEN_H
                self._checksum += ord(rx_data)
            else:
                self._receive_state = Recstate.WAITING_FF1

        elif self._receive_state==Recstate.RECEIVE_LEN_H:
            self._receive_state = Recstate.RECEIVE_LEN_L
            self._checksum = self._checksum + ord(rx_data)
            self.length = ord(rx_data)*256

        elif self._receive_state == Recstate.RECEIVE_LEN_L:
            self._receive_state = Recstate.RECEIVE_PACKAGE
            self._checksum += ord(rx_data)
            self.length += ord(rx_data)

        elif self._receive_state == Recstate.RECEIVE_PACKAGE:
            self._checksum += ord(rx_data)
            self.message = self.message + rx_data
            self._byte_count += 1
            if self._byte_count > self.length:
                self._receive_state = Recstate.RECEIVE_CHECK
                self._checksum  = self._checksum % 255

        elif self._receive_state == Recstate.RECEIVE_CHECK:
            if rx_data == self._checksum.to_bytes(1,'big'):
                self._checksum = 0
                self._receive_state = Recstate.WAITING_FF1
                return 1
            else:
                self._receive_state = Recstate.WAITING_FF1


        else:
            self._receive_state = Recstate.WAITING_FF1

        return 0



    def RFLink_packdata(self, cmd, databyte):
        """
        RFLink数据与指令打包函数
        :param cmd:Command
        :param data:待发送数据
        :return:符合RFLink通讯协议的消息包
        """
        first_byte = b'\xff'
        second_byte = b'\xff'
        third_bye = MY_ID
        fourth_byte = FRIEND_ID

        cmdbyte = cmd.to_bytes(1,'big')
        if databyte != 0 and databyte is not None:
            datalenbyte = len(databyte).to_bytes(2,'big')
        else:
            databyte = b''
            datalenbyte = b'\x00\x00'

        check_num = ord(first_byte) + ord(second_byte) + ord(third_bye) + ord(fourth_byte)
        check_num = check_num + datalenbyte[0] + datalenbyte[1] + ord(cmdbyte)
        for data in databyte:
            check_num = check_num + data
        check_num = (check_num%255).to_bytes(1,'big')

        datapack = first_byte + second_byte + third_bye + fourth_byte + datalenbyte + cmdbyte + databyte + check_num

        return datapack





if __name__ == "__main__":
    #print(RFLink_packdata(Command.SET_CPG_AMP.value,0.0))
    # rf = RFLink()
    # rf.RFLink_receivedata(b'\xff')
    # rf.RFLink_receivedata(b'\xff')
    # rf.RFLink_receivedata(b'\x01')
    # rf.RFLink_receivedata(b'\x11')
    # rf.RFLink_receivedata(b'\x00')
    # rf.RFLink_receivedata(b'\x00')
    # rf.RFLink_receivedata(b'\x01')
    # print(rf.RFLink_receivedata(b'\x13'))
    print(Command(1))

































