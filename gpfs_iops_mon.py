#!/usr/bin/env python3

import argparse
import csv
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import shutil
import select
import termios
import tty
from datetime import datetime


MMPMON = "/usr/lpp/mmfs/bin/mmpmon"

running = True


# ======================================================================
# SIGNAL HANDLING
# ======================================================================

def stop_handler(signum, frame):
    global running
    running = False


signal.signal(signal.SIGINT, stop_handler)
signal.signal(signal.SIGTERM, stop_handler)


# ======================================================================
# NODE LIST
# ======================================================================

def load_nodes(filename):

    nodes = []

    with open(filename, "r") as f:

        for line in f:

            line = line.strip()

            if not line or line.startswith("#"):
                continue

            node = line.split()[0]

            if node not in nodes:
                nodes.append(node)

    return nodes


# ======================================================================
# MMPMON PARSER
# ======================================================================

def parse_mmpmon(line):

    if "_io_s_" not in line:
        return None

    fields = dict(
        re.findall(
            r"_(\w+)_\s+(\S+)",
            line
        )
    )

    try:

        if fields.get("rc", "0") != "0":
            return None

        node = fields.get("nn")

        if not node:
            return None

        sample_time = (
            float(fields.get("t", 0)) +
            float(fields.get("tu", 0)) /
            1000000.0
        )

        return {
            "node": node,
            "time": sample_time,

            "read": int(
                fields.get("rdc", 0)
            ),

            "write": int(
                fields.get("wc", 0)
            ),

            "bytes_read": int(
                fields.get("br", 0)
            ),

            "bytes_write": int(
                fields.get("bw", 0)
            ),
        }

    except (ValueError, TypeError):

        return None


# ======================================================================
# RATE CALCULATION
# ======================================================================

def calculate_rates(current, previous):

    if previous is None:
        return None

    dt = (
        current["time"] -
        previous["time"]
    )

    if dt <= 0:
        return None

    read_ops = (
        current["read"] -
        previous["read"]
    )

    write_ops = (
        current["write"] -
        previous["write"]
    )

    read_bytes = (
        current["bytes_read"] -
        previous["bytes_read"]
    )

    write_bytes = (
        current["bytes_write"] -
        previous["bytes_write"]
    )

    # Protect against counter resets.

    if read_ops < 0:
        read_ops = 0

    if write_ops < 0:
        write_ops = 0

    if read_bytes < 0:
        read_bytes = 0

    if write_bytes < 0:
        write_bytes = 0

    read_iops = read_ops / dt
    write_iops = write_ops / dt

    read_mbps = (
        read_bytes /
        dt /
        1024.0 /
        1024.0
    )

    write_mbps = (
        write_bytes /
        dt /
        1024.0 /
        1024.0
    )

    return {
        "read_iops": read_iops,
        "write_iops": write_iops,

        "read_mbps": read_mbps,
        "write_mbps": write_mbps,

        "total_iops":
            read_iops + write_iops,

        "total_mbps":
            read_mbps + write_mbps,

        "dt": dt,
    }


# ======================================================================
# START MMPMON
# ======================================================================

def start_mmpmon(nodes, interval, batch_number):

    delay_ms = int(
        interval * 1000
    )

    fd, command_file = tempfile.mkstemp(
        prefix="gpfs_mmpmon_",
        suffix=".cmd"
    )

    try:

        with os.fdopen(fd, "w") as f:

            f.write(
                "nlist new " +
                " ".join(nodes) +
                "\n"
            )

            f.write(
                "io_s\n"
            )

    except Exception:

        try:
            os.unlink(command_file)
        except Exception:
            pass

        raise

    cmd = [
        MMPMON,
        "-p",
        "-i",
        command_file,
        "-r",
        "0",
        "-d",
        str(delay_ms),
    ]

    print(
        "Starting mmpmon batch {}: {} nodes".format(
            batch_number,
            len(nodes)
        )
    )

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1
    )

    proc.mmpmon_command_file = command_file
    proc.mmpmon_batch = batch_number

    return proc


