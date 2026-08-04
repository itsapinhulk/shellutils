#!/usr/bin/env python3
"""
Compute per-period and cumulative percentage changes from a CSV time series,
such as the CSV emitted by `view-json -o csv`.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import sys

from typing import Any

# Display formats, one level finer than the period itself.
PERIOD_FORMATS = {
    'hourly': '%Y-%m-%d %H:%M',
    'daily': '%Y-%m-%d %H',
    'monthly': '%Y-%m-%d',
}


def parse_date_value(value: str) -> float | None:
    """Parse a date/time string into a local-time Unix timestamp, or None."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    return None


def to_timestamp(value: Any) -> float | None:
    """Coerce a raw field value into a Unix timestamp, or None."""
    if isinstance(value, str):
        parsed = parse_date_value(value.strip())
        if parsed is not None:
            return parsed
        try:
            return float(value)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def to_number(value: Any) -> float | None:
    """Coerce a raw field value into a float, or None."""
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def format_percent(base: float | None, value: float) -> str:
    """Percentage change of value against base."""
    if base is None:
        return ''
    if base == 0:
        return 'n/a'
    return f"{(value - base) / base * 100:+.2f}%"


def period_start(ts: float, mode: str) -> datetime.datetime:
    """Start of the period containing the given timestamp, in local time."""
    dt = datetime.datetime.fromtimestamp(ts)
    if mode == 'hourly':
        return dt.replace(minute=0, second=0, microsecond=0)
    if mode == 'daily':
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_period(start: datetime.datetime, mode: str) -> datetime.datetime:
    """Start of the period following the given period start."""
    if mode == 'hourly':
        return start + datetime.timedelta(hours=1)
    if mode == 'daily':
        return start + datetime.timedelta(days=1)
    return (start + datetime.timedelta(days=31)).replace(day=1)


def period_end(start: datetime.datetime, mode: str) -> datetime.datetime:
    """Last instant of the period beginning at the given start."""
    return next_period(start, mode) - datetime.timedelta(seconds=1)


