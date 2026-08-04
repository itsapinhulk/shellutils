"""Tests for py/analyze-time-series.py and bash/analyze-time-series."""

import csv
import datetime
import subprocess
import sys
from pathlib import Path

import pytest

analyze = __import__("analyze-time-series")

to_timestamp = analyze.to_timestamp
to_number = analyze.to_number
format_percent = analyze.format_percent
period_start = analyze.period_start
next_period = analyze.next_period
period_end = analyze.period_end
read_rows = analyze.read_rows
compute_percent_changes = analyze.compute_percent_changes

PY_SCRIPT = Path(__file__).parent.parent / "py" / "analyze-time-series.py"
BASH_SCRIPT = Path(__file__).parent.parent / "bash" / "analyze-time-series"

HEADER = "ts,symbol,close\n"


def row(ts, symbol, close, **extra):
    record = {"ts": ts, "symbol": symbol, "close": close}
    record.update(extra)
    return record


def summarize(results, value_field="close"):
    """Reduce result rows to (period_end, value, change, cumulative)."""
    return [(r["period_end"], r[value_field], r["change"], r["cumulative"])
            for r in results]


# --- to_timestamp ---

class TestToTimestamp:
    def test_date_string(self):
        assert to_timestamp("2026-01-05") == datetime.datetime(2026, 1, 5).timestamp()

    def test_datetime_string(self):
        expected = datetime.datetime(2026, 1, 5, 10, 30).timestamp()
        assert to_timestamp("2026-01-05 10:30:00") == expected

    def test_unix_seconds_string(self):
        assert to_timestamp("1704067200") == 1704067200.0

    def test_numeric(self):
        assert to_timestamp(1704067200) == 1704067200.0

    def test_surrounding_whitespace(self):
        assert to_timestamp(" 2026-01-05 ") == datetime.datetime(2026, 1, 5).timestamp()

    def test_invalid(self):
        assert to_timestamp("not-a-date") is None
        assert to_timestamp(None) is None


# --- to_number ---

class TestToNumber:
    def test_integer_string(self):
        assert to_number("42") == 42.0

    def test_float_string(self):
        assert to_number(" 3.5 ") == 3.5

    def test_negative(self):
        assert to_number("-1.25") == -1.25

    def test_invalid(self):
        assert to_number("abc") is None
        assert to_number("") is None


# --- format_percent ---

class TestFormatPercent:
    def test_no_base_is_blank(self):
        assert format_percent(None, 100.0) == ''

    def test_zero_base_is_not_available(self):
        assert format_percent(0.0, 100.0) == 'n/a'

    def test_increase(self):
        assert format_percent(100.0, 110.0) == '+10.00%'

    def test_decrease(self):
        assert format_percent(100.0, 75.0) == '-25.00%'

    def test_unchanged(self):
        assert format_percent(100.0, 100.0) == '+0.00%'


# --- period arithmetic ---

class TestPeriods:
    def test_hourly_start_truncates_minutes(self):
        ts = datetime.datetime(2026, 3, 4, 14, 37, 12).timestamp()
        assert period_start(ts, 'hourly') == datetime.datetime(2026, 3, 4, 14)

    def test_daily_start_truncates_time(self):
        ts = datetime.datetime(2026, 3, 4, 14, 37, 12).timestamp()
        assert period_start(ts, 'daily') == datetime.datetime(2026, 3, 4)

    def test_monthly_start_truncates_day(self):
        ts = datetime.datetime(2026, 3, 4, 14, 37, 12).timestamp()
        assert period_start(ts, 'monthly') == datetime.datetime(2026, 3, 1)

    def test_next_hourly(self):
        assert next_period(datetime.datetime(2026, 3, 4, 23), 'hourly') == \
            datetime.datetime(2026, 3, 5)

    def test_next_daily(self):
        assert next_period(datetime.datetime(2026, 2, 28), 'daily') == \
            datetime.datetime(2026, 3, 1)

    def test_next_monthly_short_month(self):
        assert next_period(datetime.datetime(2026, 2, 1), 'monthly') == \
            datetime.datetime(2026, 3, 1)

    def test_next_monthly_long_month(self):
        assert next_period(datetime.datetime(2026, 1, 1), 'monthly') == \
            datetime.datetime(2026, 2, 1)

    def test_next_monthly_year_rollover(self):
        assert next_period(datetime.datetime(2026, 12, 1), 'monthly') == \
            datetime.datetime(2027, 1, 1)

    def test_end_is_last_second_of_period(self):
        assert period_end(datetime.datetime(2026, 2, 1), 'monthly') == \
            datetime.datetime(2026, 2, 28, 23, 59, 59)
        assert period_end(datetime.datetime(2026, 3, 4), 'daily') == \
            datetime.datetime(2026, 3, 4, 23, 59, 59)


