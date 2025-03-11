#!/usr/bin/env python3
import argparse
import time
from tqdm import tqdm
from opendbc.car.carlog import carlog
from opendbc.car.uds import UdsClient, MessageTimeoutError, NegativeResponseError, InvalidSubAddressError, \
                            SESSION_TYPE, DATA_IDENTIFIER_TYPE
from opendbc.car.structs import CarParams
from panda import Panda

if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument("--rxoffset", default="")
  parser.add_argument("--nonstandard", action="store_true")
  parser.add_argument("--no-obd", action="store_true", help="Bus 1 will not be multiplexed to the OBD-II port")
  parser.add_argument("--no-29bit", action="store_true", help="29 bit addresses will not be queried")
  parser.add_argument("--debug", action="store_true")
  parser.add_argument("--addr")
  parser.add_argument("--sub_addr", "--subaddr", help="A hex sub-address or `scan` to scan the full sub-address range")
  parser.add_argument("--bus")
  parser.add_argument('-s', '--serial', help="Serial number of panda to use")
  args = parser.parse_args()

  if args.debug:
    carlog.setLevel('DEBUG')

  # Build list of candidate ECU addresses
  if args.addr:
    addrs = [int(args.addr, base=16)]
  else:
    addrs = [0x700 + i for i in range(256)]
    if not args.no_29bit:
      addrs += [0x18da0000 + (i << 8) + 0xf1 for i in range(256)]
  results = {}

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

  # Build dictionary of UDS identifiers from the standard enumeration.
  uds_data_ids = {}
  for std_id in DATA_IDENTIFIER_TYPE:
    uds_data_ids[std_id.value] = std_id.name
  # Optionally add manufacturer-specific ranges.
  if args.nonstandard:
    for uds_id in range(0xf100, 0xf180):
      uds_data_ids[uds_id] = "IDENTIFICATION_OPTION_VEHICLE_MANUFACTURER_SPECIFIC_DATA_IDENTIFIER"
    for uds_id in range(0xf1a0, 0xf1f0):
      uds_data_ids[uds_id] = "IDENTIFICATION_OPTION_VEHICLE_MANUFACTURER_SPECIFIC"
    for uds_id in range(0xf1f0, 0xf200):
      uds_data_ids[uds_id] = "IDENTIFICATION_OPTION_SYSTEM_SUPPLIER_SPECIFIC"

  # Define the diagnostic sessions to try.
  # The extra session 0xC0 is used by PSA (as seen in the diag_adapterbed.py script sending "10C0")
  sessions_to_try = [SESSION_TYPE.DEFAULT, SESSION_TYPE.EXTENDED_DIAGNOSTIC, 0xC0]

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
  # Safety mode: if --no-obd is set, use bus 1 multiplexing; otherwise bus 0.
  panda.set_safety_mode(CarParams.SafetyModel.elm327, 1 if args.no_obd else 0)
  print("Querying addresses ...")
  with tqdm(addrs) as t:
    for addr in t:
      # Skip functional broadcast addresses
      if addr == 0x7df or addr == 0x18db33f1:
        continue

      if args.bus:
        bus = int(args.bus)
      else:
        bus = 1 if panda.has_obd() else 0
      rx_addr = addr + int(args.rxoffset, base=16) if args.rxoffset else None

      # Try all sub-addresses for addr. Default is [None].
      for sub_addr in sub_addrs:
        sub_addr_str = hex(sub_addr) if sub_addr is not None else None
        t.set_description(f"{hex(addr)}, {sub_addr_str}")
        uds_client = UdsClient(panda, addr, rx_addr, bus, sub_addr=sub_addr, timeout=0.2)

        try:
          uds_client.tester_present()
        except (NegativeResponseError, MessageTimeoutError):
          continue
        except InvalidSubAddressError as e:
          print(f'*** Skipping address {hex(addr)}: {e}')
          break

        # Try each session to see if additional data can be obtained.
        session_responses = {}
        for session in sessions_to_try:
          try:
            # Attempt to switch to the diagnostic session.
            uds_client.diagnostic_session_control(session)
          except Exception as e:
            print(f"Failed to switch to session {hex(session)} on address {hex(addr)}: {e}")
            continue

          # For each session, query all the standard (and nonstandard if selected) data identifiers.
          resp = {}
          for uds_data_id in sorted(uds_data_ids):
            try:
              # Read data by identifier using the UDS service 0x22.
              data = uds_client.read_data_by_identifier(DATA_IDENTIFIER_TYPE(uds_data_id))
              if data:
                resp[uds_data_id] = data
            except (NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
              pass

          if resp:
            session_responses[hex(session)] = resp
          # Small pause between sessions to avoid overloading the ECU.
          time.sleep(0.05)

        if session_responses:
          results[(addr, sub_addr)] = session_responses

  # Output the gathered results.
  if results:
    for (addr, sub_addr), sessions in results.items():
      sub_addr_str = f", sub-address 0x{sub_addr:X}" if sub_addr is not None else ""
      print(f"\n\n*** Results for address 0x{addr:X}{sub_addr_str} ***\n")
      for sess, resp in sessions.items():
        print(f"--- Diagnostic Session {sess} ---")
        for rid, dat in resp.items():
          # Use the UDS dictionary for a human-readable label, if available.
          label = uds_data_ids.get(rid, "UNKNOWN")
          print(f"0x{rid:02X} {label}: {dat}")
  else:
    print("No firmware versions found!")
