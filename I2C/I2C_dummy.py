"""In-memory I2C transport for development and testing without hardware."""

import json
from typing import Tuple

from I2C.I2C import I2C

class I2C_dummy(I2C):
	"""
	I2C device represented by a bytearray of register values. 
	The registers can be initialized from the JSON file of regmap.
	"""

	ERROR_CODES = {
		0x00: "No error",
		0x01: "Address not Acknowledged",
	}
	def __init__(self, json_file: str):
		with open(json_file, 'r') as f:
			regmap = json.load(f)
		# get the biggest register address from the regmap to determine the size of the register list
		max_address = max(
			properties["address"][-1]
			for properties in regmap.values()
			if isinstance(properties, dict)
		)
		self.registers: bytearray = bytearray([0] * (max_address + 1))
		self._registers_by_board: dict[int, bytearray] = {}
		for properties in regmap.values():
			if not isinstance(properties, dict):
				continue
			lsa = properties["address"][0]
			msa = properties["address"][-1]
			byte_width = msa - lsa +1 
			
			per_byte_ranges = self._per_byte_ranges(properties["bit_range"], byte_width)
			default = properties.get("default", 0)
			if default == "N/A":
				continue

			if isinstance(default, list):
				default_values = default
			else:
				default_values = [default] * byte_width

			for i, range in enumerate(per_byte_ranges):
				msb = range[-1]
				lsb = range[0]
				mask = ((1 << (msb - lsb + 1)) - 1) << lsb
				current = self.registers[i+lsa]
				default_value = default_values[i] if i < len(default_values) else default_values[-1]
				new_value = (current & ~mask) | ((default_value << lsb) & mask)
				self.registers[i+lsa] = new_value

	def _per_byte_ranges(self, bit_range, byte_width):
		# Normalize bit_range into a list of [lsb, msb], one per byte in the register
		# Used by both read and write
		# For a field that covers a single byte
		if byte_width == 1:
			# For a field that covers one bit on one byte
			return [[bit_range[0], bit_range[-1]]]
		# For a field that covers multiple bytes
		if all(isinstance(entry, list) for entry in bit_range):
			# For a field of multiple bytes where the bit_range is a list of lists eg 'hit_trig_bcid'
			return [[entry[0], entry[-1]] for entry in bit_range]
		# For a field of multiple bytes where each byte uses the same range of bits eg 'ch5_TDC_data'
		return [[bit_range[0], bit_range[-1]]] * byte_width
	
	def _valid_range(self, address: int, n: int) -> bool:
		"""Return whether an address and length fit inside the register list."""
		return (
			isinstance(address, int)
			and isinstance(n, int)
			and address >= 0
			and n >= 0
			and address + n <= len(self.registers)
		)

	def read(self, board_address: int, address: int, n: int) -> Tuple[int, bytes]:
		"""Read ``n`` contiguous values from the register list."""
		if not self._valid_range(address, n):
			return 0x01, bytes()
		registers = self._registers_by_board.setdefault(
			board_address, bytearray(self.registers)
		)
		return 0x00, bytes(registers[address:address + n])


	def write(self, board_address: int, address: int, data) -> bool:
		"""Write contiguous byte values into the register list."""
		values = bytes([data]) if isinstance(data, int) else bytes(data)
		if not self._valid_range(address, len(values)):
			return False
		registers = self._registers_by_board.setdefault(
			board_address, bytearray(self.registers)
		)
		registers[address:address + len(values)] = values
		return True

	def get_number_of_devices(self) -> int:
		return 1

	def describe_error(self, code: int) -> str:
		return self.ERROR_CODES.get(code, f"Unknown error code 0x{code:02X}")