# --- read_rows ---

class TestReadRows:
    def test_single_file(self, tmp_path):
        f = tmp_path / "a.csv"
        f.write_text(HEADER + "2026-01-01,AAA,100\n")
        rows, header = read_rows([str(f)])
        assert header == ["ts", "symbol", "close"]
        assert rows == [{"ts": "2026-01-01", "symbol": "AAA", "close": "100"}]

    def test_files_are_merged(self, tmp_path):
        f1 = tmp_path / "a.csv"
        f2 = tmp_path / "b.csv"
        f1.write_text(HEADER + "2026-01-01,AAA,100\n")
        f2.write_text(HEADER + "2026-01-02,AAA,110\n")
        rows, header = read_rows([str(f1), str(f2)])
        assert header == ["ts", "symbol", "close"]
        assert [r["close"] for r in rows] == ["100", "110"]

    def test_mismatched_header_raises(self, tmp_path):
        f1 = tmp_path / "a.csv"
        f2 = tmp_path / "b.csv"
        f1.write_text(HEADER + "2026-01-01,AAA,100\n")
        f2.write_text("ts,name,close\n2026-01-02,AAA,110\n")
        with pytest.raises(ValueError):
            read_rows([str(f1), str(f2)])

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            read_rows(["/nonexistent/path/data.csv"])


# --- compute_percent_changes ---

