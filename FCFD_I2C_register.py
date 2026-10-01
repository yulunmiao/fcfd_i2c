import json
import logging
from pathlib import Path
from I2C.I2C_windows import I2C_windows
from I2C.I2C_dummy import I2C_dummy
from i2c_register_map import I2CRegisterMap

class ErrorColorFormatter(logging.Formatter):
    RED = "\033[31m"
    RESET = "\033[0m"

    def format(self, record):
        message = super().format(record)
        if record.levelno >= logging.ERROR:
            return f"{self.RED}{message}{self.RESET}"
        return message


def load_configured_devices(config, config_path, argparser):
    register_map_by_address = {}
    address_by_name = {}
    groups = {}
    for device_name, device_cfg in config.items():
        if (not isinstance(device_cfg, dict) 
            or "regmap" not in device_cfg 
            or "address" not in device_cfg 
            or "I2C_type" not in device_cfg):
            continue
        regmap_path = Path(device_cfg["regmap"])
        if not regmap_path.is_absolute():
            regmap_path = config_path.parent / regmap_path
        regmap_path = regmap_path.resolve()
        if not regmap_path.is_file():
            argparser.error(f"register map does not exist: {regmap_path}")

        device_address = int(device_cfg["address"])
        i2c_type = device_cfg["I2C_type"]
        group_key = (regmap_path, i2c_type)
        if group_key not in groups:
            if i2c_type == "windows":
                i2c = I2C_windows()
            elif i2c_type == "dummy":
                i2c = I2C_dummy(str(regmap_path))
            else:
                raise ValueError(f"Unknown I2C type: {i2c_type}")
            groups[group_key] = I2CRegisterMap(json_file=str(regmap_path), i2c=i2c)

        register_map_by_address[device_address] = {
            "register_map": groups[group_key],
        }
        address_by_name[device_name] = device_address

    return register_map_by_address, address_by_name


def resolve_command_line_targets(register_map_by_address, address_by_name, device_names, device_addresses, argparser):
    if device_names is None and device_addresses is None:
        selected_addresses = list(register_map_by_address)
    else:
        selected_addresses = []
        for device_name in device_names or []:
            if device_name not in address_by_name:
                argparser.error(f"unknown device name: {device_name}")
            device_address = address_by_name[device_name]
            if device_address not in selected_addresses:
                selected_addresses.append(device_address)
        for device_address in device_addresses or []:
            if device_address not in register_map_by_address:
                argparser.error(f"unknown board address: {device_address}")
            if device_address not in selected_addresses:
                selected_addresses.append(device_address)

    name_by_address = {}
    for device_name, device_address in address_by_name.items():
        name_by_address.setdefault(device_address, device_name)

    return [
        (
            name_by_address[device_address],
            device_address,
            register_map_by_address[device_address]["register_map"],
        )
        for device_address in selected_addresses
    ]


def run_interactive_write(register_map_by_address, address_by_name):
    while True:
        print(
            "Enter a device name/address, register name, and comma-separated value(s) "
            "(e.g. 'VDDA rst 1'), or 'e' to return to mode selection:"
        )
        user_input = input(str())
        if user_input == 'e':
            return

        parts = user_input.split(maxsplit=2)
        if len(parts) != 3:
            print("Enter a device, register, and value(s). Please try again.")
            continue

        device_selector, register_selector, values_string = parts
        
        try:
            device_address = address_by_name.get(device_selector)
            if device_address is None:
                try:
                    device_address = int(device_selector, 0)
                except ValueError as error:
                    raise ValueError(f"unknown device name or address: {device_selector}") from error
            if device_address not in register_map_by_address:
                raise ValueError(f"unknown device name or address: {device_selector}")

            device = register_map_by_address[device_address]["register_map"]
            if register_selector in device._registers:
                register = register_selector
            else:
                raise ValueError(f"unknown register name: {register_selector}")

            values = bytearray(int(value.strip(), 0) for value in values_string.split(","))

        except ValueError as error:
            print(f"Invalid input: {error}. Please try again.")
            continue

        if device.write(device_address, register, values):
            logging.info(
                f"Successfully wrote {list(values)} to {register} on board {device_address}"
            )
        else:
            logging.error(
                f"Failed to write {list(values)} to {register} on board {device_address}"
            )

