#!/usr/bin/env python3
import argparse
import time
from tqdm import tqdm
from opendbc.car.carlog import carlog
from opendbc.car.uds import UdsClient, MessageTimeoutError, NegativeResponseError, InvalidSubAddressError, \
                            SESSION_TYPE, DATA_IDENTIFIER_TYPE, SERVICE_TYPE
from opendbc.car.structs import CarParams
from panda import Panda

"""
# Basic usage for PSA vehicles, scanning all PSA-specific addresses on bus 0:
python query_fw_versions.py --psa --bus 0 --rxoffset -20

# To scan a specific ECU address:
python query_fw_versions.py --psa --addr 6B5 --bus 0 --rxoffset -20 --timeout 1.5

# For a more thorough scan, including non-standard identifiers:
python query_fw_versions.py --psa --nonstandard --bus 0 --rxoffset -20
"""

# Add PSA-specific DATA_IDENTIFIER_TYPE entries
PSA_DATA_IDENTIFIERS = {
    0xF1A0: "PSA_SPECIFIC_DATA_1",
    0xF1A1: "PSA_SPECIFIC_DATA_2",
    0xF1A2: "PSA_ECU_HARDWARE_NUMBER",
    0xF1A3: "PSA_ECU_SOFTWARE_NUMBER",
    0xF1A4: "PSA_SUPPLIER_NAME",
    0xF1A5: "PSA_CALIBRATION_NUMBER",
    0xF1AF: "PSA_SYSTEM_NAME",
    0xF1B0: "PSA_VIN",
    0xF1E0: "PSA_FIRMWARE_VERSION",
    # Add more PSA-specific identifiers as needed
}

class EnhancedUdsClient(UdsClient):
    """Extended UDS client with PSA-specific enhancements"""

    def __init__(self, *args, **kwargs):
        # Allow longer timeout for PSA ECUs
        if 'timeout' not in kwargs:
            kwargs['timeout'] = 1.0  # Increase default timeout
        super().__init__(*args, **kwargs)

    def diagnostic_session_control_with_retry(self, session_type, max_retries=3):
        """Try diagnostic session control with retries"""
        for attempt in range(max_retries):
            try:
                return self.diagnostic_session_control(session_type)
            except (NegativeResponseError, MessageTimeoutError) as e:
                if attempt == max_retries - 1:
                    raise
                time.sleep(0.1)  # Short delay between retries

    def read_data_by_identifier_raw(self, identifier):
        """Send raw read data request for an identifier"""
        service_id = SERVICE_TYPE.READ_DATA_BY_IDENTIFIER
        data = bytes([service_id]) + identifier.to_bytes(2, 'big')
        return self._send_with_response(service_id, data)

