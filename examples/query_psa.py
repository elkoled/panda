#!/usr/bin/env python3
import argparse
import csv
import subprocess
import time
import os
import sys
from datetime import datetime
from io import StringIO

def load_ecu_ids(csv_file):
    """Load ECU IDs from CSV file"""
    ecus = []
    try:
        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ecus.append({
                    'family': row['family'],
                    'request': int(row['request'], 16),
                    'response': int(row['response'], 16)
                })
        return ecus
    except FileNotFoundError:
        print(f"Error: CSV file '{csv_file}' not found!")
        return []
    except Exception as e:
        print(f"Error loading CSV: {e}")
        return []

def query_ecu(ecu, bus, rxoffset, timeout, nonstandard):
    """Query a single ECU using query_fw_versions.py"""
    print(f"\n{'='*80}")
    print(f"Querying ECU: {ecu['family']} (Request: 0x{ecu['request']:X}, Response: 0x{ecu['response']:X})")
    print(f"{'='*80}")

    # Build command with appropriate arguments
    cmd = [
        "python", "query_fw_versions.py",
        "--psa",
        "--addr", f"{hex(ecu['request'])}",
        "--bus", str(bus),
        "--rxoffset", f"{rxoffset}",
        "--timeout", str(timeout)
    ]

    if nonstandard:
        cmd.append("--nonstandard")

    # Execute the query command
    try:
        start_time = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True)
        elapsed_time = time.time() - start_time

        # Process and return output
        output = result.stdout if result.stdout else result.stderr
        success = "no fw versions found!" not in output and result.returncode == 0

        return {
            'family': ecu['family'],
            'request': ecu['request'],
            'response': ecu['response'],
            'output': output,
            'success': success,
            'elapsed_time': elapsed_time
        }
    except Exception as e:
        print(f"Error executing query: {e}")
        return {
            'family': ecu['family'],
            'request': ecu['request'],
            'response': ecu['response'],
            'output': f"Error: {str(e)}",
            'success': False,
            'elapsed_time': 0
        }

def parse_ecu_response(output):
    """Parse the output to extract firmware information"""
    info = {}

    # Handle empty or error responses
    if not output or "no fw versions found!" in output:
        return info

    # Extract information from output
    current_address = None
    for line in output.split('\n'):
        if line.startswith('*** Results for address'):
            # Extract address from line like "*** Results for address 0x6B5 ***"
            parts = line.split()
            current_address = parts[4]
            info[current_address] = {}
        elif current_address and ':' in line and len(line.split(':')) >= 2:
            # Extract data ID and value
            parts = line.split(':', 1)
            data_id = parts[0].strip()
            data_value = parts[1].strip()
            info[current_address][data_id] = data_value

    return info