def run_interactive_read(register_map_by_address, address_by_name):
    while True:
        print(
            "Enter device name(s)/address(es) followed by register name(s), separated by spaces "
            "(e.g. 'VDDA rst'). Use 'all' as the device to select every configured device, "
            "or as the register to read every readable register. Enter 'e' to return:"
        )
        user_input = input(str())
        if user_input == 'e':
            return

        parts = user_input.split()
        if len(parts) < 2:
            print("Enter a device and register(s). Please try again.")
            continue

        selector_index = 0
        try:
            if parts[0] == "all":
                target_addresses = list(register_map_by_address)
                selector_index = 1
            else:
                target_addresses = []
                while selector_index < len(parts):
                    device_selector = parts[selector_index]
                    device_address = address_by_name.get(device_selector)
                    if device_address is None:
                        try:
                            device_address = int(device_selector, 0)
                        except ValueError:
                            break
                    if device_address not in register_map_by_address:
                        if not target_addresses:
                            raise ValueError(
                                f"unknown device name or address: {device_selector}"
                            )
                        break
                    if device_address not in target_addresses:
                        target_addresses.append(device_address)
                    selector_index += 1
                if not target_addresses:
                    raise ValueError(f"unknown device name or address: {parts[0]}")

            register_selectors = parts[selector_index:]
            if not register_selectors:
                raise ValueError("provide at least one register name or 'all'")

            target_devices = [
                (device_address, register_map_by_address[device_address])
                for device_address in target_addresses
            ]
            target_registers_by_device = None if "all" in register_selectors else register_selectors
        except ValueError as error:
            print(f"Invalid input: {error}. Please try again.")
            continue

        for device_address, device_config in target_devices:
            device = device_config["register_map"]
            if target_registers_by_device is None:
                target_registers = [
                    name for name, properties in device._registers.items()
                    if properties["access"] != device.access_type.WRITE_ONLY
                ]
            else:
                target_registers = target_registers_by_device

            for register in target_registers:
                error_code, value = device.read(device_address, register)
                if error_code == 0:
                    logging.info(f"Read from {register} on board {device_address}: {list(value)}")
                elif error_code == -1:
                    logging.error(
                        f"Failed to read from {register} on board {device_address}: "
                        "register is unknown or write-only"
                    )
                else:
                    logging.error(
                        f"Failed to read from {register} on board {device_address}: "
                        f"{device.describe_error(device_address, error_code)}"
                    )

def run_interactive_self_test(register_map_by_address, address_by_name):
    while True:
        print(
            "Enter device name(s)/address(es) to run self-test, separated by spaces "
            "(e.g. 'VDDA'). Use 'all' as the device to select every configured device. Enter 'e' to return:"
        )
        user_input = input(str())
        if user_input == 'e':
            return

        parts = user_input.split()
        if not parts:
            print("Enter at least one device name or address. Please try again.")
            continue

        try:
            if parts[0] == "all":
                target_addresses = list(register_map_by_address)
            else:
                target_addresses = []
                for device_selector in parts:
                    device_address = address_by_name.get(device_selector)
                    if device_address is None:
                        try:
                            device_address = int(device_selector, 0)
                        except ValueError:
                            raise ValueError(f"unknown device name or address: {device_selector}")
                    if device_address not in register_map_by_address:
                        raise ValueError(f"unknown device name or address: {device_selector}")
                    if device_address not in target_addresses:
                        target_addresses.append(device_address)

        except ValueError as error:
            print(f"Invalid input: {error}. Please try again.")
            continue

        for device_address in target_addresses:
            device = register_map_by_address[device_address]["register_map"]
            device.self_test(device_address)

def run_interactive_set_default(register_map_by_address, address_by_name):
    while True:
        print(
            "Enter device name(s)/address(es) to set registers to default values, separated by spaces "
            "(e.g. 'VDDA'). Use 'all' as the device to select every configured device. Enter 'e' to return:"
        )
        user_input = input(str())
        if user_input == 'e':
            return

        parts = user_input.split()
        if not parts:
            print("Enter at least one device name or address. Please try again.")
            continue

        try:
            if parts[0] == "all":
                target_addresses = list(register_map_by_address)
            else:
                target_addresses = []
                for device_selector in parts:
                    device_address = address_by_name.get(device_selector)
                    if device_address is None:
                        try:
                            device_address = int(device_selector, 0)
                        except ValueError:
                            raise ValueError(f"unknown device name or address: {device_selector}")
                    if device_address not in register_map_by_address:
                        raise ValueError(f"unknown device name or address: {device_selector}")
                    if device_address not in target_addresses:
                        target_addresses.append(device_address)

        except ValueError as error:
            print(f"Invalid input: {error}. Please try again.")
            continue

        for device_address in target_addresses:
            device = register_map_by_address[device_address]["register_map"]
            device.set_default(device_address)
            logging.info(f"Set registers to default values on board {device_address}")