def read_rows(filepaths: list[str]) -> tuple[list[dict], list[str]]:
    """Read CSV rows from the given files, or stdin for '-' / no files."""
    rows = []
    header = None
    for filepath in (filepaths or ['-']):
        if filepath == '-':
            reader = csv.DictReader(sys.stdin)
            rows.extend(reader)
            fieldnames = reader.fieldnames
        else:
            with open(filepath, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows.extend(reader)
                fieldnames = reader.fieldnames
        if fieldnames is None:
            continue
        if (header is not None) and (list(fieldnames) != header):
            raise ValueError(f"{filepath} has a different header: {list(fieldnames)} != {header}")
        header = list(fieldnames)
    return rows, (header or [])


def compute_percent_changes(rows: list[dict], value_field: str, id_fields: list[str],
                            time_field: str, mode: str, snap_forward: bool = False,
                            collapse: bool = False) -> list[dict]:
    """Snap value_field to the last value seen in each period, per id, and
    compute per-period and cumulative percentage changes. Each period is
    reported at the time of its last record, truncated to one level finer than
    the period; periods with no records carry the previous period's value
    forward and show the period boundary instead. Each series opens with a row
    for its earliest record, which is the baseline the first period's change and
    every cumulative change are measured against. With snap_forward, every id is
    projected to the last period present in the data rather than stopping at its
    own. With collapse, the ids are summed per period and the percentages
    describe the summed series."""
    end_fmt = PERIOD_FORMATS[mode]

    snapped: dict[tuple[tuple, datetime.datetime], tuple[float, float]] = {}
    openings: dict[tuple, tuple[float, float]] = {}
    for row in rows:
        ident = tuple(row.get(f) for f in id_fields)
        ts = to_timestamp(row.get(time_field))
        raw = row.get(value_field)
        if any((v is None) or (v == '') for v in ident):
            continue
        if (ts is None) or (raw is None) or (raw == ''):
            continue
        value = to_number(raw)
        if value is None:
            raise ValueError(f"{value_field} is not numeric: {raw!r}")
        try:
            period = period_start(ts, mode)
        except (ValueError, OSError, OverflowError):
            continue
        key = (ident, period)
        previous = snapped.get(key)
        if (previous is None) or (ts >= previous[0]):
            snapped[key] = (ts, value)
        opening = openings.get(ident)
        if (opening is None) or (ts < opening[0]):
            openings[ident] = (ts, value)

    by_id: dict[tuple, dict[datetime.datetime, tuple[float, float]]] = {}
    for (ident, period), entry in snapped.items():
        by_id.setdefault(ident, {})[period] = entry

    final_period = max((p for _, p in snapped), default=None)

    # Each entry is (id, period, stamp, value, order), where order 0 marks the
    # opening value the first period's change is measured against.
    walked = []
    for ident, periods in by_id.items():
        first_period = min(periods)
        opening_ts, opening_value = openings[ident]
        walked.append((ident, first_period,
                       datetime.datetime.fromtimestamp(opening_ts), opening_value, 0))

        period = first_period
        last_period = final_period if snap_forward else max(periods)
        carried = None
        while period <= last_period:
            entry = periods.get(period)
            if entry is not None:
                carried = entry
                # A period with data is stamped at that data's own time; a gap
                # has none, so it falls back to the period boundary.
                stamp = datetime.datetime.fromtimestamp(entry[0])
            else:
                stamp = period_end(period, mode)
            walked.append((ident, period, stamp, carried[1], 1))
            period = next_period(period, mode)

    if collapse:
        totals: dict[datetime.datetime, float] = {}
        stamps: dict[datetime.datetime, datetime.datetime] = {}
        for _, period, stamp, value, order in walked:
            if order == 0:
                continue
            totals[period] = totals.get(period, 0.0) + value
            stamps[period] = max(stamp, stamps[period]) if period in stamps else stamp

        combined = []
        if totals:
            # Only the ids that are already present in the first period
            # contribute to the combined opening value.
            first_period = min(totals)
            opening = [(stamp, value) for _, period, stamp, value, order in walked
                       if (order == 0) and (period == first_period)]
            combined.append((None, first_period, max(s for s, _ in opening),
                             sum(v for _, v in opening), 0))
        combined += [(None, period, stamps[period], totals[period], 1)
                     for period in sorted(totals)]
        walked = combined

    rows_out = []
    firsts: dict[tuple | None, float] = {}
    previous_by_id: dict[tuple | None, float] = {}
    for ident, period, stamp, value, order in walked:
        first = firsts.setdefault(ident, value)
        row = {'period_end': stamp.strftime(end_fmt)}
        if ident is not None:
            row.update(zip(id_fields, ident))
        row[value_field] = value
        row['change'] = format_percent(previous_by_id.get(ident), value)
        row['cumulative'] = format_percent(first, value)
        rows_out.append(((row['period_end'], order, ident or ()), row))
        previous_by_id[ident] = value

    rows_out.sort(key=lambda r: r[0])
    return [row for _, row in rows_out]


def print_table(results: list[dict], field_list: list[str], no_header: bool) -> None:
    """Print results as an aligned text table."""
    if not results:
        print("No matching records found.")
        return

    col_widths = {f: len(f) for f in field_list}
    for r in results:
        for f in field_list:
            col_widths[f] = max(col_widths[f], len(str(r.get(f, ''))))

    def print_row(values):
        print(' | '.join(str(v).ljust(col_widths[f]) for f, v in zip(field_list, values)))

    if not no_header:
        print_row(field_list)
        print('-+-'.join('-' * col_widths[f] for f in field_list))

    for r in results:
        print_row([r.get(f, '') for f in field_list])


def main():
    parser = argparse.ArgumentParser(
        description='Compute percentage changes over time from a CSV time series',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  view-json prices.jsonl -f ts symbol close -t ts -o csv | %(prog)s --time ts --value close
  %(prog)s prices.csv --time ts --value close --mode monthly
  %(prog)s prices.csv --time ts --value close --id exchange symbol
  %(prog)s 2024.csv 2025.csv --time ts --value close --collapse
        """
    )
    parser.add_argument('files', nargs='*', help='Input CSV file(s) with a header row '
                                                 '(default: stdin)')
    parser.add_argument('-t', '--time', required=True,
                        help='Column holding the timestamp (Unix seconds or a date string)')
    parser.add_argument('-v', '--value', required=True,
                        help='Numeric column whose percentage change is computed')
    parser.add_argument('-i', '--id', nargs='+',
                        help='Columns to group by (default: every column other than --time '
                             'and --value)')
    parser.add_argument('--mode', choices=list(PERIOD_FORMATS), default='daily',
                        help='Period to snap values to (default: daily)')
    parser.add_argument('--snap-forward', action='store_true',
                        help='Carry each id forward to the last period in the data instead of '
                             'ending at its own last record')
    parser.add_argument('--collapse', action='store_true',
                        help='Sum the ids together per period and compute the percentages on the '
                             'summed series (forces --snap-forward)')
    parser.add_argument('--csv', action='store_true',
                        help='Write csv instead of an aligned table')
    parser.add_argument('--no-header', action='store_true',
                        help='Omit the header row in the output')

    args = parser.parse_args()

    try:
        rows, header = read_rows(args.files)
    except FileNotFoundError as e:
        print(f"Error: File not found: {e.filename}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError) as e:
        print(f"Error reading input: {e}", file=sys.stderr)
        sys.exit(1)

    id_fields = args.id if args.id is not None \
        else [f for f in header if f not in (args.time, args.value)]

    missing = [f for f in [args.time, args.value] + id_fields if f not in header]
    if missing:
        print(f"Error: column(s) not in CSV header: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    overlap = [f for f in id_fields if f in (args.time, args.value)]
    if overlap:
        parser.error(f"--id cannot name the --time or --value column: {', '.join(overlap)}")

    try:
        results = compute_percent_changes(rows, args.value, id_fields, args.time,
                                          args.mode, args.snap_forward or args.collapse,
                                          args.collapse)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    field_list = list(results[0].keys()) if results else []
    if args.csv:
        writer = csv.writer(sys.stdout)
        if not args.no_header and field_list:
            writer.writerow(field_list)
        for r in results:
            writer.writerow([r.get(f, '') for f in field_list])
    else:
        print_table(results, field_list, args.no_header)


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:
        # Handle pipe closure gracefully (e.g., when piping to head)
        sys.stderr.close()
    except KeyboardInterrupt:
        sys.exit(1)
