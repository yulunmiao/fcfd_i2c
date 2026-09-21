import json
from enum import Enum
import logging
from pathlib import Path
import time
from typing import List, Optional, Tuple
from I2C.I2C import I2C
from I2C.I2C_windows import I2C_windows
from I2C.I2C_dummy import I2C_dummy
from typing import Optional, Tuple
from itertools import product 

class FCFD_I2C_register:
    """
    This class defines the register structure for the FCFD I2C interface. It provides 
    methods to read from and write to the registers, as well as to configure the I2C 
    settings. The class encapsulates the register addresses and their corresponding 
    values, allowing for easy manipulation of the I2C interface. It allows multiple 
    I2C transports to be used, so long as the I2Cs are with the same register map. 
    The I2C transport is passed in as a dictionary of board addresses to I2C objects.

    This class uses write, read, and get_number_of_devices methods from the I2C class 
    to communicate with the hardware.
    N.B. The write, read functions handles the whole byte(s) of the register, thus the 
    bit_range is used to put the value in the correct bits of the byte(s) of the register. 
    The bit_range is a list of [lsb, msb] for each byte in the register.
    """
    class access_type(Enum):
        READ_ONLY = 0
        WRITE_ONLY = 1
        READ_WRITE = 2
    
    def __init__(self, json_file:str = None, i2cs: dict[int, I2C] = None):
        time.sleep(1)
        self.i2cs = i2cs if i2cs is not None else {0x72: I2C_windows(board_address=0x72)}

        self._registers = {}
        if json_file is None:
            return
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
            # Address can either be a list with 
            # 1 element for individual registers
            # or 2 elements marking the starting and ending address bytes for grouped registers

            # Each register has a 2 byte address and 1 byte of data, the 8 data bits are allocated as in the json,
            # some portions of the register map have sequential registers that save the same purpose hence motivating the 'grouped registers'
            if (not isinstance(address, list) 
                or (len(address)!= 1 and len(address)!=2)):
                raise ValueError(
                    f"Address for {register!r} must be list of 1 or 2 elements, currently being {address}"
                )

            lsa = properties["address"][0]
            msa = properties["address"][-1]
            byte_width = msa - lsa +1 
            bit_range = properties['bit_range']
            # If it is a single byte register bit_range can be
            # 1-d list have 1 element being the bit of the register
            # 1-d list have 2 element marking the start and end bit of the register 
            if len(address)==1:
                if (not isinstance(bit_range, list)
                    or (len(bit_range)!=1 and len(bit_range)!=2)):
                    raise TypeError(
                        f"Bit_range for {register!r} must be a list of 1 or 2 elements, currently being {bit_range}"
                    )
            # If it is a mulit-byte register bit_range can be
            # 1-d list have 2 element for the same bits in all bytes
            # 2-d list [n][2], marking the start and end bit of the register in each byte repectively
            else:
                if not isinstance(bit_range, list) or (
                    len(bit_range) != 2 and not (
                        all(isinstance(entry, list) and len(entry) == 2 for entry in bit_range)
                    )
                ):
                    raise TypeError(
                        f"Bit_range for {register!r} must be a 1-d list of 2 elements or a 2-d list [n][2] for a multi-byte register, currently being {bit_range}"
                    )
            # format the bit_range into a list of [lsb, msb] for each byte in the register
            bit_range = self._per_byte_ranges(bit_range, byte_width)


            # default must be N/A or single integer
            if properties['default'] == 'N/A':
                default = None
                value = [None] * byte_width
            elif not isinstance(properties['default'], int):
                raise TypeError(
                    f"Address and bit_range for {register!r} must be lists"
                )
            else:
                default = properties['default']
                value = bytearray([default]) * byte_width
            self._registers[register] = {
                'address': address,
                'bit_range': bit_range,
                'access': access_type,
                'default': default,
            }

    def _per_byte_ranges(self, bit_range: List, byte_width: int) -> List[List[int]]:
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

    def write(self, board_address: int, register: str, value: bytearray=bytearray() ) -> bool:
        # Write byte array to the register
        # If you want to write 0 to register 'write_test' you would do FCFD_I2C_register.write('write_test', [0])
        # Accepts an integer or a byte array-like payload and validates it against the configured bit range before storing the result
        
        # Register must exist
        if register not in self._registers:
            logging.debug(f"[FCFD_I2C_register.write] Unknown register: {register!r}")
            return False

        # Register must be writable
        properties = self._registers[register]
        if properties['access'] == self.access_type.READ_ONLY:
            logging.debug(f"[FCFD_I2C_register.write] Register {register!r} is read-only")
            return False

        lsa = properties["address"][0]
        msa = properties["address"][-1]
        # Number of bytes the register covers
        byte_width = msa - lsa +1 

        if(len(value) != byte_width):
            logging.debug(f"[FCFD_I2C_register.write] Mismatch in register size and value size")
            logging.debug(f"[FCFD_I2C_register.write] Register {register} has {byte_width} bytes")
            logging.debug(f"[FCFD_I2C_register.write] Value has {len(value)} bytes")
            return False

        # Establish the bit ranges per byte of the register
        bit_range = properties["bit_range"]

        # Bit-width check between the input value and the available bits
        for byte_val, (lsb, msb) in zip(value, bit_range):
            bit_width = msb - lsb + 1
            if byte_val >= ((0b1) << bit_width):
                logging.debug(f"[FCFD_I2C_register.write] Mismatch in register size and value size")
                logging.debug(f"[FCFD_I2C_register.write] Register {register} has {bit_width} bits")
                logging.debug(f"[FCFD_I2C_register.write] Cannot contain 0b{byte_val:b}")
                return False
 
        # Fields can share a byte with other fields (e.g. clk_enable, clk_inv_data, clk_eq etc. are all packed into byte 0)
        # So read the current byte(s) first, patch in only this field's bits, and write the whole byte(s) back
        if properties['access'] == self.access_type.WRITE_ONLY:
            # Strobe bits have no meaningful state to preserve
            current = bytearray(byte_width)
        else:
            error_code, existing = self.i2cs[board_address].read(board_address, lsa, byte_width)
            if error_code != 0:
                logging.debug(f"[FCFD_I2C_register.write] Could not read back current value of {register!r} before writing; aborting")
                return False
            current = bytearray(existing)
 
        for i, (byte_val, (lsb, msb)) in enumerate(zip(value, bit_range)):
            width = msb - lsb + 1
            mask = (1 << width) - 1
            current[i] = (current[i] & ~(mask << lsb) & 0xFF) | ((byte_val & mask) << lsb)

        # Actually write the values to the chip
        if not self.i2cs[board_address].write(board_address, lsa, bytes(current)):
            return False

        return True

    def read(self, board_address: int, register: str) -> Tuple[int, Optional[bytearray]]:
        # Read the register from hardware and return the field's value(s) as a bytearray
        # Returns None on an unknown register, a write-only register, or an I2C error.
        # returns a tuple of (error_code, value) where error_code is 0 for success, -1 
        # for failing sanity checks, and I2C defined error codes for I2C errors. 
        # 
        # The value is a bytearray of the register's value(s) if successful, or None if unsuccessful.

        
        # Check that the register exists
        if register not in self._registers:
            logging.debug(f"[FCFD_I2C_register.read] Unknown register: {register!r}")
            return -1, None

        # Check that the register can be read from
        properties = self._registers[register]
        if properties['access'] == self.access_type.WRITE_ONLY:
            logging.debug(f"[FCFD_I2C_register.read] Register {register!r} is write-only")
            return -1, None
 
        lsa = properties["address"][0]
        msa = properties["address"][-1]
        # How many bytes the register spans
        byte_width = msa - lsa + 1

        # Read the byte values of the register's span
        error_code, raw = self.i2cs[board_address].read(board_address, lsa, byte_width)

        if error_code != 0:
            return error_code, None

        # Create the bit range per byte in the field's byte span
        extracted = bytearray(byte_width)
        # Split the raw read values into the values for the register
        for i, (byte_val, (lsb, msb)) in enumerate(zip(raw, properties['bit_range'])):
            width = msb - lsb + 1
            mask = (1 << width) - 1
            extracted[i] = (byte_val >> lsb) & mask
 
        return 0, extracted

    # Check that a register matches the desired value
    def check_reg(self, board_address: int, register: str, data: bytearray=bytearray()) -> bool:
        success, check = self.read(board_address, register)
        if success == 0 and check == bytes(data):
            return True
        else:
            return False

    # Set writeable registers to default values
    def set_default(self, board_address: int) -> None:
        for register in self._registers:
            if self._registers[register]['access'] == self.access_type.READ_ONLY: 
                logging.debug(f'[FCFD_I2C_register.set_default]Register {register}: this register is read only.')
                continue
            if self._registers[register]['access'] == self.access_type.WRITE_ONLY: 
                logging.debug(f'[FCFD_I2C_register.set_default]Register {register}: this register is write only.')
                continue
            check = False
            while not check:
                value = [self._registers[register]['default']]
                self.write(board_address, register, value)
                check = self.check_reg(board_address, register, value)
            logging.info(f'[FCFD_I2C_register.set_default]Register {register}: has been set to it\'s default value.')
    # Error code descriptions are provided by the I2C transport, so this function just calls the transport's describe_error method.

    def self_test(self, board_address: int) -> bool:
        # Perform a self-test by writing and reading back each writable register
        logging.info("[FCFD_I2C_register.self_test] Starting self-test of read-writable registers")
        for register, properties in self._registers.items():
            if properties['access'] != self.access_type.READ_WRITE:
                continue
            # loop through all possible values for the register byte and bit range
            logging.info(f"[FCFD_I2C_register.self_test] Testing register {register!r}")
            possible_values = [
                list(range(1 << (msb-lsb+1))) for lsb, msb in properties['bit_range']
            ]
            test_values = [bytearray(test_value) for test_value in product(*possible_values)]
            for test_value in test_values:
                # Write the test value to the register
                if not self.write(board_address, register, test_value):
                    logging.error(f"[FCFD_I2C_register.self_test] Failed to write 0x{test_value.hex()} to {register!r}")
                    continue
                # Read back the value from the register
                error_code, read_value = self.read(board_address, register)
                if error_code != 0:
                    if error_code == -1:
                        logging.error(f"[FCFD_I2C_register.self_test] Failed to read from {register!r}: register is unknown or write-only")
                    else:
                        logging.error(f"[FCFD_I2C_register.self_test] Failed to read from {register!r}: {self.describe_error(board_address, error_code)}")
                    continue
                # Check that the read value matches the written value
                else:
                    logging.debug(f"[FCFD_I2C_register.self_test] Wrote 0x{test_value.hex()} to {register!r}, read back 0x{read_value.hex()}")
                if read_value != test_value:
                    logging.error(f"[FCFD_I2C_register.self_test] Mismatch for {register!r}: wrote 0x{test_value.hex()}, read 0x{read_value.hex()}")
                    continue
        logging.info("[FCFD_I2C_register.self_test] Finish self-test of read-writable registers")
        return True
    def describe_error(self, board_address: int, code: int) -> str:
        return self.i2cs[board_address].describe_error(code)    
    def __str__(self):
        rtn = ""
        for register, properties in self._registers.items():
            rtn += f"Register: {register}\n"
            for key, value in properties.items():
                rtn+=f"\t{key}:{value}\n"
        return rtn

