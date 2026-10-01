import json
import logging
from enum import Enum
from itertools import product
from typing import List, Optional, Tuple

from I2C.I2C import I2C


class I2CRegisterMap:
    """Register map abstraction backed by one I2C transport."""

    class access_type(Enum):
        READ_ONLY = 0
        WRITE_ONLY = 1
        READ_WRITE = 2

    def __init__(self, json_file: str, i2c: I2C):
        self.i2c = i2c

        self._registers = {}
        with open(json_file, 'r') as f:
            input_json = json.load(f)

        for register, properties in input_json.items():
            if not isinstance(properties, dict):
                continue

            access_type_str = properties['access'].lower()
            access_types = {
                'ro': self.access_type.READ_ONLY,
                'wo': self.access_type.WRITE_ONLY,
                'rw': self.access_type.READ_WRITE,
            }
            try:
                access_type = access_types[access_type_str]
            except KeyError as error:
                raise ValueError(
                    f"Unsupported access type {access_type_str!r} for {register!r}"
                )

            address = properties['address']
            if (not isinstance(address, list)
                or (len(address) != 1 and len(address) != 2)):
                raise ValueError(
                    f"Address for {register!r} must be list of 1 or 2 elements, currently being {address}"
                )

            lsa = address[0]
            msa = address[-1]
            byte_width = msa - lsa + 1
            bit_range = properties['bit_range']
            if len(address) == 1:
                if (not isinstance(bit_range, list)
                    or (len(bit_range) != 1 and len(bit_range) != 2)):
                    raise TypeError(
                        f"Bit_range for {register!r} must be a list of 1 or 2 elements, currently being {bit_range}"
                    )
            else:
                if not isinstance(bit_range, list) or (
                    len(bit_range) != 2 and not (
                        all(isinstance(entry, list) and len(entry) == 2 for entry in bit_range)
                    )
                ):
                    raise TypeError(
                        f"Bit_range for {register!r} must be a 1-d list of 2 elements or a 2-d list [n][2] for a multi-byte register, currently being {bit_range}"
                    )
            bit_range = self._per_byte_ranges(bit_range, byte_width)

            if properties['default'] == 'N/A':
                default = None
            elif isinstance(properties['default'], int):
                default = properties['default']
            elif isinstance(properties['default'], list) and all(
                isinstance(item, int) for item in properties['default']
            ):
                default = properties['default']
            else:
                raise TypeError(
                    f"Default for {register!r} must be 'N/A', an integer, or a list of integers"
                )
            self._registers[register] = {
                'address': address,
                'bit_range': bit_range,
                'access': access_type,
                'default': default,
            }

    def _per_byte_ranges(self, bit_range: List, byte_width: int) -> List[List[int]]:
        if byte_width == 1:
            return [[bit_range[0], bit_range[-1]]]
        if all(isinstance(entry, list) for entry in bit_range):
            return [[entry[0], entry[-1]] for entry in bit_range]
        return [[bit_range[0], bit_range[-1]]] * byte_width

    def write(self, device_address: int, register: str, value: bytearray = bytearray()) -> bool:
        if register not in self._registers:
            logging.debug(f"[I2CRegisterMap.write] Unknown register: {register!r} on board {device_address}")
            return False

        properties = self._registers[register]
        if properties['access'] == self.access_type.READ_ONLY:
            logging.debug(f"[I2CRegisterMap.write] Register {register!r} is read-only on board {device_address}")
            return False

        lsa = properties["address"][0]
        msa = properties["address"][-1]
        byte_width = msa - lsa + 1

        if len(value) != byte_width:
            logging.debug(f"[I2CRegisterMap.write] Mismatch in register size and value size on board {device_address}")
            logging.debug(f"[I2CRegisterMap.write] Register {register} has {byte_width} bytes on board {device_address}")
            logging.debug(f"[I2CRegisterMap.write] Value has {len(value)} bytes on board {device_address}")
            return False

        bit_range = properties["bit_range"]
        for byte_val, (lsb, msb) in zip(value, bit_range):
            bit_width = msb - lsb + 1
            if byte_val >= (1 << bit_width):
                logging.debug(f"[I2CRegisterMap.write] Mismatch in register size and value size on board {device_address}")
                logging.debug(f"[I2CRegisterMap.write] Register {register} has {bit_width} bits on board {device_address}")
                logging.debug(f"[I2CRegisterMap.write] Cannot contain 0b{byte_val:b} on board {device_address}")
                return False

        if properties['access'] == self.access_type.WRITE_ONLY:
            current = bytearray(byte_width)
        else:
            error_code, existing = self.i2c.read(device_address, lsa, byte_width)
            if error_code != 0:
                logging.debug(
                    f"[I2CRegisterMap.write] Could not read current value of {register!r} on board {device_address} before writing; aborting"
                )
                return False
            current = bytearray(existing)

        for i, (byte_val, (lsb, msb)) in enumerate(zip(value, bit_range)):
            width = msb - lsb + 1
            mask = (1 << width) - 1
            current[i] = (current[i] & ~(mask << lsb) & 0xFF) | ((byte_val & mask) << lsb)

        return self.i2c.write(device_address, lsa, bytes(current))

    def read(self, device_address: int, register: str) -> Tuple[int, Optional[bytearray]]:
        if register not in self._registers:
            logging.debug(f"[I2CRegisterMap.read] Unknown register: {register!r} on board {device_address}")
            return -1, None

        properties = self._registers[register]
        if properties['access'] == self.access_type.WRITE_ONLY:
            logging.debug(f"[I2CRegisterMap.read] Register {register!r} is write-only on board {device_address}")
            return -1, None

        lsa = properties["address"][0]
        msa = properties["address"][-1]
        byte_width = msa - lsa + 1
        error_code, raw = self.i2c.read(device_address, lsa, byte_width)
        if error_code != 0:
            return error_code, None

        extracted = bytearray(byte_width)
        for i, (byte_val, (lsb, msb)) in enumerate(zip(raw, properties['bit_range'])):
            width = msb - lsb + 1
            mask = (1 << width) - 1
            extracted[i] = (byte_val >> lsb) & mask

        return 0, extracted

    def check_reg(self, device_address: int, register: str, data: bytearray = bytearray()) -> bool:
        success, check = self.read(device_address, register)
        return success == 0 and check == bytes(data)

    def set_default(self, device_address: int) -> None:
        for register in self._registers:
            access = self._registers[register]['access']
            if access == self.access_type.READ_ONLY:
                logging.debug(f'[I2CRegisterMap.set_default] Register {register} on board {device_address}: read only.')
                continue
            if access == self.access_type.WRITE_ONLY:
                logging.debug(f'[I2CRegisterMap.set_default] Register {register} on board {device_address}: write only.')
                continue
            check = False
            while not check:
                value = [self._registers[register]['default']]
                self.write(device_address, register, value)
                check = self.check_reg(device_address, register, value)
            logging.info(f'[I2CRegisterMap.set_default] Register {register} on board {device_address}: set to its default value.')

    def self_test(self, device_address: int) -> bool:
        logging.info(f"[I2CRegisterMap.self_test] Starting self-test on board {device_address}")
        for register, properties in self._registers.items():
            if properties['access'] != self.access_type.READ_WRITE:
                continue
            logging.info(f"[I2CRegisterMap.self_test] Testing register {register!r} on board {device_address}")
            possible_values = [
                list(range(1 << (msb - lsb + 1))) for lsb, msb in properties['bit_range']
            ]
            test_values = [bytearray(test_value) for test_value in product(*possible_values)]
            for test_value in test_values:
                if not self.write(device_address, register, test_value):
                    logging.error(
                        f"[I2CRegisterMap.self_test] Failed to write 0x{test_value.hex()} to {register!r} on board {device_address}"
                    )
                    continue
                error_code, read_value = self.read(device_address, register)
                if error_code != 0:
                    if error_code == -1:
                        logging.error(
                            f"[I2CRegisterMap.self_test] Failed to read {register!r} on board {device_address}: unknown or write-only register"
                        )
                    else:
                        logging.error(
                            f"[I2CRegisterMap.self_test] Failed to read {register!r} on board {device_address}: {self.describe_error(device_address, error_code)}"
                        )
                    continue
                logging.debug(
                    f"[I2CRegisterMap.self_test] Wrote 0x{test_value.hex()} to {register!r} on board {device_address}, read back 0x{read_value.hex()}"
                )
                if read_value != test_value:
                    logging.error(
                        f"[I2CRegisterMap.self_test] Mismatch for {register!r} on board {device_address}: wrote 0x{test_value.hex()}, read 0x{read_value.hex()}"
                    )
        logging.info(f"[I2CRegisterMap.self_test] Finished self-test on board {device_address}")
        return True

    def describe_error(self, device_address: int, code: int) -> str:
        return self.i2c.describe_error(code)

    def __str__(self):
        str = ""
        for register, properties in self._registers.items():
            str += f"Register: {register}\n"
            for key, value in properties.items():
                if key == "access":
                    value = value.name
                str += f"\t{key}:{value}\n"
        return str
