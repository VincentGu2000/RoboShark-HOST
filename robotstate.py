# python3
# @Time    : 2021.05.18
# @Author  : 张鹏飞
# @FileName: robotstate.py
# @Software: 机器鲨鱼上位机

from enum import Enum

class AutoCTL(Enum):
    AutoCTL_RUN = 0
    AutoCTL_STOP = 1


class SwimState(Enum):
    """
    游动状态枚举类型
    """
    SWIM_FORCESTOP = 0
    SWIM_STOP = 1
    SWIM_RUN = 2
    SWIM_INIT = 3


class GimbalState(Enum):
    """
    云台状态枚举类型
    """
    GIMBAL_STOP = 0
    GIMBAL_ZERO = 1
    GIMBAL_RUN = 2


class RobotState:
    """
    机器人状态类
    """
    def __init__(self):
        self.autoctl_state = AutoCTL.AutoCTL_STOP.value
        self.swim_state = SwimState.SWIM_FORCESTOP.value
        self.gimbal_state = GimbalState.GIMBAL_STOP.value
        self.water_state = 0
        self.motion_amp = 0.0
        self.motion_freq = 0.0
        self.motion_offset = 0.0
        self.pecfin_angle = 0.0
        self.onboard_imu_roll = 0.0
        self.onboard_imu_pitch = 0.0
        self.onboard_imu_yaw = 0.0
        self.onboard_imu_accelx = 0.0
        self.onboard_imu_accely = 0.0
        self.onboard_imu_accelz = 0.0
        self.onboard_imu_gyrox = 0.0
        self.onboard_imu_gyroy = 0.0
        self.onboard_imu_gyroz = 0.0
        self.gimbal_imu_roll = 0.0
        self.gimbal_imu_pitch = 0.0
        self.gimbal_imu_yaw = 0.0
        self.gimbal_imu_accelx = 0.0
        self.gimbal_imu_accely = 0.0
        self.gimbal_imu_accelz = 0.0
        self.gimbal_imu_gyrox = 0.0
        self.gimbal_imu_gyroy = 0.0
        self.gimbal_imu_gyroz = 0.0
        self.infrared_ahead_switch = 0
        self.infrared_left_switch = 0
        self.infrared_right_switch = 0
        self.infrared_down_distance = 0

