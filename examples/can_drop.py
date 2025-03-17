#!/usr/bin/env python3
import csv
import sys

CSV_KEYS = {
    "logger": {
        "time": "Time",
        "message_id": "MessageID",
        "data": "Message",
        "bus": "Bus"
    },
    "cabana": {
        "time": "time",
        "message_id": "addr",
        "data": "data",
        "bus": "bus"
    }
}

def load_message_counts(filename, start, end):
    """
    Loads message counts from a CSV log between start and end times.
    """
    counts = {}
    with open(filename, newline='') as inp:
        reader = csv.DictReader(inp)
        dtype = None
        for row in reader:
            if not len(row):
                continue
            if dtype is None:
                dtype = "logger" if "Bus" in row else "cabana"

            time = float(row[CSV_KEYS[dtype]["time"]])
            bus = int(row[CSV_KEYS[dtype]["bus"]])
            if time < start or bus > 127:
                continue
            elif time > end:
                break

            message_id = row[CSV_KEYS[dtype]["message_id"]]
            if message_id.startswith('0x'):
                message_id = message_id[2:]
            else:
                message_id = hex(int(message_id))[2:]
            message_id = f'{bus}:{message_id}'

            counts[message_id] = counts.get(message_id, 0) + 1

    duration = end - start
    rates = {msg_id: count / duration for msg_id, count in counts.items()}
    return rates

def compare_rates(baseline_rates, compare_rates, threshold=0.5):
    """
    Compares message rates between baseline and compare intervals.
    Flags messages whose rate dropped below the threshold.
    """
    dropped_messages = []
    for msg_id, base_rate in baseline_rates.items():
        compare_rate = compare_rates.get(msg_id, 0)
        if base_rate == 0:
            continue  # Avoid div by zero, or log if necessary
        drop_ratio = compare_rate / base_rate
        if drop_ratio < threshold:
            dropped_messages.append((msg_id, base_rate, compare_rate, drop_ratio))

    return dropped_messages

def print_dropped_messages(dropped_messages):
    if not dropped_messages:
        print("No messages found with significant drop in rate.")
        return

    print("Messages with significant rate drop:")
    print(f"{'Message ID':<15} {'Baseline Rate (Hz)':<20} {'New Rate (Hz)':<15} {'Drop Ratio':<10}")
    for msg_id, base_rate, new_rate, drop_ratio in dropped_messages:
        print(f"{msg_id:<15} {base_rate:<20.3f} {new_rate:<15.3f} {drop_ratio:<10.2f}")

def main():
    if len(sys.argv) < 4:
        print(f'Usage:\n{sys.argv[0]} log.csv <baseline-start>-<baseline-end> <compare-start>-<compare-end>')
        sys.exit(0)

    log_file = sys.argv[1]
    baseline_range = sys.argv[2]
    compare_range = sys.argv[3]

    baseline_start, baseline_end = map(float, baseline_range.split('-'))
    compare_start, compare_end = map(float, compare_range.split('-'))

    print(f"Analyzing baseline period {baseline_start}-{baseline_end}...")
    baseline_rates = load_message_counts(log_file, baseline_start, baseline_end)

    print(f"Analyzing compare period {compare_start}-{compare_end}...")
    compare_rates_data = load_message_counts(log_file, compare_start, compare_end)

    dropped_msgs = compare_rates(baseline_rates, compare_rates_data, threshold=0.5)

    print_dropped_messages(dropped_msgs)

if __name__ == "__main__":
    main()