def generate_summary(results):
    """Generate a summary of the query results"""
    success_count = sum(1 for r in results if r['success'])
    total_time = sum(r['elapsed_time'] for r in results)

    summary = StringIO()
    summary.write(f"\n{'='*80}\n")
    summary.write(f"PSA ECU QUERY SUMMARY - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    summary.write(f"{'='*80}\n\n")
    summary.write(f"Total ECUs queried: {len(results)}\n")
    summary.write(f"Successful responses: {success_count}\n")
    summary.write(f"Failed responses: {len(results) - success_count}\n")
    summary.write(f"Total query time: {total_time:.2f} seconds\n\n")

    # Table header
    summary.write(f"{'ECU Family':<20} {'Request ID':<12} {'Response ID':<12} {'Status':<10} {'Time (s)':<10}\n")
    summary.write(f"{'-'*20} {'-'*12} {'-'*12} {'-'*10} {'-'*10}\n")

    # Table rows
    for result in sorted(results, key=lambda x: x['family']):
        status = "SUCCESS" if result['success'] else "FAILED"
        req_id = f"0x{result['request']:X}"
        resp_id = f"0x{result['response']:X}"
        summary.write(f"{result['family']:<20} {req_id:<12} {resp_id:<12} {status:<10} {result['elapsed_time']:.2f}\n")

    return summary.getvalue()

def generate_detailed_report(results):
    """Generate a detailed report of all ECU responses"""
    report = StringIO()
    report.write(f"\n{'='*80}\n")
    report.write(f"PSA ECU DETAILED REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    report.write(f"{'='*80}\n\n")

    for result in sorted(results, key=lambda x: x['family']):
        report.write(f"\n{'-'*80}\n")
        report.write(f"ECU: {result['family']} (Request: 0x{result['request']:X}, Response: 0x{result['response']:X})\n")
        report.write(f"Status: {'SUCCESS' if result['success'] else 'FAILED'}\n")
        report.write(f"Query Time: {result['elapsed_time']:.2f} seconds\n")
        report.write(f"{'-'*80}\n\n")

        if result['success']:
            # Parse and display structured data
            ecu_info = parse_ecu_response(result['output'])
            if ecu_info:
                for addr, data in ecu_info.items():
                    report.write(f"Address: {addr}\n")
                    report.write(f"{'-'*40}\n")
                    for data_id, value in data.items():
                        report.write(f"{data_id}: {value}\n")
                    report.write("\n")
            else:
                report.write("No structured data could be extracted.\n")
                report.write("Raw output:\n")
                report.write(result['output'])
        else:
            report.write("Query failed. Raw output:\n")
            report.write(result['output'])

    return report.getvalue()

def check_environment():
    """Check if the environment is properly set up"""
    if not os.path.exists("query_fw_versions.py"):
        print("Error: query_fw_versions.py not found in current directory!")
        return False
    return True

def main():
    parser = argparse.ArgumentParser(description="Query PSA ECUs from CSV list")
    parser.add_argument("--csv", default="psa_ecu_ids.csv", help="CSV file with ECU IDs")
    parser.add_argument("--bus", type=int, default=0, help="Bus number")
    parser.add_argument("--rxoffset", default="-20", help="RX offset")
    parser.add_argument("--timeout", type=float, default=1.5, help="Timeout for each query")
    parser.add_argument("--nonstandard", action="store_true", help="Include non-standard identifiers")
    parser.add_argument("--output", help="Output file for detailed report")
    parser.add_argument("--summary", help="Output file for summary report")
    args = parser.parse_args()

    # Check environment
    if not check_environment():
        sys.exit(1)

    print(f"\n{'='*80}")
    print(f"PSA ECU QUERY TOOL - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")
    print(f"CSV File: {args.csv}")
    print(f"Bus: {args.bus}")
    print(f"RX Offset: {args.rxoffset}")
    print(f"Timeout: {args.timeout}s")
    print(f"Non-standard identifiers: {'Yes' if args.nonstandard else 'No'}")

    # Load ECU IDs from CSV
    ecus = load_ecu_ids(args.csv)
    if not ecus:
        print("No ECUs found in CSV file. Exiting.")
        sys.exit(1)

    print(f"\nFound {len(ecus)} ECUs in CSV file.")

    # Query each ECU
    results = []
    for i, ecu in enumerate(ecus, 1):
        print(f"\nProcessing ECU {i}/{len(ecus)}: {ecu['family']}")
        result = query_ecu(ecu, args.bus, args.rxoffset, args.timeout, args.nonstandard)
        results.append(result)

    # Generate summary and report
    summary = generate_summary(results)
    report = generate_detailed_report(results)

    # Print summary to console
    print(summary)

    # Save reports to files if specified
    if args.summary:
        with open(args.summary, 'w') as f:
            f.write(summary)
        print(f"Summary saved to {args.summary}")

    if args.output:
        with open(args.output, 'w') as f:
            f.write(report)
        print(f"Detailed report saved to {args.output}")

    # Summary of firmware information
    print("\nECU FIRMWARE INFORMATION SUMMARY")
    print(f"{'-'*80}")

    # Count ECUs with different types of information
    ecu_with_fw = 0
    ecu_with_hw = 0
    success_count = sum(1 for r in results if r['success'])

    for result in results:
        if result['success']:
            ecu_info = parse_ecu_response(result['output'])
            has_fw = False
            has_hw = False

            for addr, data in ecu_info.items():
                for data_id, value in data.items():
                    if "SOFTWARE" in data_id or "FIRMWARE" in data_id:
                        has_fw = True
                    if "HARDWARE" in data_id:
                        has_hw = True

            if has_fw:
                ecu_with_fw += 1
            if has_hw:
                ecu_with_hw += 1

    print(f"ECUs with firmware info: {ecu_with_fw}/{success_count}")
    print(f"ECUs with hardware info: {ecu_with_hw}/{success_count}")

    print(f"\nQuery completed. Total time: {sum(r['elapsed_time'] for r in results):.2f} seconds")

if __name__ == "__main__":
    main()