def try_special_initialization(uds_client):
    """Try PSA-specific initialization sequence"""
    try:
        # Common PSA initialization commands
        # Tester present
        uds_client.tester_present()

        # Try extended diagnostic session, which often works better for PSA
        uds_client.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)

        # Some PSA ECUs need a delay after session change
        time.sleep(0.1)

        # Send additional tester present to keep session alive
        uds_client.tester_present()

        return True
    except (NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
        return False

def query_psa_specific_ecu(panda, addr, rx_addr=None, bus=0, sub_addr=None, timeout=1.0):
    """Query a PSA ECU with PSA-specific sequences"""
    results = {}

    # Try standard addressing first
    uds_client = EnhancedUdsClient(panda, addr, rx_addr, bus, sub_addr=sub_addr, timeout=timeout)

    # Some PSA ECUs need a tester present before any other command
    try:
        uds_client.tester_present()
    except (NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
        return results

    # Try different diagnostic sessions in PSA-preferred order
    sessions_to_try = [
        SESSION_TYPE.EXTENDED_DIAGNOSTIC,
        SESSION_TYPE.DEFAULT,
        SESSION_TYPE.PROGRAMMING,
    ]

    session_established = False
    for session in sessions_to_try:
        try:
            uds_client.diagnostic_session_control(session)
            session_established = True
            break
        except (NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
            continue

    if not session_established:
        return results

    # Add all standard UDS identifiers
    uds_data_ids = {}
    for std_id in DATA_IDENTIFIER_TYPE:
        uds_data_ids[std_id.value] = std_id.name

    # Add PSA-specific identifiers
    for psa_id, name in PSA_DATA_IDENTIFIERS.items():
        uds_data_ids[psa_id] = name

    # Query all identifiers
    for uds_data_id in sorted(uds_data_ids):
        try:
            # First try the standard way
            try:
                data = uds_client.read_data_by_identifier(DATA_IDENTIFIER_TYPE(uds_data_id))
                if data:
                    results[uds_data_id] = data
            except (ValueError, NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
                # If standard way fails for PSA-specific IDs, try raw request
                if uds_data_id in PSA_DATA_IDENTIFIERS:
                    try:
                        data = uds_client.read_data_by_identifier_raw(uds_data_id)
                        if data and len(data) > 3:  # Skip service ID and identifier bytes
                            results[uds_data_id] = data[3:]  # Remove header from response
                    except (NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
                        pass
        except Exception:
            # Continue to next identifier on any error
            pass

    return results

def scan_psa_specific_addresses(args):
    """Scan PSA-specific address ranges"""
    # Standard PSA 11-bit address ranges
    psa_ranges = []

    # ECU Module addresses commonly used in PSA vehicles
    psa_ranges.extend([0x6A0 + i for i in range(16)])  # Engine, transmission
    psa_ranges.extend([0x6B0 + i for i in range(16)])  # ABS, ESP, power steering
    psa_ranges.extend([0x6C0 + i for i in range(16)])  # Dashboard, display
    psa_ranges.extend([0x6D0 + i for i in range(16)])  # BSI, BCM
    psa_ranges.extend([0x6E0 + i for i in range(16)])  # Entertainment, radio
    psa_ranges.extend([0x760 + i for i in range(16)])  # Additional modules

    # Add standard UDS/diagnostic addresses
    psa_ranges.extend([0x700 + i for i in range(16)])

    # Add manufacturer-specific broadcast address
    psa_ranges.append(0x7DF)

    # Remove duplicates and sort
    psa_ranges = sorted(list(set(psa_ranges)))

    return psa_ranges

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rxoffset", default="")
    parser.add_argument("--nonstandard", action="store_true")
    parser.add_argument("--psa", action="store_true", help="Use PSA-specific address ranges and identifiers")
    parser.add_argument("--no-obd", action="store_true", help="Bus 1 will not be multiplexed to the OBD-II port")
    parser.add_argument("--no-29bit", action="store_true", help="29 bit addresses will not be queried")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--addr")
    parser.add_argument("--sub_addr", "--subaddr", help="A hex sub-address or `scan` to scan the full sub-address range")
    parser.add_argument("--bus")
    parser.add_argument("--timeout", type=float, default=1.0, help="Timeout in seconds for UDS requests (default: 1.0)")
    parser.add_argument('-s', '--serial', help="Serial number of panda to use")
    args = parser.parse_args()

    if args.debug:
        carlog.setLevel('DEBUG')

    if args.addr:
        addrs = [int(args.addr, base=16)]
    elif args.psa:
        # Use PSA-specific address ranges
        addrs = scan_psa_specific_addresses(args)
        print(f"Scanning {len(addrs)} PSA-specific addresses")
    else:
        # Default address ranges
        addrs = [0x700 + i for i in range(256)]
        if not args.no_29bit:
            addrs += [0x18da0000 + (i << 8) + 0xf1 for i in range(256)]

    results = {}

    sub_addrs = [None]
    if args.sub_addr:
        if args.sub_addr == "scan":
            sub_addrs = list(range(0xff + 1))
        else:
            sub_addrs = [int(args.sub_addr, base=16)]
            if sub_addrs[0] > 0xff:  # type: ignore
                print(f"Invalid sub-address: 0x{sub_addrs[0]:X}, needs to be in range 0x0 to 0xff")
                parser.print_help()
                exit()

    uds_data_ids = {}
    for std_id in DATA_IDENTIFIER_TYPE:
        uds_data_ids[std_id.value] = std_id.name

    # Add PSA-specific identifiers
    if args.psa or args.nonstandard:
        for psa_id, name in PSA_DATA_IDENTIFIERS.items():
            uds_data_ids[psa_id] = name

    if args.nonstandard:
        for uds_id in range(0xf100, 0xf180):
            uds_data_ids[uds_id] = "IDENTIFICATION_OPTION_VEHICLE_MANUFACTURER_SPECIFIC_DATA_IDENTIFIER"
        for uds_id in range(0xf1a0, 0xf1f0):
            uds_data_ids[uds_id] = "IDENTIFICATION_OPTION_VEHICLE_MANUFACTURER_SPECIFIC"
        for uds_id in range(0xf1f0, 0xf200):
            uds_data_ids[uds_id] = "IDENTIFICATION_OPTION_SYSTEM_SUPPLIER_SPECIFIC"

    panda_serials = Panda.list()
    if args.serial is None and len(panda_serials) > 1:
        print("\nMultiple pandas found, choose one:")
        for serial in panda_serials:
            with Panda(serial) as panda:
                print(f"  {serial}: internal={panda.is_internal()}")
        print()
        parser.print_help()
        exit()

    panda = Panda(serial=args.serial)
    panda.set_safety_mode(CarParams.SafetyModel.elm327, 1 if args.no_obd else 0)
    print("querying addresses ...")
    with tqdm(addrs) as t:
        for addr in t:
            # skip functional broadcast addrs if not specifically requested
            if (addr == 0x7df or addr == 0x18db33f1) and not args.addr:
                continue

            if args.bus:
                bus = int(args.bus)
            else:
                bus = 1 if panda.has_obd() else 0

            rx_addr = None
            if args.rxoffset:
                rx_addr = addr + int(args.rxoffset, base=16)

            # Try all sub-addresses for addr. By default, this is None
            for sub_addr in sub_addrs:
                sub_addr_str = hex(sub_addr) if sub_addr is not None else None
                t.set_description(f"{hex(addr)}, {sub_addr_str}")

                # First try with the standard UDS client
                try:
                    uds_client = UdsClient(panda, addr, rx_addr, bus, sub_addr=sub_addr, timeout=args.timeout)

                    # Check for anything alive at this address
                    try:
                        uds_client.tester_present()
                    except (NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
                        # If standard approach fails, try PSA-specific approach
                        if args.psa:
                            result = query_psa_specific_ecu(panda, addr, rx_addr, bus, sub_addr, args.timeout)
                            if result:
                                results[(addr, sub_addr)] = result
                        continue

                    # Try to switch to highest available diagnostic session
                    try:
                        uds_client.diagnostic_session_control(SESSION_TYPE.DEFAULT)
                        uds_client.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
                    except NegativeResponseError:
                        # Continue anyway, we might be able to read some data
                        pass
                    except MessageTimeoutError:
                        # If timeout during session change, try PSA-specific approach
                        if args.psa:
                            result = query_psa_specific_ecu(panda, addr, rx_addr, bus, sub_addr, args.timeout)
                            if result:
                                results[(addr, sub_addr)] = result
                        continue
                    except InvalidSubAddressError as e:
                        print(f'*** Skipping address {hex(addr)}: {e}')
                        break

                    # Query data identifiers
                    resp = {}
                    for uds_data_id in sorted(uds_data_ids):
                        try:
                            data = uds_client.read_data_by_identifier(DATA_IDENTIFIER_TYPE(uds_data_id))
                            if data:
                                resp[uds_data_id] = data
                        except (ValueError, NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
                            # Try PSA-specific raw request for PSA identifiers
                            if (args.psa or args.nonstandard) and uds_data_id in PSA_DATA_IDENTIFIERS:
                                try:
                                    if hasattr(uds_client, 'read_data_by_identifier_raw'):
                                        data = uds_client.read_data_by_identifier_raw(uds_data_id)
                                        if data and len(data) > 3:
                                            resp[uds_data_id] = data[3:]
                                except (NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
                                    pass

                    if resp.keys():
                        results[(addr, sub_addr)] = resp

                except Exception as e:
                    if args.debug:
                        print(f"Exception at addr {hex(addr)}: {e}")
                    continue

    if len(results.items()):
        for (addr, sub_addr), resp in sorted(results.items()):
            sub_addr_str = f", sub-address 0x{sub_addr:X}" if sub_addr is not None else ""
            print(f"\n\n*** Results for address 0x{addr:X}{sub_addr_str} ***\n\n")
            for rid, dat in sorted(resp.items()):
                data_name = uds_data_ids.get(rid, f"UNKNOWN_ID_0x{rid:04X}")
                print(f"0x{rid:04X} {data_name}: {dat}")
    else:
        print("no fw versions found!")