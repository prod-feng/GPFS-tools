#!/usr/bin/env python3
#
# This scipt is a "top" like command to monitor the IBM ESS storage cluster's IO performance.
# You need to run it on one of the quorum server.
#
# GPFS ESS / GNR I/O TOP
#
# Python 3.6+
#
# Views:
#   1 = NODE
#   2 = PDISK
#   3 = RECOVERY GROUP
#   4 = DECLUSTERED ARRAY
#
# Sort:
#   r = READ/s
#   w = WRITE/s
#   i = READ IOPS
#   j = WRITE IOPS
#   e = TOTAL ERRORS
#   n = NAME
#
# PDISK-only sort:
#   t = TIMEOUT
#   m = MEDIA ERRORS
#   c = CHECKSUM ERRORS
#   p = PATH ERRORS
#
# PDISK paging:
#   Up / Down
#   PageUp / PageDown
#   Home / End
#
# Quit:
#   q
#

from __future__ import print_function

import curses
import subprocess
import threading
import time


MMLSPDISK = "/usr/lpp/mmfs/bin/mmlspdisk"

REFRESH = 5.0

PDISKS_PER_PAGE = 20


# ============================================================
# Formatting
# ============================================================

def fmt_bytes(value):

    if value < 1024.0:
        return "{:.0f}B".format(value)

    if value < 1024.0 ** 2:
        return "{:.1f}K".format(
            value / 1024.0
        )

    if value < 1024.0 ** 3:
        return "{:.1f}M".format(
            value / (1024.0 ** 2)
        )

    if value < 1024.0 ** 4:
        return "{:.2f}G".format(
            value / (1024.0 ** 3)
        )

    return "{:.2f}T".format(
        value / (1024.0 ** 4)
    )


def fmt_iops(value):

    if value < 1000.0:
        return "{:.0f}".format(value)

    if value < 1000000.0:
        return "{:.1f}K".format(
            value / 1000.0
        )

    return "{:.2f}M".format(
        value / 1000000.0
    )


def short_node(name):

    if not name:
        return "?"

    name = name.strip()

    if "-ib." in name:
        return name.split(
            "-ib.",
            1
        )[0]

    return name.split(
        ".",
        1
    )[0]


def short_rg(name):

    if not name:
        return "?"

    if "ess6k_3a_ib_ess6k_3b_ib" in name:
        return "RG3a-3b"

    if "ess6k_4a_ib_ess6k_4b_ib" in name:
        return "RG4a-4b"

    return name[:12]


def short_node_pair(nodes):

    if not nodes:
        return "?"

    values = []

    for node in nodes:

        value = short_node(node)

        if value not in values:
            values.append(value)

    if len(values) == 1:
        return values[0]

    return "/".join(values)


def intval(data, key):

    try:
        return int(
            data.get(
                key,
                "0"
            )
        )
    except Exception:
        return 0


def floatval(data, key):

    try:
        return float(
            data.get(
                key,
                "0"
            )
        )
    except Exception:
        return 0.0


# ============================================================
# Error counters
# ============================================================

ERROR_FIELDS = (
    "IOErrors",
    "IOTimeouts",
    "mediaErrors",
    "checksumErrors",
    "pathErrors"
)


def total_errors(data):

    return (
        intval(data, "IOErrors")
        +
        intval(data, "IOTimeouts")
        +
        intval(data, "mediaErrors")
        +
        intval(data, "checksumErrors")
        +
        intval(data, "pathErrors")
    )


# ============================================================
# Device node parser
# ============================================================

def parse_device_nodes(device):

    nodes = []

    if not device:
        return nodes

    for part in device.split(","):

        part = part.strip()

        if not part.startswith("//"):
            continue

        value = part[2:]

        if "/dev/" not in value:
            continue

        node = value.split(
            "/dev/",
            1
        )[0]

        if node not in nodes:
            nodes.append(node)

    return nodes


# ============================================================
# Parse one pdisk record
# ============================================================