def main():
    import argparse
    argparser = argparse.ArgumentParser(description="FCFD interface for performing I2C operations",epilog="The script would proceed in the interactive if the --interactive option is specified, otherwise it would perform the operations specified by the other options and exit; if multiple operations are specified, they will be performed in the order of --self-test --set-default, --write, and --read")
    argparser.add_argument("--json", "-j", type=str, default="./config/config_windows.json", help="Path to the JSON config file")
    argparser.add_argument("--write", "-w", nargs=2, metavar=("REGISTER", "VALUES"),help="Write comma-separated decimal, 0b-prefixed binary, or 0x-prefixed hexadecimal values")
    argparser.add_argument("--read", "-r", nargs='*', metavar="REGISTER", type=str, help="Read from registers, format: register_names or use all to read all readable registers")
    argparser.add_argument("--set-default", "-d", action="store_true", help="Set all registers to their default values")
    argparser.add_argument("--self-test", "-t", action="store_true", help="Run self-test to verify read/write operations")
    argparser.add_argument("--interactive", "-i", action="store_true", help="Run in interactive mode")
    argparser.add_argument("--board-address", "-b", nargs='*', type=int, default=None, help="Board addresses to use for I2C operations; if not specified, all board addresses in the config file will be used")
    argparser.add_argument("--debug", "-D", action="store_true", help="Enable debug logging")
    argparser.add_argument("--log-file", "-l", type=str, default=None, help="Path to a log file; if not specified, logs will be printed to the console")

    args = argparser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    if args.log_file:
        file_handler = logging.FileHandler(args.log_file)
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        logging.getLogger().addHandler(file_handler)
        
    config_path = Path(args.json).resolve()
    if not config_path.is_file():
        argparser.error(f"config file does not exist: {config_path}")

    with config_path.open('r') as f:
        config = json.load(f)

    if not config.get("regmap"):
        argparser.error("no register map specified in config file")
    regmap_path = Path(config["regmap"])
    if not regmap_path.is_absolute():
        regmap_path = config_path.parent / regmap_path
    regmap_path = regmap_path.resolve()
    if not regmap_path.is_file():
        argparser.error(f"register map does not exist: {regmap_path}")

    i2cs = {}
    for board_address, i2c_type in zip(config["board_addresses"], config["I2C_type"]):
        if i2c_type == "windows":
            i2cs[board_address] = I2C_windows(board_address=board_address)
        elif i2c_type == "dummy":
            i2cs[board_address] = I2C_dummy(str(regmap_path))
        else:
            raise ValueError(f"Unknown I2C type: {i2c_type}")
    fcfd = FCFD_I2C_register(json_file=str(regmap_path), i2cs=i2cs)


    if args.interactive:
        while True:
            print("\nEnter a mode:\n'w' -- write,\n'r' --- read,\n'd' --- set to default,\n't' --- self-test,\n'e' --- exit.")
            mode = input(str())
            if mode == 'e':
                return
            elif mode == 'w':
                while True:
                    print("Enter register name and value(s) to write, separated by a space (e.g. 'register_name 0,1,2') or 'e' to return to mode selection:")
                    user_input = input(str())
                    if user_input == 'e':
                        break
                    try:
                        register, values_str = user_input.split()
                        values = [int(v, 0) for v in values_str.split(",")]
                        if args.board_address is not None:
                            board_addresses = [args.board_address]
                        else:
                            board_addresses = list(i2cs.keys())
                        for board_address in board_addresses:
                            if fcfd.write(board_address, register, bytearray(values)):
                                logging.info(f"Successfully wrote {values} to {register} on board {board_address}")
                            else:
                                logging.error(f"Failed to write {values} to {register} on board {board_address}")
                    except ValueError as e:
                        print(f"Invalid input: {e}. Please try again.")
            elif mode == 'r':
                while True:
                    print("Enter register name(s) to read, separated by spaces (e.g. 'register1 register2') or 'all' to read all readable registers, or 'e' to return to mode selection:")
                    user_input = input(str())
                    if user_input == 'e':
                        break
                    registers = user_input.split()
                    if args.board_address is not None:
                        board_addresses = [args.board_address]
                    else:
                        board_addresses = list(i2cs.keys())
                    for board_address in board_addresses:
                        if "all" in registers:
                            registers = list(fcfd._registers.keys())
                        for register in registers:
                            error_code, value = fcfd.read(board_address, register)
                            if error_code == 0:
                                logging.info(f"Read from {register} on board {board_address}: {list(value)}")
                            elif error_code == -1:
                                logging.error(f"Failed to read from {register} on board {board_address}: register is unknown or write-only")
                            else:
                                logging.error(f"Failed to read from {register} on board {board_address}: {fcfd.describe_error(board_address, error_code)}")
            elif mode == 'd':
                if args.board_address is not None:
                    board_addresses = [args.board_address]
                else:
                    board_addresses = list(i2cs.keys())
                for board_address in board_addresses:
                    fcfd.set_default(board_address)
            elif mode == 't':
                if args.board_address is not None:
                    board_addresses = [args.board_address]
                else:
                    board_addresses = list(i2cs.keys())
                for board_address in board_addresses:
                    fcfd.self_test(board_address) 
            else:
                print('Invalid mode. Please try again.')

    if args.self_test:
        if args.board_address is not None:
            board_addresses = [args.board_address]
        else:
            board_addresses = list(i2cs.keys())
        for board_address in board_addresses:
            fcfd.self_test(board_address)

    if args.set_default:
        if args.board_address is not None:
            board_addresses = [args.board_address]
        else:
            board_addresses = list(i2cs.keys())
        for board_address in board_addresses:
            fcfd.set_default(board_address)

    if args.write:
        register, values_str = args.write
        values = [int(v, 0) for v in values_str.split(",")]
        if args.board_address is not None:
            board_addresses = [args.board_address]
        else:
            board_addresses = list(i2cs.keys())
        for board_address in board_addresses:
            if fcfd.write(board_address, register, bytearray(values)):
                logging.info(f"Successfully wrote {values} to {register} on board {board_address}")
            else:
                logging.error(f"Failed to write {values} to {register} on board {board_address}")

    if args.read:
        registers = args.read
        if args.board_address is not None:
            board_addresses = [args.board_address]
        else:
            board_addresses = list(i2cs.keys())
        for board_address in board_addresses:
            if "all" in registers:
                registers = list(fcfd._registers.keys())
        for register in registers:
            error_code, value = fcfd.read(board_address, register)
            if error_code == 0:
                logging.info(f"Read from {register} on board {board_address}: {list(value)}")
            elif error_code == -1:
                logging.error(f"Failed to read from {register} on board {board_address}: register is unknown or write-only")
            else:
                logging.error(f"Failed to read from {register} on board {board_address}: {fcfd.describe_error(board_address, error_code)}")

if __name__ == "__main__":
    main()
