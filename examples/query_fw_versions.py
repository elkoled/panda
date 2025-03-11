#!/usr/bin/env python3
import argparse
from tqdm import tqdm

from opendbc.car.carlog import carlog
from opendbc.car.uds import UdsClient, MessageTimeoutError, NegativeResponseError, \
                            InvalidSubAddressError, SESSION_TYPE, DATA_IDENTIFIER_TYPE
from panda import Panda


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Enhanced script for scanning PSA ECUs over UDS.")
  parser.add_argument("--nonstandard", action="store_true",
                      help="Include manufacturer-specific data ID ranges 0xF100..0xF1FF.")
  parser.add_argument("--no-obd", action="store_true",
                      help="If set, bus 1 will NOT be multiplexed to the OBD-II port (safety_mode).")
  parser.add_argument("--no-29bit", action="store_true", help="Skip scanning 29-bit (extended) addresses.")
  parser.add_argument("--debug", action="store_true")
  parser.add_argument("--addr", help="Scan only this specific address (hex).")
  parser.add_argument("--sub_addr", "--subaddr",
                      help="A hex sub-address or the word 'scan' to brute-force 0x0..0xFF subaddrs.")
  parser.add_argument("--bus", help="Which bus to use (default tries 0,1).")
  parser.add_argument("--serial", help="Specific panda serial to connect to.")
  parser.add_argument("--rxoffset", default="",
                      help="Offset (decimal or hex) to compute the response address = request+offset. "
                           "E.g. --rxoffset -20 or --rxoffset -0x14 for typical PSA offset.")
  args = parser.parse_args()

  if args.debug:
    carlog.setLevel('DEBUG')

  # --- Parse subaddresses ----------------------------------------------------
  sub_addrs = [None]
  if args.sub_addr:
    if args.sub_addr == "scan":
      sub_addrs = list(range(0x100))  # 0..0xFF
    else:
      # parse e.g. '0x12' or '18' from command-line
      sa_val = int(args.sub_addr, 0)
      if not (0 <= sa_val <= 0xFF):
        print(f"ERROR: sub-address out of range: {hex(sa_val)}")
        exit(1)
      sub_addrs = [sa_val]

  # --- Parse rx offset (allow decimal or hex, signed) ------------------------
  rx_offset = None
  if args.rxoffset:
    rx_offset = int(args.rxoffset, 0)  # int(..., 0) parses "10", "-20", "0x14", "-0x14", etc.

  # --- Build list of addresses to try ----------------------------------------
  # If user gave --addr, just do that. Otherwise scan typical 0x600..0x7FF
  # plus optional 29-bit. Adjust as needed for your platform
  if args.addr:
    addrs = [int(args.addr, 0)]
  else:
    addrs = list(range(0x600, 0x800))  # 0x600..0x7FF is often used by PSA modules
    if not args.no_29bit:
      # Typical extended IDs: 0x18daXXXX (some cars respond here).
      # In practice, you might want 0x18da6000..0x18da7FFF or so
      for i in range(0x600, 0x700):
        extended = 0x18DA0000 + (i << 8) + 0xF1
        addrs.append(extended)

  # --- Gather data IDs to read: standard + optional manufacturer ranges ------
  uds_data_ids = {}
  for std_id in DATA_IDENTIFIER_TYPE:
    uds_data_ids[std_id.value] = std_id.name
  # “--nonstandard” means also try typical OEM-specific IDs in 0xF1xx
  if args.nonstandard:
    for uds_id in range(0xF100, 0xF180):
      uds_data_ids[uds_id] = "PSA_OEM_SPECIFIC_1"
    for uds_id in range(0xF1A0, 0xF200):
      uds_data_ids[uds_id] = "PSA_OEM_SPECIFIC_2"

  # --- Connect to panda and set ELM safety mode (ISO-TP echo) ----------------
  panda = Panda(serial=args.serial)
  # If you have a newer PSA with the OBD port on bus 0, you might want bus=0 in set_safety_mode.
  # But openpilot’s “ELM327” mode typically sets bus=1 to multiplex OBD on bus1. Adjust as needed.
  panda.set_safety_mode(Panda.SAFETY_ELM327, 1 if not args.no_obd else 0)

  # Decide which bus lines to try
  # If user gave --bus, just do that. Otherwise, try bus=0 and bus=1 (typical)
  if args.bus is not None:
    bus_list = [int(args.bus)]
  else:
    bus_list = [0, 1]

  results = {}

  print("Scanning addresses ...")
  with tqdm(addrs) as t:
    for addr in t:
      for sub_addr in sub_addrs:
        t.set_description(f"0x{addr:X}, sub=0x{sub_addr:X}" if sub_addr is not None else f"0x{addr:X}")

        for bus in bus_list:
          rx_addr = None
          if rx_offset is not None:
            rx_addr = addr + rx_offset

          # Create UdsClient
          uds_client = UdsClient(panda, req_id=addr, rx_addr=rx_addr, bus=bus,
                                 sub_addr=sub_addr, timeout=0.2)

          # Step 1: ping with TesterPresent
          try:
            uds_client.tester_present()
          except MessageTimeoutError:
            # not responding on this address
            continue
          except InvalidSubAddressError:
            # sub-addr invalid
            break
          except NegativeResponseError:
            pass

          # Step 2: attempt all known session types:
          # Some PSA ECUs only give full info in Programming or SafetySystem session
          session_types = [
            SESSION_TYPE.DEFAULT,
            SESSION_TYPE.EXTENDED_DIAGNOSTIC,
            SESSION_TYPE.PROGRAMMING,
            SESSION_TYPE.SAFETY_SYSTEM_DIAGNOSTIC,
          ]
          saw_any_response = False
          for st in session_types:
            try:
              uds_client.diagnostic_session_control(st)
              saw_any_response = True
            except NegativeResponseError:
              # ECU doesn’t allow that session
              pass
            except MessageTimeoutError:
              # no reply
              pass
            except InvalidSubAddressError:
              # no sense to keep going
              saw_any_response = False
              break

          if not saw_any_response:
            # No valid session found
            continue

          # Step 3: read all data IDs
          resp_data = {}
          for data_id in sorted(uds_data_ids):
            try:
              data = uds_client.read_data_by_identifier(data_id)
              if data:
                resp_data[data_id] = data
            except (NegativeResponseError, MessageTimeoutError, InvalidSubAddressError):
              pass

          if len(resp_data):
            results[(addr, sub_addr, bus)] = resp_data

  # --- Print out results -----------------------------------------------------
  if len(results):
    for (addr, sub_addr, bus), resp in results.items():
      sub_str = f", sub=0x{sub_addr:X}" if sub_addr is not None else ""
      print(f"\n\n*** Results @addr=0x{addr:X}{sub_str}, bus={bus} ***\n")
      for rid, dat in resp.items():
        # If we had a label for this data_id
        label = uds_data_ids.get(rid, f"0x{rid:X}")
        print(f"  0x{rid:04X} {label}: {dat}")
  else:
    print("No firmware responses found!")