def parse_record(lines):

    data = {}

    for line in lines:

        line = line.strip()

        if "=" not in line:
            continue

        key, value = line.split(
            "=",
            1
        )

        key = key.strip()
        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == '"'
            and value[-1] == '"'
        ):
            value = value[1:-1]

        data[key] = value

    data["device_nodes"] = parse_device_nodes(
        data.get(
            "device",
            ""
        )
    )

    return data


# ============================================================
# Parse mmlspdisk output
# ============================================================

def parse_mmlspdisk(text):

    records = []

    current = []

    for line in text.splitlines():

        stripped = line.strip()

        if stripped.startswith(
            "replacementPriority"
        ):

            if current:

                record = parse_record(
                    current
                )

                if record.get("name"):
                    records.append(record)

            current = [line]

        elif current:

            current.append(line)

    if current:

        record = parse_record(
            current
        )

        if record.get("name"):
            records.append(record)

    return records


# ============================================================
# Run mmlspdisk
# ============================================================

def collect():

    start = time.time()

    process = subprocess.Popen(
        [
            MMLSPDISK,
            "all"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    stdout, stderr = process.communicate()

    elapsed = time.time() - start

    if process.returncode != 0:

        raise RuntimeError(
            stderr.decode(
                "utf-8",
                "replace"
            ).strip()
        )

    text = stdout.decode(
        "utf-8",
        "replace"
    )

    records = parse_mmlspdisk(
        text
    )

    return records, elapsed


# ============================================================
# Calculate rates
# ============================================================

def calc_rate(
    old,
    new,
    elapsed
):

    rate = {
        "read_bps": 0.0,
        "write_bps": 0.0,
        "read_iops": 0.0,
        "write_iops": 0.0
    }

    if old is None:
        return rate

    if elapsed <= 0:
        return rate

    old_reads = intval(
        old,
        "reads"
    )

    new_reads = intval(
        new,
        "reads"
    )

    old_writes = intval(
        old,
        "writes"
    )

    new_writes = intval(
        new,
        "writes"
    )

    old_br = floatval(
        old,
        "bytesReadInGiB"
    )

    new_br = floatval(
        new,
        "bytesReadInGiB"
    )

    old_bw = floatval(
        old,
        "bytesWrittenInGiB"
    )

    new_bw = floatval(
        new,
        "bytesWrittenInGiB"
    )

    if new_reads < old_reads:
        return rate

    if new_writes < old_writes:
        return rate

    if new_br < old_br:
        return rate

    if new_bw < old_bw:
        return rate

    read_ops = (
        new_reads -
        old_reads
    )

    write_ops = (
        new_writes -
        old_writes
    )

    read_gib = (
        new_br -
        old_br
    )

    write_gib = (
        new_bw -
        old_bw
    )

    rate["read_iops"] = (
        read_ops /
        elapsed
    )

    rate["write_iops"] = (
        write_ops /
        elapsed
    )

    rate["read_bps"] = (
        read_gib *
        1024.0 *
        1024.0 *
        1024.0 /
        elapsed
    )

    rate["write_bps"] = (
        write_gib *
        1024.0 *
        1024.0 *
        1024.0 /
        elapsed
    )

    return rate


# ============================================================
# Monitor
# ============================================================

class Monitor(object):

    def __init__(self):

        self.lock = threading.Lock()

        self.current = {}

        self.last_sample = None

        self.command_time = 0.0

        self.error = ""

        self.running = True

        self.thread = threading.Thread(
            target=self.worker
        )

        self.thread.daemon = True

    def start(self):

        self.thread.start()

    def stop(self):

        self.running = False

        self.thread.join(
            3.0
        )

    def worker(self):

        while self.running:

            try:

                records, command_time = collect()

                now = time.time()

                with self.lock:

                    previous = self.current

                    current = {}

                    if self.last_sample is None:

                        interval = 0.0

                    else:

                        interval = (
                            now -
                            self.last_sample
                        )

                    self.last_sample = now

                    for record in records:

                        name = record.get(
                            "name"
                        )

                        rg = record.get(
                            "recoveryGroup",
                            ""
                        )

                        if not name:
                            continue

                        key = (
                            rg,
                            name
                        )

                        old = previous.get(
                            key
                        )

                        item = dict(
                            record
                        )

                        item.update(
                            calc_rate(
                                old,
                                record,
                                interval
                            )
                        )

                        current[key] = item

                    self.current = current

                    self.command_time = (
                        command_time
                    )

                    self.error = ""

            except Exception as exc:

                with self.lock:

                    self.error = str(
                        exc
                    )

            end = (
                time.time()
                +
                REFRESH
            )

            while (
                self.running
                and
                time.time() < end
            ):

                time.sleep(
                    0.1
                )

    def snapshot(self):

        with self.lock:

            return (
                list(
                    self.current.values()
                ),
                self.error,
                self.command_time,
                self.last_sample
            )


# ============================================================
# Aggregation
# ============================================================

def new_total():

    return {
        "read_bps": 0.0,
        "write_bps": 0.0,
        "read_iops": 0.0,
        "write_iops": 0.0,

        "IOErrors": 0,
        "IOTimeouts": 0,
        "mediaErrors": 0,
        "checksumErrors": 0,
        "pathErrors": 0,

        "pdisks": 0
    }


def add_record(
    total,
    record
):

    total["read_bps"] += record.get(
        "read_bps",
        0.0
    )

    total["write_bps"] += record.get(
        "write_bps",
        0.0
    )

    total["read_iops"] += record.get(
        "read_iops",
        0.0
    )

    total["write_iops"] += record.get(
        "write_iops",
        0.0
    )

    for field in ERROR_FIELDS:

        total[field] += intval(
            record,
            field
        )

    total["pdisks"] += 1


def aggregate_total(records):

    total = new_total()

    for record in records:

        add_record(
            total,
            record
        )

    return total


def aggregate_rg(records):

    groups = {}

    for record in records:

        rg = record.get(
            "recoveryGroup",
            "?"
        )

        if rg not in groups:

            groups[rg] = new_total()

        add_record(
            groups[rg],
            record
        )

    return groups


def aggregate_da(records):

    groups = {}

    for record in records:

        key = (
            record.get(
                "recoveryGroup",
                "?"
            ),
            record.get(
                "declusteredArray",
                "?"
            )
        )

        if key not in groups:

            groups[key] = new_total()

        add_record(
            groups[key],
            record
        )

    return groups


def aggregate_node(records):

    groups = {}

    for record in records:

        nodes = record.get(
            "device_nodes",
            []
        )

        pair = short_node_pair(
            nodes
        )

        if pair not in groups:

            groups[pair] = new_total()

        add_record(
            groups[pair],
            record
        )

    return groups


# ============================================================
# Sorting
# ============================================================

def sort_value(
    data,
    mode
):

    if mode == "read":

        return data.get(
            "read_bps",
            0.0
        )

    if mode == "write":

        return data.get(
            "write_bps",
            0.0
        )

    if mode == "riops":

        return data.get(
            "read_iops",
            0.0
        )

    if mode == "wiops":

        return data.get(
            "write_iops",
            0.0
        )

    if mode == "errors":

        return total_errors(
            data
        )

    if mode == "timeout":

        return intval(
            data,
            "IOTimeouts"
        )

    if mode == "media":

        return intval(
            data,
            "mediaErrors"
        )

    if mode == "checksum":

        return intval(
            data,
            "checksumErrors"
        )

    if mode == "path":

        return intval(
            data,
            "pathErrors"
        )

    return data.get(
        "name",
        ""
    )


def sort_records(
    records,
    mode
):

    records = list(records)

    if mode == "name":

        records.sort(
            key=lambda x:
                x.get(
                    "name",
                    ""
                ).lower()
        )

    else:

        records.sort(
            key=lambda x:
                sort_value(
                    x,
                    mode
                ),
            reverse=True
        )

    return records


# ============================================================
# Header
# ============================================================

def draw_header(
    stdscr,
    records,
    command_time,
    last_sample,
    error,
    view,
    sort_mode
):

    height, width = stdscr.getmaxyx()

    now = time.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    title = (
        "ESS TOP  {}  PDISKS={}  "
        "mmlspdisk={:.2f}s"
    ).format(
        now,
        len(records),
        command_time
    )

    stdscr.addnstr(
        0,
        0,
        title,
        width - 1,
        curses.A_BOLD
    )

    controls = (
        "1 NODE  2 PDISK  3 RG  4 DA   "
        "r READ  w WRITE  i R-IOPS  j W-IOPS  "
        "e ERRORS  n NAME  q QUIT"
    )

    stdscr.addnstr(
        1,
        0,
        controls,
        width - 1
    )

    if view == "pdisk":

        pdisk_controls = (
            "PDISK SORT: "
            "t TIMEOUT  m MEDIA  c CHECKSUM  p PATH"
        )

        stdscr.addnstr(
            2,
            0,
            pdisk_controls,
            width - 1
        )

    if error:

        stdscr.addnstr(
            3,
            0,
            "ERROR: " + error,
            width - 1
        )

    elif not records:

        stdscr.addnstr(
            3,
            0,
            "Waiting for mmlspdisk...",
            width - 1
        )

    else:

        if last_sample is not None:

            age = (
                time.time()
                -
                last_sample
            )

            sort_names = {
                "read": "READ/s",
                "write": "WRITE/s",
                "riops": "READ IOPS",
                "wiops": "WRITE IOPS",
                "errors": "ERRORS",
                "name": "NAME",
                "timeout": "TIMEOUT",
                "media": "MEDIA",
                "checksum": "CHECKSUM",
                "path": "PATH"
            }

            status = (
                "Sample age: {:.1f}s   "
                "Refresh: {:.1f}s   "
                "Sort: {}"
            ).format(
                age,
                REFRESH,
                sort_names.get(
                    sort_mode,
                    sort_mode
                )
            )

        else:

            status = (
                "Waiting for first sample..."
            )

        stdscr.addnstr(
            3,
            0,
            status,
            width - 1
        )


# ============================================================
# VIEW 1 - NODE
# ============================================================

def draw_node(
    stdscr,
    records,
    sort_mode
):

    groups = aggregate_node(
        records
    )

    items = list(
        groups.items()
    )

    items.sort(
        key=lambda x:
            sort_value(
                x[1],
                sort_mode
            ),
        reverse=True
    )

    width = stdscr.getmaxyx()[1]

    header = (
        "{:<18} "
        "{:>7} "
        "{:>12} "
        "{:>12} "
        "{:>10} "
        "{:>10} "
        "{:>8}"
    ).format(
        "NODE",
        "PDISKS",
        "READ/s",
        "WRITE/s",
        "R-IOPS",
        "W-IOPS",
        "ERRORS"
    )

    stdscr.addnstr(
        5,
        0,
        header,
        width - 1,
        curses.A_REVERSE
    )

    row = 6

    for name, data in items:

        if row >= stdscr.getmaxyx()[0] - 5:
            break

        line = (
            "{:<18} "
            "{:>7} "
            "{:>12} "
            "{:>12} "
            "{:>10} "
            "{:>10} "
            "{:>8}"
        ).format(
            name[:18],
            data["pdisks"],
            fmt_bytes(
                data["read_bps"]
            ),
            fmt_bytes(
                data["write_bps"]
            ),
            fmt_iops(
                data["read_iops"]
            ),
            fmt_iops(
                data["write_iops"]
            ),
            total_errors(
                data
            )
        )

        stdscr.addnstr(
            row,
            0,
            line,
            width - 1
        )

        row += 1

    draw_summary(
        stdscr,
        row,
        aggregate_total(records),
        width,
        "TOTAL"
    )


# ============================================================
# VIEW 2 - PDISK
# ============================================================

def draw_pdisk(
    stdscr,
    records,
    sort_mode,
    page
):

    records = sort_records(
        records,
        sort_mode
    )

    count = len(records)

    pages = max(
        1,
        (
            count +
            PDISKS_PER_PAGE -
            1
        ) //
        PDISKS_PER_PAGE
    )

    page = max(
        0,
        min(
            page,
            pages - 1
        )
    )

    start = (
        page *
        PDISKS_PER_PAGE
    )

    end = min(
        start +
        PDISKS_PER_PAGE,
        count
    )

    visible = records[
        start:end
    ]

    width = stdscr.getmaxyx()[1]

    header = (
        "{:<7} "
        "{:<11} "
        "{:<8} "
        "{:<4} "
        "{:>9} "
        "{:>9} "
        "{:>7} "
        "{:>7} "
        "{:>6} "
        "{:>7} "
        "{:>6} "
        "{:>7} "
        "{:>5}"
    ).format(
        "PDISK",
        "NODE PAIR",
        "RG",
        "DA",
        "READ/s",
        "WRITE/s",
        "R-IOPS",
        "W-IOPS",
        "IOERR",
        "TIMEOUT",
        "MEDIA",
        "CHKSUM",
        "PATH"
    )

    stdscr.addnstr(
        5,
        0,
        header,
        width - 1,
        curses.A_REVERSE
    )

    row = 6

    for record in visible:

        if row >= stdscr.getmaxyx()[0] - 7:
            break

        pair = short_node_pair(
            record.get(
                "device_nodes",
                []
            )
        )

        line = (
            "{:<7} "
            "{:<11} "
            "{:<8} "
            "{:<4} "
            "{:>9} "
            "{:>9} "
            "{:>7} "
            "{:>7} "
            "{:>6} "
            "{:>7} "
            "{:>6} "
            "{:>7} "
            "{:>5}"
        ).format(
            record.get(
                "name",
                "?"
            )[:7],

            pair[:11],

            short_rg(
                record.get(
                    "recoveryGroup",
                    "?"
                )
            )[:8],

            record.get(
                "declusteredArray",
                "?"
            )[:4],

            fmt_bytes(
                record.get(
                    "read_bps",
                    0.0
                )
            ),

            fmt_bytes(
                record.get(
                    "write_bps",
                    0.0
                )
            ),

            fmt_iops(
                record.get(
                    "read_iops",
                    0.0
                )
            ),

            fmt_iops(
                record.get(
                    "write_iops",
                    0.0
                )
            ),

            intval(
                record,
                "IOErrors"
            ),

            intval(
                record,
                "IOTimeouts"
            ),

            intval(
                record,
                "mediaErrors"
            ),

            intval(
                record,
                "checksumErrors"
            ),

            intval(
                record,
                "pathErrors"
            )
        )

        stdscr.addnstr(
            row,
            0,
            line,
            width - 1
        )

        row += 1

    total = aggregate_total(
        records
    )

    row += 1

    stdscr.addnstr(
        row,
        0,
        "-" * min(
            width - 1,
            120
        ),
        width - 1
    )

    row += 1

    line = (
        "{:<7} "
        "{:<11} "
        "{:<8} "
        "{:<4} "
        "{:>9} "
        "{:>9} "
        "{:>7} "
        "{:>7} "
        "{:>6} "
        "{:>7} "
        "{:>6} "
        "{:>7} "
        "{:>5}"
    ).format(
        "TOTAL",
        "{} PDISKS".format(
            total["pdisks"]
        ),
        "",
        "",
        fmt_bytes(
            total["read_bps"]
        ),
        fmt_bytes(
            total["write_bps"]
        ),
        fmt_iops(
            total["read_iops"]
        ),
        fmt_iops(
            total["write_iops"]
        ),
        total["IOErrors"],
        total["IOTimeouts"],
        total["mediaErrors"],
        total["checksumErrors"],
        total["pathErrors"]
    )

    stdscr.addnstr(
        row,
        0,
        line,
        width - 1,
        curses.A_BOLD
    )

    footer = (
        "PDISK {}-{} / {}    PAGE {}/{}    "
        "Up/Down/PgUp/PgDn: page   Home/End: first/last"
    ).format(
        start + 1 if count else 0,
        end,
        count,
        page + 1,
        pages
    )

    stdscr.addnstr(
        stdscr.getmaxyx()[0] - 2,
        0,
        footer,
        width - 1,
        curses.A_BOLD
    )

    return page


# ============================================================
# VIEW 3 - RECOVERY GROUP
# ============================================================

def draw_rg(
    stdscr,
    records,
    sort_mode
):

    groups = aggregate_rg(
        records
    )

    items = list(
        groups.items()
    )

    items.sort(
        key=lambda x:
            sort_value(
                x[1],
                sort_mode
            ),
        reverse=True
    )

    width = stdscr.getmaxyx()[1]

    header = (
        "{:<14} "
        "{:>7} "
        "{:>12} "
        "{:>12} "
        "{:>10} "
        "{:>10} "
        "{:>8}"
    ).format(
        "RECOVERY GROUP",
        "PDISKS",
        "READ/s",
        "WRITE/s",
        "R-IOPS",
        "W-IOPS",
        "ERRORS"
    )

    stdscr.addnstr(
        5,
        0,
        header,
        width - 1,
        curses.A_REVERSE
    )

    row = 6

    for rg, data in items:

        line = (
            "{:<14} "
            "{:>7} "
            "{:>12} "
            "{:>12} "
            "{:>10} "
            "{:>10} "
            "{:>8}"
        ).format(
            short_rg(rg),
            data["pdisks"],
            fmt_bytes(
                data["read_bps"]
            ),
            fmt_bytes(
                data["write_bps"]
            ),
            fmt_iops(
                data["read_iops"]
            ),
            fmt_iops(
                data["write_iops"]
            ),
            total_errors(
                data
            )
        )

        stdscr.addnstr(
            row,
            0,
            line,
            width - 1
        )

        row += 1

    draw_summary(
        stdscr,
        row,
        aggregate_total(records),
        width,
        "TOTAL"
    )


# ============================================================
# VIEW 4 - DECLUSTERED ARRAY
# ============================================================

def draw_da(
    stdscr,
    records,
    sort_mode
):

    groups = aggregate_da(
        records
    )

    items = list(
        groups.items()
    )

    items.sort(
        key=lambda x:
            sort_value(
                x[1],
                sort_mode
            ),
        reverse=True
    )

    width = stdscr.getmaxyx()[1]

    header = (
        "{:<12} "
        "{:<5} "
        "{:>7} "
        "{:>12} "
        "{:>12} "
        "{:>10} "
        "{:>10} "
        "{:>8}"
    ).format(
        "RG",
        "DA",
        "PDISKS",
        "READ/s",
        "WRITE/s",
        "R-IOPS",
        "W-IOPS",
        "ERRORS"
    )

    stdscr.addnstr(
        5,
        0,
        header,
        width - 1,
        curses.A_REVERSE
    )

    row = 6

    for key, data in items:

        rg, da = key

        line = (
            "{:<12} "
            "{:<5} "
            "{:>7} "
            "{:>12} "
            "{:>12} "
            "{:>10} "
            "{:>10} "
            "{:>8}"
        ).format(
            short_rg(rg),
            da,
            data["pdisks"],
            fmt_bytes(
                data["read_bps"]
            ),
            fmt_bytes(
                data["write_bps"]
            ),
            fmt_iops(
                data["read_iops"]
            ),
            fmt_iops(
                data["write_iops"]
            ),
            total_errors(
                data
            )
        )

        stdscr.addnstr(
            row,
            0,
            line,
            width - 1
        )

        row += 1

    draw_summary(
        stdscr,
        row,
        aggregate_total(records),
        width,
        "TOTAL"
    )


# ============================================================
# Common summary
# ============================================================

def draw_summary(
    stdscr,
    row,
    total,
    width,
    name
):

    row += 1

    stdscr.addnstr(
        row,
        0,
        "-" * min(
            width - 1,
            100
        ),
        width - 1
    )

    row += 1

    line = (
        "{:<18} "
        "{:>7} "
        "{:>12} "
        "{:>12} "
        "{:>10} "
        "{:>10} "
        "{:>8}"
    ).format(
        name,
        total["pdisks"],
        fmt_bytes(
            total["read_bps"]
        ),
        fmt_bytes(
            total["write_bps"]
        ),
        fmt_iops(
            total["read_iops"]
        ),
        fmt_iops(
            total["write_iops"]
        ),
        total_errors(
            total
        )
    )

    stdscr.addnstr(
        row,
        0,
        line,
        width - 1,
        curses.A_BOLD
    )


# ============================================================
# Main curses loop
# ============================================================

def main(stdscr):

    curses.curs_set(0)

    stdscr.nodelay(True)

    monitor = Monitor()

    monitor.start()

    view = "node"

    sort_mode = "riops"

    pdisk_page = 0

    try:

        while True:

            (
                records,
                error,
                command_time,
                last_sample
            ) = monitor.snapshot()

            stdscr.erase()

            draw_header(
                stdscr,
                records,
                command_time,
                last_sample,
                error,
                view,
                sort_mode
            )

            if view == "node":

                draw_node(
                    stdscr,
                    records,
                    sort_mode
                )

            elif view == "pdisk":

                pdisk_page = draw_pdisk(
                    stdscr,
                    records,
                    sort_mode,
                    pdisk_page
                )

            elif view == "rg":

                draw_rg(
                    stdscr,
                    records,
                    sort_mode
                )

            elif view == "da":

                draw_da(
                    stdscr,
                    records,
                    sort_mode
                )

            stdscr.refresh()

            key = stdscr.getch()

            if key == -1:

                time.sleep(
                    0.1
                )

                continue

            # ------------------------------------------------
            # Quit
            # ------------------------------------------------

            if key in (
                ord("q"),
                ord("Q")
            ):

                break

            # ------------------------------------------------
            # Views
            # ------------------------------------------------

            elif key == ord("1"):

                view = "node"

            elif key == ord("2"):

                view = "pdisk"

                pdisk_page = 0

            elif key == ord("3"):

                view = "rg"

            elif key == ord("4"):

                view = "da"

            # ------------------------------------------------
            # Common sorting
            # ------------------------------------------------

            elif key in (
                ord("r"),
                ord("R")
            ):

                sort_mode = "read"

                pdisk_page = 0

            elif key in (
                ord("w"),
                ord("W")
            ):

                sort_mode = "write"

                pdisk_page = 0

            elif key in (
                ord("i"),
                ord("I")
            ):

                sort_mode = "riops"

                pdisk_page = 0

            elif key in (
                ord("j"),
                ord("J")
            ):

                sort_mode = "wiops"

                pdisk_page = 0

            elif key in (
                ord("e"),
                ord("E")
            ):

                sort_mode = "errors"

                pdisk_page = 0

            elif key in (
                ord("n"),
                ord("N")
            ):

                sort_mode = "name"

                pdisk_page = 0

            # ------------------------------------------------
            # PDISK-only error sorting
            # ------------------------------------------------

            elif (
                view == "pdisk"
                and
                key in (
                    ord("t"),
                    ord("T")
                )
            ):

                sort_mode = "timeout"

                pdisk_page = 0

            elif (
                view == "pdisk"
                and
                key in (
                    ord("m"),
                    ord("M")
                )
            ):

                sort_mode = "media"

                pdisk_page = 0

            elif (
                view == "pdisk"
                and
                key in (
                    ord("c"),
                    ord("C")
                )
            ):

                sort_mode = "checksum"

                pdisk_page = 0

            elif (
                view == "pdisk"
                and
                key in (
                    ord("p"),
                    ord("P")
                )
            ):

                sort_mode = "path"

                pdisk_page = 0

            # ------------------------------------------------
            # PDISK paging
            # ------------------------------------------------

            elif view == "pdisk":

                total = len(
                    records
                )

                pages = max(
                    1,
                    (
                        total +
                        PDISKS_PER_PAGE -
                        1
                    ) //
                    PDISKS_PER_PAGE
                )

                if key == curses.KEY_UP:

                    pdisk_page -= 1

                elif key == curses.KEY_DOWN:

                    pdisk_page += 1

                elif key == curses.KEY_PPAGE:

                    pdisk_page -= 1

                elif key == curses.KEY_NPAGE:

                    pdisk_page += 1

                elif key == curses.KEY_HOME:

                    pdisk_page = 0

                elif key == curses.KEY_END:

                    pdisk_page = (
                        pages - 1
                    )

                pdisk_page = max(
                    0,
                    min(
                        pdisk_page,
                        pages - 1
                    )
                )

    finally:

        monitor.stop()


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":

    curses.wrapper(
        main
    )
