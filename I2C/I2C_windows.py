"""Windows I2C transport backed by the USB-to-I2C Professional DLL."""

import ctypes
from typing import Optional, Tuple

from I2C.I2C import I2C


class I2C_windows(I2C):
	"""Manage one USB-to-I2C Professional DLL connection."""

	ERROR_CODES = {
		0x00: "No error",
		0x01: "Address not Acknowledged",
		0x02: "Data not Acknowledged",
		0x07: "Arbitration lost",
		0x08: "I2C Time Out",
		0x09: "I2C Time Out with no START condition (check bus / pull-ups)",
		0x0A: "Transmission aborted",
		0x0B: "Message sent but a Nack was encountered",
		0x80: "Unsupported function (check firmware version)",
		0xFF: "Hardware not detected or USB error",
	}

	def __init__(self, dll_name: str = "USBtoI2Cpro.dll"):
		self.dll = ctypes.WinDLL(dll_name)
		self._configure_dll()

	def _configure_dll(self) -> None:
		self.dll.GetFirmwareRevision.argtypes = []
		self.dll.GetFirmwareRevision.restype = ctypes.c_ubyte
		self.dll.GetNumberOfDevices.argtypes = []
		self.dll.GetNumberOfDevices.restype = ctypes.c_int
		self.dll.SetI2CFrequency.argtypes = [ctypes.c_int]
		self.dll.SetI2CFrequency.restype = ctypes.c_int
		self.dll.GetI2CFrequency.argtypes = []
		self.dll.GetI2CFrequency.restype = ctypes.c_int
		self.dll.I2CReadArrayDB.argtypes = [
			ctypes.c_ubyte,
			ctypes.c_ubyte,
			ctypes.c_ubyte,
			ctypes.c_short,
			ctypes.POINTER(ctypes.c_ubyte),
		]
		self.dll.I2CReadArrayDB.restype = ctypes.c_ubyte
		self.dll.I2CWriteArrayDB.argtypes = [
			ctypes.c_ubyte,
			ctypes.c_ubyte,
			ctypes.c_ubyte,
			ctypes.c_short,
			ctypes.POINTER(ctypes.c_ubyte),
		]
		self.dll.I2CWriteArrayDB.restype = ctypes.c_ubyte
		self.dll.ShutdownProcedure.argtypes = []
		self.dll.ShutdownProcedure.restype = None

	def describe_error(self, code: int) -> str:
		return self.ERROR_CODES.get(code, f"Unknown error code 0x{code:02X}")

	def get_number_of_devices(self) -> int:
		return self.dll.GetNumberOfDevices()

	def read(self, board_address: int, address: int, n: int) -> Tuple[int, Optional[bytes]]:
		"""Read ``n`` bytes starting at a two-byte register address."""
		buf = (ctypes.c_ubyte * n)()
		sa_high = (address >> 8) & 0xFF
		sa_low = address & 0xFF

		err = self.dll.I2CReadArrayDB(board_address, sa_high, sa_low, n, buf)
		if err != 0x00:
			print(f"Read error on register {address}: {self.describe_error(err)}")
			return err, None
		return 0, bytes(buf)

	def write(self, board_address: int, address: int, data) -> bool:
		"""Write an integer or byte-like payload to a two-byte register address."""
		if isinstance(data, int):
			data = bytes([data])
		else:
			data = bytes(data)
		n = len(data)
		buf = (ctypes.c_ubyte * n)(*data)
		sa_high = (address >> 8) & 0xFF
		sa_low = address & 0xFF

		err = self.dll.I2CWriteArrayDB(board_address, sa_high, sa_low, n, buf)
		if err != 0x00:
			print(f"Write error on register {address}: {self.describe_error(err)}")
			return False
		return True
