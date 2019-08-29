from commRaspMain import PiBot as PiBotBase
from abc import ABC as AbstractBaseClass
from abc import abstractmethod
import time
import os


# TODO:
# Käpitsate kalibreerimine
# IMU

class SensorConverter(AbstractBaseClass):
    @abstractmethod
    def get(self, x: int) -> float:
        pass

    @staticmethod
    def make_converters(file_path: str) -> list:
        """
            2
        1      3

        4      9
         5    8
          6  7
        :param file_path: File path
        :return: 3 * [SensorConverter]
        """
        converters = []

        with open(file_path, 'r', encoding="utf-8-sig") as file:
            converters.append(EncoderConverter(float(file.readline().strip())))
            try:
                line = file.readline()
                order, open_, down, closed, up = map(int, line.split())
            except:
                order, open_, down, closed, up = 1, 33, 39, 23, 29
            converters.append(GrabberHeightConverter(order, up, down))
            converters.append(GrabberCloseConverter(order, closed, open_))

        return converters


class GrabberHeightConverter(SensorConverter):
    def __init__(self, order, down, up):
        self.right_order = bool(order)
        self.up = up
        self.down = down

    def get(self, percentage: int):
        slope = (self.down - self.up) / 100
        y = slope * (percentage - 100) + self.down
        return y


class GrabberCloseConverter(SensorConverter):
    def __init__(self, order, open, closed):
        self.right_order = bool(order)
        self.open = open
        self.closed = closed

    def get(self, percentage: int):
        slope = (self.open - self.closed) / 100
        y = slope * (percentage - 100) + self.open
        return y


class EncoderConverter(SensorConverter):
    def __init__(self, degree_per_tick):
        self.degree_per_tick = degree_per_tick

    def get(self, x: int):
        return -self.degree_per_tick * int(x)


class Validator:
    @staticmethod
    def _get_validate_percentage_arg_function(name: str, start: int, end: int):
        def validate_percentage_arg(i: int):
            def validate_percentage_wrapper(function):
                def validate_percentage(*args):
                    percentage = args[i]
                    if not start <= percentage <= end:
                        raise ValueError(f"{name.capitalize()} percentage must be in range {start} .. {end}")
                    return function(*args)

                return validate_percentage

            return validate_percentage_wrapper

        return validate_percentage_arg

    @staticmethod
    def _validate_grabber_percentage_arg(nth_arg: int):
        return Validator._get_validate_percentage_arg_function("grabber", 0, 100)(nth_arg)

    @staticmethod
    def _validate_speed_percentage_arg(nth_arg: int):
        return Validator._get_validate_percentage_arg_function("speed", -99, 99)(nth_arg)

    @staticmethod
    def validate_speed_percentage(speed_function):
        return Validator._validate_speed_percentage_arg(1)(speed_function)

    @staticmethod
    def validate_grabber_percentage(grb_function):
        return Validator._validate_grabber_percentage_arg(1)(grb_function)