# ======================================================================
# MMPMON READER
# ======================================================================

def mmpmon_reader(
    proc,
    samples,
    lock,
    batch_number
):

    while running:

        try:

            line = proc.stdout.readline()

        except Exception:

            break

        if not line:

            if proc.poll() is not None:
                break

            time.sleep(0.05)
            continue

        line = line.strip()

        if not line:
            continue

        sample = parse_mmpmon(line)

        if sample is None:
            continue

        node = sample["node"]

        with lock:

            samples[node] = sample


# ======================================================================
# TERMINAL
# ======================================================================

def terminal_size():

    try:

        size = shutil.get_terminal_size(
            fallback=(120, 40)
        )

        return (
            size.columns,
            size.lines
        )

    except Exception:

        return 120, 40


def clear_screen():

    sys.stdout.write(
        "\033[2J\033[H"
    )

    sys.stdout.flush()


def enable_keyboard():

    if not sys.stdin.isatty():
        return None

    try:

        fd = sys.stdin.fileno()

        old_settings = termios.tcgetattr(
            fd
        )

        tty.setcbreak(fd)

        return old_settings

    except Exception:

        return None


def disable_keyboard(old_settings):

    if old_settings is None:
        return

    try:

        fd = sys.stdin.fileno()

        termios.tcsetattr(
            fd,
            termios.TCSADRAIN,
            old_settings
        )

    except Exception:

        pass


def read_key():

    if not sys.stdin.isatty():
        return None

    try:

        ready, _, _ = select.select(
            [sys.stdin],
            [],
            [],
            0
        )

        if ready:
            return sys.stdin.read(1)

    except Exception:

        return None

    return None


# ======================================================================
# DASHBOARD
# ======================================================================

