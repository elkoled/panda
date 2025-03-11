#!/usr/bin/env python3
import argparse
from tqdm import tqdm
from opendbc.car.carlog import carlog
from opendbc.car.uds import UdsClient, MessageTimeoutError, NegativeResponseError, InvalidSubAddressError, \
                            SESSION_TYPE, DATA_IDENTIFIER_TYPE
from opendbc.car.structs import CarParams
from panda import Panda

# Build a mapping of standard UDS identifiers
uds_data_ids = {}
for std_id in DATA_IDENTIFIER_TYPE:
  uds_data_ids[std_id.value] = std_id.name

def add_nonstandard_ids(nonstandard_flag):
  """If --nonstandard flag is set, add extra ranges for manufacturer‐specific DIDs."""
  if nonstandard_flag:
    for uds_id in range(0xf100, 0xf180):
      uds_data_ids[uds_id] = "IDENTIFICATION_OPTION_VEHICLE_MANUFACTURER_SPECIFIC_DATA_IDENTIFIER"
    for uds_id in range(0xf1a0, 0xf1f0):
      uds_data_ids[uds_id] = "IDENTIFICATION_OPTION_VEHICLE_MANUFACTURER_SPECIFIC"
    for uds_id in range(0xf1f0, 0xf200):
      uds_data_ids[uds_id] = "IDENTIFICATION_OPTION_SYSTEM_SUPPLIER_SPECIFIC"

def query_raw_info(uds_client):
  """
  Query extra manufacturer-specific commands using raw UDS messages.
  This function sends a sequence of commands that in the PSA adapter were:
    - 10C0 : Diagnostic session control (to switch to a specific session)
    - 2180 : Read additional firmware info
    - 17FF00 : Request extended ECU details
  It assumes the UdsClient has an internal method _do_cmd that sends a raw command (in bytes) and returns the response.
  """
  raw_cmds = {
      "10C0": "Diagnostic session control to manufacturer-specific session",
      "2180": "Read manufacturer-specific firmware info",
      "17FF00": "Request extended ECU details"
  }
  responses = {}
  for cmd, desc in raw_cmds.items():
    try:
      # Convert the hex string to bytes and send as a raw command.
      # (This uses the internal _do_cmd method; if unavailable, implement a similar raw send.)
      response = uds_client._do_cmd(bytes.fromhex(cmd))
      responses[cmd] = response
    except Exception as e:
      responses[cmd] = f"Error: {str(e)}"
  return responses

if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument("--rxoffset", default="")
  parser.add_argument("--nonstandard", action="store_true", help="Include non-standard UDS IDs")
  parser.add_argument("--no-obd", action="store_true", help="Bus 1 will not be multiplexed to the OBD-II port")
  parser.add_argument("--no-29bit", action="store_true", help="29 bit addresses will not be queried")
  parser.add_argument("--debug", action="store_true")
  parser.add_argument("--addr")
  parser.add_argument("--sub_addr", "--subaddr", help="A hex sub-address or `scan` to scan the full sub-address range")
  parser.add_argument("--bus")
  parser.add_argument("--raw", action="store_true", help="Also query manufacturer-specific raw commands")
  parser.add_argument('-s', '--serial', help="Serial number of panda to use")
  args = parser.parse_args()

  if args.debug:
    carlog.setLevel('DEBUG')

  add_nonstandard_ids(args.nonstandard)

  if args.addr:
    addrs = [int(args.addr, base=16)]
  else:
    addrs = [0x700 + i for i in range(256)]
    if not args.no_29bit:
      addrs += [0x18da0000 + (i << 8) + 0xf1 for i in range(256)]
  results = {}

  # Setup sub-addresses
  sub_addrs: list[int | None] = [None]
  if args.sub_addr:
    if args.sub_addr == "scan":
      sub_addrs = list(range(0xff + 1))
    else:
      sub_addrs = [int(args.sub_addr, base=16)]
      if sub_addrs[0] > 0xff:  # type: ignore
        print(f"Invalid sub-address: 0x{sub_addrs[0]:X}, needs to be in range 0x0 to 0xff")
        parser.print_help()
        exit()

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
      # skip functional broadcast addresses
      if addr == 0x7df or addr == 0x18db33f1:
        continue

      bus = int(args.bus) if args.bus else (1 if panda.has_obd() else 0)
      rx_addr = addr + int(args.rxoffset, base=16) if args.rxoffset else None

      for sub_addr in sub_addrs:
        sub_addr_str = hex(sub_addr) if sub_addr is not None else None
        t.set_description(f"{hex(addr)}, {sub_addr_str}")
        uds_client = UdsClient(panda, addr, rx_addr, bus, sub_addr=sub_addr, timeout=0.2)

        # Establish a session on the ECU:
        try:
          uds_client.tester_present()
          uds_client.diagnostic_session_control(SESSION_TYPE.DEFAULT)
          uds_client.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
        except NegativeResponseError:
          pass
        except MessageTimeoutError:
          continue
        except InvalidSubAddressError as e:
          print(f'*** Skipping address {hex(addr)}: {e}')
          break

        # Query standard UDS data identifiers
        std_resp = {}
        for uds_data_id in sorted(uds_data_ids):
          try:
            data = uds_client.read_data_by_identifier(DATA_IDENTIFIER_TYPE(uds_data_id))
            if data:
              std_resp[uds_data_id] = data
          except (NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
            pass

        # If requested, query extra (raw) manufacturer-specific commands
        raw_resp = {}
        if args.raw:
          raw_resp = query_raw_info(uds_client)

        results[(addr, sub_addr)] = {"standard": std_resp, "raw": raw_resp}

  # Print out the results:
  if results:
    for (addr, sub_addr), resp in results.items():
      sub_addr_str = f", sub-address 0x{sub_addr:X}" if sub_addr is not None else ""
      print(f"\n\n*** Results for address 0x{addr:X}{sub_addr_str} ***\n")
      if resp["standard"]:
        print("Standard UDS responses:")
        for rid, dat in resp["standard"].items():
          name = uds_data_ids.get(rid, f"0x{rid:02X}")
          print(f"  0x{rid:02X} {name}: {dat}")
      else:
        print("No standard UDS responses.")
      if args.raw:
        print("\nRaw (manufacturer-specific) responses:")
        for cmd, dat in resp["raw"].items():
          print(f"  Command {cmd}: {dat}")
  else:
    print("no fw versions found!")