class PiBot(PiBotBase):
    def __init__(self):
        super().__init__()

        # Read robot number
        robot_nr = int(os.environ["ROBOT_ID"])

        # Converters
        self.converters = SensorConverter.make_converters("converters{}.txt".format(robot_nr))
        self.encoder_converter, \
        self.grabber_height_converter, \
        self.grabber_close_converter = self.converters

        # Initialize robot
        self.initialize_robot()
        self.servo_enabled = True

        # Initialize timestamps
        self.first_block_last_update = 0
        self.second_block_last_update = 0
        self.sensors_last_update = 0
        self.tof_sensors_last_update = 0

        # Constants
        self.UPDATE_TIME = 0.005
        self.WHEEL_DIAMETER = 0.03
        self.AXIS_LENGTH = 0.14

    def initialize_robot(self):
        self._motors_enable()
        self._encoders_enable()
        self._servo_enable()
        self.set_grabber_height(50)
        self.close_grabber(50)
        self._adc_conf(3)

    def is_simulation(self):
        return False

    def set_update_time(self, update_time):
        self.UPDATE_TIME = update_time

    @staticmethod
    def _values_correct(array, start, end) -> bool:
        for value in enumerate(array[start:end]):
            if value is None:
                return False
        return True

    def _sensor_values_correct(self, start: int = 0, end: int = 15) -> bool:
        return PiBot._values_correct(self.sensor, start, end)

    def _tof_values_correct(self) -> bool:
        return PiBot._values_correct(self.tof_values, 0, 3)

    def _update_first_sensor_block(self):
        timestamp = time.time()
        if timestamp - self.first_block_last_update >= self.UPDATE_TIME:
            self._adc_read(1)
            while not self._sensor_values_correct(0, 8):
                self._adc_read(1)
            self.first_block_last_update = timestamp

    def _update_second_sensor_block(self):
        timestamp = time.time()
        if timestamp - self.second_block_last_update >= self.UPDATE_TIME:
            self._adc_read(2)
            while not self._sensor_values_correct(8, 15):
                self._adc_read(2)
            self.second_block_last_update = timestamp

    def _update_sensors(self):
        timestamp = time.time()
        if timestamp - self.sensors_last_update >= self.UPDATE_TIME:
            self._adc_read()
            while not self._sensor_values_correct():
                self._adc_read()
            self.sensors_last_update = timestamp

    def _update_sensor_block(self, block_nr):
        if block_nr == 1:
            self._update_first_sensor_block()
        elif block_nr == 2:
            self._update_second_sensor_block()
        else:
            self._update_sensors()

    def _update_tof_sensors(self):
        timestamp = time.time()
        if timestamp - self.tof_sensors_last_update >= self.UPDATE_TIME:
            self._tof_read()
            while not self._tof_values_correct():
                self._tof_read()
            self.tof_sensors_last_update = timestamp

    def _update_imu(self):
        timestamp = time.time()
        if timestamp - self.imu_last_update >= self.UPDATE_TIME:
            self._imu_read_gyro()
            self._imu_read_compass()
            self.imu_last_update = timestamp

    def _get_value_or_none_from_converter(self, converter, sensor_value, sensor_block_nr):
        try:
            value = converter.get(sensor_value)
        except OverflowError:
            value = None
            self._adc_read(sensor_block_nr)
        return value

    def _get_value_from_converter(self, converter, sensor_index, sensor_block_nr):
        value = None
        self._update_sensor_block(sensor_block_nr)
        while value is None:
            value = self._get_value_or_none_from_converter(converter, self.sensor[sensor_index], sensor_block_nr)
        return value

    def _get_value(self, sensor_index, sensor_block_nr):
        self._update_sensor_block(sensor_block_nr)
        return self.sensor[sensor_index]

    def _get_front_ir_value(self, index: int):
        self._update_tof_sensors()
        return self.tof_values[index]

    def get_front_left_ir(self) -> float:
        return self._get_front_ir_value(0)

    def get_front_middle_ir(self) -> float:
        return self._get_front_ir_value(1)

    def get_front_right_ir(self) -> float:
        return self._get_front_ir_value(2)

    def get_front_irs(self) -> [float]:
        return [self.get_front_left_ir(), self.get_front_middle_ir(), self.get_front_right_ir()]

    def get_rear_left_straight_ir(self) -> float:
        return self._get_value(2, 1)

    def get_rear_left_diagonal_ir(self) -> float:
        return self._get_value(1, 1)

    def get_rear_left_side_ir(self) -> float:
        return self._get_value(0, 1)

    def get_rear_right_straight_ir(self) -> float:
        return self._get_value(3, 1)

    def get_rear_right_diagonal_ir(self) -> float:
        return self._get_value(4, 1)

    def get_rear_right_side_ir(self) -> float:
        return self._get_value(5, 1)

    def get_rear_irs(self) -> [float]:
        return [
            self.get_rear_left_side_ir(), self.get_rear_left_diagonal_ir(), self.get_rear_left_straight_ir(),
            self.get_rear_right_straight_ir(), self.get_rear_right_diagonal_ir(), self.get_rear_right_side_ir()
        ]

    def get_irs(self) -> [float]:
        return self.get_front_irs() + self.get_rear_irs()

    def get_leftmost_line_sensor(self) -> int:
        return self._get_value(8, 2)

    def get_second_line_sensor_from_left(self) -> int:
        return self._get_value(9, 2)

    def get_third_line_sensor_from_left(self) -> int:
        return self._get_value(10, 2)

    def get_rightmost_line_sensor(self) -> int:
        return self._get_value(13, 2)

    def get_second_line_sensor_from_right(self) -> int:
        return self._get_value(12, 2)

    def get_third_line_sensor_from_right(self) -> int:
        return self._get_value(11, 2)

    def get_left_line_sensors(self):
        return [self.get_leftmost_line_sensor(), self.get_second_line_sensor_from_left(),
                self.get_third_line_sensor_from_left()]

    def get_right_line_sensors(self):
        return [self.get_rightmost_line_sensor(), self.get_second_line_sensor_from_right(),
                self.get_third_line_sensor_from_right()]

    def get_line_sensors(self):
        return self.get_left_line_sensors() + self.get_right_line_sensors()

    @Validator.validate_speed_percentage
    def set_left_wheel_speed(self, percentage: int):
        """
        :param percentage: -99 .. 99
        """
        self._motorR_set(percentage)

    @Validator.validate_speed_percentage
    def set_right_wheel_speed(self, percentage: int):
        """
        :param percentage: -99 .. 99
        """
        self._motorL_set(percentage)

    @Validator.validate_speed_percentage
    def set_wheels_speed(self, percentage: int):
        """
        :param percentage: -99 .. 99
        """
        self._motorB_set(percentage)

    def _update_encoders(self):
        while not self._encoders_get() or any(map(lambda encoder: encoder is None, self.encoder)):
            pass

    def get_right_wheel_encoder(self) -> int:
        self._update_encoders()
        return self.encoder_converter.get(self.encoder[0])

    def get_left_wheel_encoder(self) -> int:
        self._update_encoders()
        return self.encoder_converter.get(self.encoder[1])

    def get_gyro(self):
        self._update_imu()
        return self.gyro

    def get_compass(self):
        self._update_imu()
        return self.compass

    def _enable_servo_if_not(self):
        if not self.servo_enabled:
            self._servo_enable()

    @Validator.validate_grabber_percentage
    def set_grabber_height(self, height_percentage):
        """
        :param height: 0 .. 100
        """
        y = self.grabber_height_converter.get(height_percentage)
        if self.grabber_height_converter.right_order:
            self._servo_two_set(y)
        else:
            self._servo_one_set(y)

    @Validator.validate_grabber_percentage
    def close_grabber(self, percentage):
        """
        :param percentage: 0 .. 100
        """
        y = self.grabber_close_converter.get(percentage)
        if self.grabber_close_converter.right_order:
            self._servo_one_set(y)
        else:
            self._servo_two_set(y)
