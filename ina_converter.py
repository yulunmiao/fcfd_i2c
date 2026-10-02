"""Convert INA219 measurement register bytes into SI values."""

from typing import Union


RegisterBytes = Union[bytes, bytearray]


def _word_from_bytes(register_bytes: RegisterBytes, *, signed: bool = False) -> int:
	if len(register_bytes) != 2:
		raise ValueError("INA219 register data must contain exactly two bytes")
	return int.from_bytes(register_bytes, byteorder="little", signed=signed)


def convert_shunt_voltage(shunt_voltage_bytes: RegisterBytes) -> float:
	"""Convert the signed shunt voltage register to volts."""
	shunt_voltage_lsb = 10e-6
	return _word_from_bytes(shunt_voltage_bytes, signed=True) * shunt_voltage_lsb


def convert_bus_voltage(bus_voltage_bytes: RegisterBytes) -> float:
	"""Convert the bus voltage register to volts."""
	bus_voltage_word = _word_from_bytes(bus_voltage_bytes)
	bus_voltage_lsb = 4e-3
	return ((bus_voltage_word >> 3) & 0x1FFF) * bus_voltage_lsb


def convert_current(current_bytes: RegisterBytes, current_lsb: float) -> float:
	"""Convert the signed current register to amperes."""
	if current_lsb <= 0:
		raise ValueError("current_lsb must be greater than zero")
	return _word_from_bytes(current_bytes, signed=True) * current_lsb


def convert_power(power_bytes: RegisterBytes, current_lsb: float) -> float:
	"""Convert the power register to watts using 20 * current_lsb per bit."""
	if current_lsb <= 0:
		raise ValueError("current_lsb must be greater than zero")
	return _word_from_bytes(power_bytes) * (20 * current_lsb)