def run_interactive_mode(register_map_by_address, address_by_name):
    while True:
        print(
            "\nAvailable modes:"
            "\n  w  Write register values"
            "\n  r  Read registers"
            "\n  d  Set registers to defaults"
            "\n  t  Run self-test"
            "\n  l  List all registers"
            "\n  ld List all registers with details"
            "\n  e  Exit"
        )
        mode = input(str())
        if mode == 'e':
            return
        elif mode == 'w':
            run_interactive_write(register_map_by_address, address_by_name)
        elif mode == 'r':
            run_interactive_read(register_map_by_address, address_by_name)
        elif mode == 'd':
            run_interactive_set_default(register_map_by_address, address_by_name)
        elif mode == 't':
            run_interactive_self_test(register_map_by_address, address_by_name)
        elif mode == 'l':
            for device_name, device_address in address_by_name.items():
                device = register_map_by_address[device_address]["register_map"]
                logging.info(f"Registers for board {device_name} (address {device_address}):\n{list(device._registers.keys())}")
        elif mode == 'ld':
            for device_name, device_address in address_by_name.items():
                device = register_map_by_address[device_address]["register_map"]
                logging.info(f"Registers for board {device_name} (address {device_address}):\n{device}")
        else:
            print('Invalid mode. Please try again.')



def main():
    import argparse
    argparser = argparse.ArgumentParser(description="FCFD interface for performing I2C operations",epilog="The script would proceed in the interactive if the --interactive option is specified, otherwise it would perform the operations specified by the other options and exit; if multiple operations are specified, they will be performed in the order of --self-test --set-default, --write, and --read")
    argparser.add_argument("--json", "-j", type=str, default="./config/config_windows.json", help="Path to the JSON config file")
    argparser.add_argument("--write", "-w", nargs=2, metavar=("REGISTER", "VALUES"),help="Write comma-separated decimal, 0b-prefixed binary, or 0x-prefixed hexadecimal values")
    argparser.add_argument("--read", "-r", nargs='*', metavar="REGISTER", type=str, help="Read from registers, format: register_names or use all to read all readable registers")
    argparser.add_argument("--set-default", "-d", action="store_true", help="Set all registers to their default values")
    argparser.add_argument("--self-test", "-t", action="store_true", help="Run self-test to verify read/write operations")
    argparser.add_argument("--interactive", "-i", action="store_true", help="Run in interactive mode")
    argparser.add_argument("--board-address", "-b", nargs="+", type=int, default=None, help="Board addresses to use for I2C operations")
    argparser.add_argument("--device-name", "--device", nargs="+", dest="device_names", metavar="NAME", help="Configured device names to use; may be combined with --board-address")
    argparser.add_argument("--debug", "-D", action="store_true", help="Enable debug logging")
    argparser.add_argument("--log-file", "-l", type=str, default=None, help="Path to a log file; if not specified, logs will be printed to the console")

    args = argparser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    console_handler = next(
        handler for handler in logging.getLogger().handlers
        if isinstance(handler, logging.StreamHandler)
    )
    console_handler.setFormatter(ErrorColorFormatter('%(asctime)s - %(levelname)s - %(message)s'))

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

    if not isinstance(config, dict) or not any(isinstance(v, dict) and "regmap" in v for v in config.values()):
        argparser.error("no register map specified in config file")

    register_map_by_address, address_by_name = load_configured_devices(config, config_path, argparser)
    if not register_map_by_address:
        argparser.error("no usable register maps found in config file")

    if args.interactive:
        run_interactive_mode(register_map_by_address, address_by_name)
        return

    targets = resolve_command_line_targets(
        register_map_by_address,
        address_by_name,
        args.device_names,
        args.board_address,
        argparser,
    )

    if args.self_test:
        for device_name, device_address, device_fcfd in targets:
            device_fcfd.self_test(device_address)

    if args.set_default:
        for device_name, device_address, device_fcfd in targets:
            device_fcfd.set_default(device_address)

    if args.write:
        register, values_str = args.write
        values = [int(v, 0) for v in values_str.split(",")]
        for device_name, device_address, device_fcfd in targets:
            if device_fcfd.write(device_address, register, bytearray(values)):
                logging.info(f"Successfully wrote {values} to {register} on board {device_address}")
            else:
                logging.error(f"Failed to write {values} to {register} on board {device_address}")

    if args.read:
        registers = args.read
        for device_name, device_address, device_fcfd in targets:
            if "all" in registers:
                target_registers = [
                    name for name, properties in device_fcfd._registers.items()
                    if properties["access"] != device_fcfd.access_type.WRITE_ONLY
                ]
            else:
                target_registers = registers
            for register in target_registers:
                error_code, value = device_fcfd.read(device_address, register)
                if error_code == 0:
                    logging.info(f"Read from {register} on board {device_address}: {list(value)}")
                elif error_code == -1:
                    logging.error(f"Failed to read from {register} on board {device_address}: register is unknown or write-only")
                else:
                    logging.error(f"Failed to read from {register} on board {device_address}: {device_fcfd.describe_error(device_address, error_code)}")

if __name__ == "__main__":
    main()