class TestComputePercentChanges:
    def test_last_value_in_period_wins(self):
        rows = [
            row("2026-01-01 09:00:00", "AAA", "100"),
            row("2026-01-01 16:00:00", "AAA", "110"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily")
        assert summarize(results) == [
            ("2026-01-01 09", 100.0, '', '+0.00%'),
            ("2026-01-01 16", 110.0, '+10.00%', '+10.00%'),
        ]

    def test_out_of_order_rows_snap_to_latest(self):
        rows = [
            row("2026-01-01 16:00:00", "AAA", "110"),
            row("2026-01-01 09:00:00", "AAA", "100"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily")
        assert summarize(results) == [
            ("2026-01-01 09", 100.0, '', '+0.00%'),
            ("2026-01-01 16", 110.0, '+10.00%', '+10.00%'),
        ]

    def test_gap_period_carries_value_and_shows_boundary(self):
        rows = [
            row("2026-01-01 09:00:00", "AAA", "100"),
            row("2026-01-03 12:00:00", "AAA", "121"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily")
        assert summarize(results) == [
            ("2026-01-01 09", 100.0, '', '+0.00%'),
            ("2026-01-01 09", 100.0, '+0.00%', '+0.00%'),
            ("2026-01-02 23", 100.0, '+0.00%', '+0.00%'),
            ("2026-01-03 12", 121.0, '+21.00%', '+21.00%'),
        ]

    def test_opening_row_is_the_baseline(self):
        rows = [
            row("2026-01-01 09:00:00", "AAA", "50"),
            row("2026-01-01 16:00:00", "AAA", "100"),
            row("2026-01-02 16:00:00", "AAA", "150"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily")
        # Cumulative is measured from the opening 50, not from the first close.
        assert summarize(results) == [
            ("2026-01-01 09", 50.0, '', '+0.00%'),
            ("2026-01-01 16", 100.0, '+100.00%', '+100.00%'),
            ("2026-01-02 16", 150.0, '+50.00%', '+200.00%'),
        ]

    def test_monthly_mode_reports_day_of_last_record(self):
        rows = [
            row("2026-01-05 09:00:00", "AAA", "100"),
            row("2026-02-04 09:00:00", "AAA", "110"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "monthly")
        assert [r["period_end"] for r in results] == \
            ["2026-01-05", "2026-01-05", "2026-02-04"]

    def test_hourly_mode_reports_minute_of_last_record(self):
        rows = [
            row("2026-01-05 09:15:00", "AAA", "100"),
            row("2026-01-05 10:20:00", "AAA", "110"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "hourly")
        assert [r["period_end"] for r in results] == \
            ["2026-01-05 09:15", "2026-01-05 09:15", "2026-01-05 10:20"]

    def test_ids_are_independent_series(self):
        rows = [
            row("2026-01-01 09:00:00", "AAA", "100"),
            row("2026-01-02 09:00:00", "AAA", "110"),
            row("2026-01-01 09:00:00", "BBB", "200"),
            row("2026-01-02 09:00:00", "BBB", "100"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily")
        last = [r for r in results if r["period_end"] == "2026-01-02 09"]
        assert {r["symbol"]: r["change"] for r in last} == \
            {"AAA": '+10.00%', "BBB": '-50.00%'}

    def test_id_columns_are_preserved(self):
        rows = [
            row("2026-01-01 09:00:00", "AAA", "100", exchange="X"),
            row("2026-01-02 09:00:00", "AAA", "110", exchange="X"),
        ]
        results = compute_percent_changes(rows, "close", ["exchange", "symbol"],
                                          "ts", "daily")
        assert all((r["exchange"] == "X") and (r["symbol"] == "AAA") for r in results)

    def test_series_ends_at_its_own_last_record(self):
        rows = [
            row("2026-01-01 09:00:00", "AAA", "100"),
            row("2026-01-01 09:00:00", "BBB", "200"),
            row("2026-01-03 09:00:00", "BBB", "220"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily")
        assert [r["period_end"] for r in results if r["symbol"] == "AAA"] == \
            ["2026-01-01 09", "2026-01-01 09"]

    def test_snap_forward_extends_to_last_period_in_data(self):
        rows = [
            row("2026-01-01 09:00:00", "AAA", "100"),
            row("2026-01-01 09:00:00", "BBB", "200"),
            row("2026-01-03 09:00:00", "BBB", "220"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily",
                                          snap_forward=True)
        projected = [(r["period_end"], r["close"], r["change"])
                     for r in results if r["symbol"] == "AAA"]
        assert projected == [
            ("2026-01-01 09", 100.0, ''),
            ("2026-01-01 09", 100.0, '+0.00%'),
            ("2026-01-02 23", 100.0, '+0.00%'),
            ("2026-01-03 23", 100.0, '+0.00%'),
        ]

    def test_collapse_sums_ids_and_drops_id_columns(self):
        rows = [
            row("2026-01-01 09:00:00", "AAA", "100"),
            row("2026-01-02 09:00:00", "AAA", "100"),
            row("2026-01-02 09:00:00", "BBB", "50"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily",
                                          collapse=True)
        assert summarize(results) == [
            ("2026-01-01 09", 100.0, '', '+0.00%'),
            ("2026-01-01 09", 100.0, '+0.00%', '+0.00%'),
            ("2026-01-02 09", 150.0, '+50.00%', '+50.00%'),
        ]
        assert all("symbol" not in r for r in results)

    def test_collapse_opening_excludes_later_ids(self):
        rows = [
            row("2026-01-01 09:00:00", "AAA", "100"),
            row("2026-01-02 09:00:00", "BBB", "50"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily",
                                          collapse=True)
        # BBB only exists from the second period, so the opening total is AAA's.
        assert results[0]["close"] == 100.0

    def test_no_id_columns_is_one_series(self):
        rows = [
            {"ts": "2026-01-01 09:00:00", "close": "100"},
            {"ts": "2026-01-02 09:00:00", "close": "110"},
        ]
        results = compute_percent_changes(rows, "close", [], "ts", "daily")
        assert summarize(results) == [
            ("2026-01-01 09", 100.0, '', '+0.00%'),
            ("2026-01-01 09", 100.0, '+0.00%', '+0.00%'),
            ("2026-01-02 09", 110.0, '+10.00%', '+10.00%'),
        ]

    def test_rows_with_blank_id_are_skipped(self):
        rows = [
            row("2026-01-01 09:00:00", "", "100"),
            row("2026-01-01 09:00:00", "AAA", "200"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily")
        assert all(r["symbol"] == "AAA" for r in results)

    def test_rows_with_unparseable_time_are_skipped(self):
        rows = [
            row("not-a-date", "AAA", "100"),
            row("2026-01-01 09:00:00", "AAA", "200"),
        ]
        results = compute_percent_changes(rows, "close", ["symbol"], "ts", "daily")
        assert [r["close"] for r in results] == [200.0, 200.0]

    def test_non_numeric_value_raises(self):
        rows = [row("2026-01-01 09:00:00", "AAA", "abc")]
        with pytest.raises(ValueError):
            compute_percent_changes(rows, "close", ["symbol"], "ts", "daily")

    def test_empty_input(self):
        assert compute_percent_changes([], "close", ["symbol"], "ts", "daily") == []


# --- command line ---

class TestCommandLine:
    @staticmethod
    def run(args, stdin=None):
        return subprocess.run([sys.executable, str(PY_SCRIPT)] + args,
                              input=stdin, capture_output=True, text=True)

    def test_reads_stdin_by_default(self):
        stdin = HEADER + "2026-01-01 09:00:00,AAA,100\n2026-01-02 09:00:00,AAA,110\n"
        result = self.run(["--time", "ts", "--value", "close"], stdin=stdin)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert [h.strip() for h in lines[0].split(" | ")] == \
            ["period_end", "symbol", "close", "change", "cumulative"]
        assert "+10.00%" in lines[-1]

    def test_remaining_columns_are_the_id(self):
        stdin = ("ts,exchange,symbol,close\n"
                 "2026-01-01 09:00:00,X,AAA,100\n"
                 "2026-01-02 09:00:00,X,AAA,110\n")
        result = self.run(["--time", "ts", "--value", "close"], stdin=stdin)
        assert result.returncode == 0, result.stderr
        assert [h.strip() for h in result.stdout.splitlines()[0].split(" | ")][:3] == \
            ["period_end", "exchange", "symbol"]

    def test_explicit_id_narrows_grouping(self):
        stdin = ("ts,exchange,symbol,close\n"
                 "2026-01-01 09:00:00,X,AAA,100\n"
                 "2026-01-02 09:00:00,Y,AAA,110\n")
        result = self.run(["--time", "ts", "--value", "close", "--id", "symbol"],
                          stdin=stdin)
        assert result.returncode == 0, result.stderr
        header = result.stdout.splitlines()[0].split(" | ")
        assert "exchange" not in [h.strip() for h in header]
        assert "+10.00%" in result.stdout

    def test_file_argument(self, tmp_path):
        f = tmp_path / "a.csv"
        f.write_text(HEADER + "2026-01-01 09:00:00,AAA,100\n")
        result = self.run([str(f), "--time", "ts", "--value", "close"])
        assert result.returncode == 0, result.stderr
        assert "AAA" in result.stdout

    def test_no_header_omits_header_row(self):
        stdin = HEADER + "2026-01-01 09:00:00,AAA,100\n"
        result = self.run(["--time", "ts", "--value", "close", "--no-header"],
                          stdin=stdin)
        assert result.returncode == 0, result.stderr
        assert "period_end" not in result.stdout

    def test_csv_output(self):
        stdin = HEADER + "2026-01-01 09:00:00,AAA,100\n2026-01-02 09:00:00,AAA,110\n"
        result = self.run(["--time", "ts", "--value", "close", "--csv"], stdin=stdin)
        assert result.returncode == 0, result.stderr
        rows = list(csv.reader(result.stdout.splitlines()))
        assert rows == [
            ["period_end", "symbol", "close", "change", "cumulative"],
            ["2026-01-01 09", "AAA", "100.0", "", "+0.00%"],
            ["2026-01-01 09", "AAA", "100.0", "+0.00%", "+0.00%"],
            ["2026-01-02 09", "AAA", "110.0", "+10.00%", "+10.00%"],
        ]

    def test_csv_output_without_header(self):
        stdin = HEADER + "2026-01-01 09:00:00,AAA,100\n"
        result = self.run(["--time", "ts", "--value", "close", "--csv", "--no-header"],
                          stdin=stdin)
        assert result.returncode == 0, result.stderr
        rows = list(csv.reader(result.stdout.splitlines()))
        assert rows == [
            ["2026-01-01 09", "AAA", "100.0", "", "+0.00%"],
            ["2026-01-01 09", "AAA", "100.0", "+0.00%", "+0.00%"],
        ]

    def test_unknown_column_fails(self):
        stdin = HEADER + "2026-01-01 09:00:00,AAA,100\n"
        result = self.run(["--time", "ts", "--value", "missing"], stdin=stdin)
        assert result.returncode == 1
        assert "missing" in result.stderr

    def test_id_may_not_name_time_or_value(self):
        stdin = HEADER + "2026-01-01 09:00:00,AAA,100\n"
        result = self.run(["--time", "ts", "--value", "close", "--id", "ts"],
                          stdin=stdin)
        assert result.returncode == 2
        assert "--id" in result.stderr

    def test_non_numeric_value_fails(self):
        stdin = HEADER + "2026-01-01 09:00:00,AAA,abc\n"
        result = self.run(["--time", "ts", "--value", "close"], stdin=stdin)
        assert result.returncode == 1
        assert "not numeric" in result.stderr

    def test_time_is_required(self):
        result = self.run(["--value", "close"], stdin=HEADER)
        assert result.returncode == 2

    def test_collapse_drops_id_column(self):
        stdin = (HEADER + "2026-01-01 09:00:00,AAA,100\n"
                          "2026-01-01 09:00:00,BBB,50\n")
        result = self.run(["--time", "ts", "--value", "close", "--collapse"],
                          stdin=stdin)
        assert result.returncode == 0, result.stderr
        assert "symbol" not in result.stdout
        assert "150.0" in result.stdout


# --- bash wrapper ---

class TestBashWrapper:
    def test_help_passthrough(self):
        result = subprocess.run(["bash", str(BASH_SCRIPT), "--help"],
                                capture_output=True, text=True)
        assert result.returncode == 0
        assert "percentage changes" in result.stdout

    def test_args_forwarded_verbatim(self, tmp_path):
        f = tmp_path / "a.csv"
        f.write_text(HEADER + "2026-01-01 09:00:00,AAA,100\n")
        result = subprocess.run(
            ["bash", str(BASH_SCRIPT), str(f), "--time", "ts", "--value", "close"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "AAA" in result.stdout