def draw_dashboard(
    rates,
    expected_nodes,
    batch_count,
    sort_mode,
    page,
    page_size,
    interval,
    top_n,
    node_filter,
    nonzero_only
):

    width, height = terminal_size()

    clear_screen()

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # --------------------------------------------------------------
    # FILTER
    # --------------------------------------------------------------

    filtered_nodes = []

    for node in rates:

        if node_filter:

            if node_filter.lower() not in node.lower():
                continue

        if nonzero_only:

            if (
                rates[node]["total_iops"] <= 0 and
                rates[node]["total_mbps"] <= 0
            ):
                continue

        filtered_nodes.append(node)

    # --------------------------------------------------------------
    # CLUSTER TOTALS
    # --------------------------------------------------------------

    valid_nodes = [
        n for n in filtered_nodes
        if rates[n] is not None
    ]

    # IMPORTANT:
    # Cluster totals should be for ALL reporting nodes,
    # not just the currently displayed page/filter.

    all_nodes = [
        n for n in rates
        if rates[n] is not None
    ]

    reporting = len(all_nodes)

    read_iops = sum(
        rates[n]["read_iops"]
        for n in all_nodes
    )

    write_iops = sum(
        rates[n]["write_iops"]
        for n in all_nodes
    )

    read_mbps = sum(
        rates[n]["read_mbps"]
        for n in all_nodes
    )

    write_mbps = sum(
        rates[n]["write_mbps"]
        for n in all_nodes
    )

    total_iops = (
        read_iops +
        write_iops
    )

    total_mbps = (
        read_mbps +
        write_mbps
    )

    # --------------------------------------------------------------
    # HEADER
    # --------------------------------------------------------------

    print(
        "GPFS I/O MONITOR"
    )

    print(
        "{}   interval={}s   "
        "nodes={}/{}   mmpmon={}".format(
            now,
            interval,
            reporting,
            expected_nodes,
            batch_count
        )
    )

    if node_filter:

        print(
            "FILTER: '{}'".format(
                node_filter
            )
        )

    if nonzero_only:

        print(
            "FILTER: non-zero I/O only"
        )

    print(
        "=" * min(width, 120)
    )

    print(
        "READ   {:>14,.0f} IOPS   {:>12,.1f} MB/s".format(
            read_iops,
            read_mbps
        )
    )

    print(
        "WRITE  {:>14,.0f} IOPS   {:>12,.1f} MB/s".format(
            write_iops,
            write_mbps
        )
    )

    print(
        "TOTAL  {:>14,.0f} IOPS   {:>12,.1f} MB/s".format(
            total_iops,
            total_mbps
        )
    )

    print(
        "=" * min(width, 120)
    )

    # --------------------------------------------------------------
    # SORT
    # --------------------------------------------------------------

    if sort_mode == "read":

        sorted_nodes = sorted(
            valid_nodes,
            key=lambda n:
                rates[n]["read_iops"],
            reverse=True
        )

    elif sort_mode == "write":

        sorted_nodes = sorted(
            valid_nodes,
            key=lambda n:
                rates[n]["write_iops"],
            reverse=True
        )

    elif sort_mode == "mbps":

        sorted_nodes = sorted(
            valid_nodes,
            key=lambda n:
                rates[n]["total_mbps"],
            reverse=True
        )

    elif sort_mode == "alpha":

        sorted_nodes = sorted(
            valid_nodes
        )

    else:

        sorted_nodes = sorted(
            valid_nodes,
            key=lambda n:
                rates[n]["total_iops"],
            reverse=True
        )

    # --------------------------------------------------------------
    # DISPLAY
    # --------------------------------------------------------------

    if top_n > 0:

        display_nodes = (
            sorted_nodes[:top_n]
        )

        page_text = (
            "TOP {}".format(
                min(
                    top_n,
                    len(sorted_nodes)
                )
            )
        )

    else:

        total_pages = max(
            1,
            (
                len(sorted_nodes) +
                page_size - 1
            ) //
            page_size
        )

        if page >= total_pages:

            page = total_pages - 1

        start = (
            page *
            page_size
        )

        end = (
            start +
            page_size
        )

        display_nodes = (
            sorted_nodes[start:end]
        )

        page_text = (
            "PAGE {}/{}".format(
                page + 1,
                total_pages
            )
        )

    print(
        "{:<25} {:>12} {:>12} {:>13} {:>12}".format(
            "NODE",
            "READ",
            "WRITE",
            "TOTAL",
            "MB/s"
        )
    )

    print(
        "-" * min(width, 120)
    )

    for node in display_nodes:

        r = rates[node]

        print(
            "{:<25} "
            "{:>12,.0f} "
            "{:>12,.0f} "
            "{:>13,.0f} "
            "{:>12,.1f}".format(
                node[:25],
                r["read_iops"],
                r["write_iops"],
                r["total_iops"],
                r["total_mbps"]
            )
        )

    print(
        "-" * min(width, 120)
    )

    print(
        "{}   sort={}   "
        "showing={}/{}".format(
            page_text,
            sort_mode.upper(),
            len(display_nodes),
            len(sorted_nodes)
        )
    )

    print(
        "n/p=page  a=alpha  r=read  "
        "w=write  t=total  b=MB/s"
    )

    print(
        "+/-=page size  "
        "f=filter  0=nonzero  x=clear  q=quit"
    )

    return page


# ======================================================================
# MAIN
# ======================================================================

def main():

    global running

    parser = argparse.ArgumentParser(
        description=(
            "GPFS mmpmon I/O monitor "
            "for 400+ nodes"
        )
    )

    parser.add_argument(
        "-n",
        "--nodes",
        default="gpfs_clients.txt"
    )

    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=5.0
    )

    parser.add_argument(
        "-o",
        "--csv",
        default="gpfs_iops.csv"
    )

    parser.add_argument(
        "--per-node",
        action="store_true",
        help=(
            "Write per-node statistics "
            "to CSV"
        )
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=90,
        help=(
            "Nodes per mmpmon process "
            "(default: 90)"
        )
    )

    parser.add_argument(
        "--page-size",
        type=int,
        default=20,
        help=(
            "Nodes displayed per page "
            "(default: 20)"
        )
    )

    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help=(
            "Show only top N nodes; "
            "0 = paging"
        )
    )

    args = parser.parse_args()

    # --------------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------------

    if args.batch_size < 1:

        print(
            "ERROR: batch-size must be > 0"
        )

        sys.exit(1)

    if args.page_size < 1:

        print(
            "ERROR: page-size must be > 0"
        )

        sys.exit(1)

    nodes = load_nodes(
        args.nodes
    )

    if not nodes:

        print(
            "ERROR: No nodes found in {}".format(
                args.nodes
            )
        )

        sys.exit(1)

    # --------------------------------------------------------------
    # SPLIT NODES
    # --------------------------------------------------------------

    batches = []

    for i in range(
        0,
        len(nodes),
        args.batch_size
    ):

        batches.append(
            nodes[
                i:i + args.batch_size
            ]
        )

    print(
        "Total nodes       : {}".format(
            len(nodes)
        )
    )

    print(
        "Batch size        : {}".format(
            args.batch_size
        )
    )

    print(
        "mmpmon processes   : {}".format(
            len(batches)
        )
    )

    print(
        "Sampling interval : {} seconds".format(
            args.interval
        )
    )

    print()

    # --------------------------------------------------------------
    # START MMPMON
    # --------------------------------------------------------------

    processes = []

    samples = {}

    lock = threading.Lock()

    for batch_number, batch in enumerate(
        batches,
        1
    ):

        proc = start_mmpmon(
            batch,
            args.interval,
            batch_number
        )

        processes.append(proc)

        thread = threading.Thread(
            target=mmpmon_reader,
            args=(
                proc,
                samples,
                lock,
                batch_number
            )
        )

        thread.daemon = True

        thread.start()

        proc.mmpmon_reader_thread = thread

    # --------------------------------------------------------------
    # CSV
    # --------------------------------------------------------------

    csv_file = None
    writer = None

    if args.per_node:

        exists = os.path.exists(
            args.csv
        )

        empty = (
            not exists or
            os.path.getsize(args.csv) == 0
        )

        csv_file = open(
            args.csv,
            "a",
            newline=""
        )

        writer = csv.writer(
            csv_file
        )

        if empty:

            writer.writerow([
                "timestamp",
                "node",
                "read_iops",
                "write_iops",
                "total_iops",
                "read_mbps",
                "write_mbps",
                "total_mbps",
            ])

            csv_file.flush()

    # --------------------------------------------------------------
    # RATE STATE
    # --------------------------------------------------------------

    previous = {}

    rates = {}

    last_csv_sample = {}

    sort_mode = "total"

    page = 0

    node_filter = ""

    nonzero_only = False

    last_dashboard = 0

    # --------------------------------------------------------------
    # KEYBOARD
    # --------------------------------------------------------------

    old_terminal = enable_keyboard()

    try:

        while running:

            # ------------------------------------------------------
            # KEYBOARD
            # ------------------------------------------------------

            key = read_key()

            if key:

                key = key.lower()

                if key == "q":

                    running = False
                    continue

                elif key == "n":

                    page += 1

                elif key == "p":

                    page = max(
                        0,
                        page - 1
                    )

                elif key == "a":

                    sort_mode = "alpha"
                    page = 0

                elif key == "r":

                    sort_mode = "read"
                    page = 0

                elif key == "w":

                    sort_mode = "write"
                    page = 0

                elif key == "t":

                    sort_mode = "total"
                    page = 0

                elif key == "b":

                    sort_mode = "mbps"
                    page = 0

                elif key == "+":

                    args.page_size = min(
                        100,
                        args.page_size + 5
                    )

                    page = 0

                elif key == "-":

                    args.page_size = max(
                        5,
                        args.page_size - 5
                    )

                    page = 0

                elif key == "0":

                    nonzero_only = (
                        not nonzero_only
                    )

                    page = 0

                elif key == "x":

                    node_filter = ""

                    page = 0

                elif key == "f":

                    # Interactive filter entry.
                    #
                    # Temporarily restore normal terminal
                    # mode so input works normally.

                    disable_keyboard(
                        old_terminal
                    )

                    print()
                    print(
                        "Enter node filter "
                        "(blank = all): "
                    )

                    try:

                        node_filter = (
                            input().strip()
                        )

                    except EOFError:

                        node_filter = ""

                    old_terminal = (
                        enable_keyboard()
                    )

                    page = 0

            # ------------------------------------------------------
            # COPY NEW SAMPLES
            # ------------------------------------------------------

            with lock:

                sample_list = list(
                    samples.values()
                )

            # ------------------------------------------------------
            # CALCULATE RATES
            # ------------------------------------------------------

            for sample in sample_list:

                node = sample["node"]

                old = previous.get(
                    node
                )

                rate = calculate_rates(
                    sample,
                    old
                )

                previous[node] = sample

                if rate is None:
                    continue

                rates[node] = rate

            # ------------------------------------------------------
            # CSV
            # ------------------------------------------------------

            if writer is not None:

                for node in sorted(
                    rates.keys()
                ):

                    rate = rates[node]

                    sample_time = (
                        previous[node]["time"]
                    )

                    old_csv_time = (
                        last_csv_sample.get(
                            node
                        )
                    )

                    if (
                        old_csv_time is not None and
                        sample_time <= old_csv_time
                    ):
                        continue

                    last_csv_sample[node] = (
                        sample_time
                    )

                    timestamp = (
                        datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    )

                    writer.writerow([
                        timestamp,
                        node,

                        "{:.2f}".format(
                            rate["read_iops"]
                        ),

                        "{:.2f}".format(
                            rate["write_iops"]
                        ),

                        "{:.2f}".format(
                            rate["total_iops"]
                        ),

                        "{:.2f}".format(
                            rate["read_mbps"]
                        ),

                        "{:.2f}".format(
                            rate["write_mbps"]
                        ),

                        "{:.2f}".format(
                            rate["total_mbps"]
                        ),
                    ])

                csv_file.flush()

            # ------------------------------------------------------
            # DASHBOARD
            # ------------------------------------------------------

            now = time.monotonic()

            if (
                last_dashboard == 0 or
                now - last_dashboard >=
                args.interval
            ):

                page = draw_dashboard(
                    rates,
                    len(nodes),
                    len(batches),
                    sort_mode,
                    page,
                    args.page_size,
                    args.interval,
                    args.top,
                    node_filter,
                    nonzero_only
                )

                last_dashboard = now

            time.sleep(0.05)

    except KeyboardInterrupt:

        running = False

    finally:

        running = False

        disable_keyboard(
            old_terminal
        )

        # ----------------------------------------------------------
        # STOP MMPMON
        # ----------------------------------------------------------

        for proc in processes:

            try:
                proc.terminate()
            except Exception:
                pass

        for proc in processes:

            try:

                proc.wait(
                    timeout=5
                )

            except Exception:

                try:
                    proc.kill()
                except Exception:
                    pass

        # ----------------------------------------------------------
        # CLOSE CSV
        # ----------------------------------------------------------

        if csv_file is not None:

            try:
                csv_file.close()
            except Exception:
                pass

        # ----------------------------------------------------------
        # REMOVE COMMAND FILES
        # ----------------------------------------------------------

        for proc in processes:

            command_file = getattr(
                proc,
                "mmpmon_command_file",
                None
            )

            if command_file:

                try:
                    os.unlink(
                        command_file
                    )

                except Exception:

                    pass

        print()


if __name__ == "__main__":
    main()

