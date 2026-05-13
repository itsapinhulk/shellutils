"""Tests for py/view-json.py and bash/view-json."""

import json
import subprocess
from pathlib import Path

import pytest

view_json = __import__("view-json")

parse_filter = view_json.parse_filter
get_nested_value = view_json.get_nested_value
convert_timestamp = view_json.convert_timestamp
matches_filter = view_json.matches_filter
load_records = view_json.load_records
extract_fields = view_json.extract_fields
main = view_json.main

BASH_SCRIPT = Path(__file__).parent.parent / "bash" / "view-json"


# --- parse_filter ---

class TestParseFilter:
    def test_equals(self):
        assert parse_filter("status=active") == ("status", "=", "active")

    def test_not_equals(self):
        assert parse_filter("status!=inactive") == ("status", "!=", "inactive")

    def test_contains(self):
        assert parse_filter("name~=john") == ("name", "~=", "john")

    def test_greater_than(self):
        assert parse_filter("age>18") == ("age", ">", "18")

    def test_less_than(self):
        assert parse_filter("age<65") == ("age", "<", "65")

    def test_gte(self):
        assert parse_filter("score>=90") == ("score", ">=", "90")

    def test_lte(self):
        assert parse_filter("score<=100") == ("score", "<=", "100")

    def test_value_with_spaces(self):
        field, op, val = parse_filter("name = Alice")
        assert field == "name"
        assert op == "="
        assert val == "Alice"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_filter("nooperator")

    def test_prefers_longer_op(self):
        # >= should match before >
        field, op, val = parse_filter("age>=18")
        assert op == ">="


# --- get_nested_value ---

class TestGetNestedValue:
    def test_top_level(self):
        assert get_nested_value({"a": 1}, "a") == 1

    def test_nested(self):
        assert get_nested_value({"user": {"name": "Alice"}}, "user.name") == "Alice"

    def test_deeply_nested(self):
        obj = {"a": {"b": {"c": 42}}}
        assert get_nested_value(obj, "a.b.c") == 42

    def test_missing_key(self):
        assert get_nested_value({"a": 1}, "b") is None

    def test_missing_nested_key(self):
        assert get_nested_value({"user": {"name": "Alice"}}, "user.age") is None

    def test_non_dict_intermediate(self):
        assert get_nested_value({"a": "string"}, "a.b") is None


# --- convert_timestamp ---

class TestConvertTimestamp:
    def test_known_timestamp(self):
        # 2024-01-01 00:00:00 UTC = 1704067200; result depends on local tz,
        # so just check it returns a formatted string, not the raw value.
        result = convert_timestamp(1704067200.0)
        assert "-" in result and ":" in result

    def test_custom_format(self):
        result = convert_timestamp(1704067200.0, "%Y-%m-%d")
        assert len(result) == 10  # YYYY-MM-DD

    def test_invalid_value(self):
        result = convert_timestamp("not-a-number")
        assert result == "not-a-number"

    def test_none_value(self):
        result = convert_timestamp(None)
        assert result == "None"


# --- matches_filter ---

class TestMatchesFilter:
    def test_equals_string(self):
        assert matches_filter({"status": "active"}, "status", "=", "active")
        assert not matches_filter({"status": "active"}, "status", "=", "inactive")

    def test_not_equals(self):
        assert matches_filter({"status": "active"}, "status", "!=", "inactive")
        assert not matches_filter({"status": "active"}, "status", "!=", "active")

    def test_contains(self):
        assert matches_filter({"name": "Johnny"}, "name", "~=", "John")
        assert not matches_filter({"name": "Alice"}, "name", "~=", "John")

    def test_numeric_comparison(self):
        assert matches_filter({"age": 25}, "age", ">", "18")
        assert not matches_filter({"age": 10}, "age", ">", "18")
        assert matches_filter({"age": 18}, "age", ">=", "18")
        assert matches_filter({"age": 10}, "age", "<", "18")
        assert matches_filter({"age": 18}, "age", "<=", "18")

    def test_missing_field(self):
        assert not matches_filter({"name": "Alice"}, "age", ">", "18")

    def test_nested_field(self):
        assert matches_filter({"user": {"role": "admin"}}, "user.role", "=", "admin")

    def test_equals_wildcard_star(self):
        assert matches_filter({"name": "alpha-beta"}, "name", "=", "a*b*")
        assert matches_filter({"name": "ab"}, "name", "=", "a*b")
        assert not matches_filter({"name": "alpha"}, "name", "=", "a*b")

    def test_equals_wildcard_question(self):
        assert matches_filter({"code": "a1b"}, "code", "=", "a?b")
        assert not matches_filter({"code": "a12b"}, "code", "=", "a?b")

    def test_equals_wildcard_charclass(self):
        assert matches_filter({"v": "a1"}, "v", "=", "a[0-9]")
        assert not matches_filter({"v": "aa"}, "v", "=", "a[0-9]")

    def test_not_equals_wildcard(self):
        assert not matches_filter({"name": "alpha-beta"}, "name", "!=", "a*b*")
        assert matches_filter({"name": "xyz"}, "name", "!=", "a*b*")

    def test_equals_no_wildcard_is_exact(self):
        # No glob metachars => exact match, not substring
        assert not matches_filter({"name": "Johnny"}, "name", "=", "John")


# --- load_records ---

class TestLoadRecords:
    def test_json_array(self, tmp_path):
        data = [{"id": 1}, {"id": 2}]
        f = tmp_path / "data.json"
        f.write_text(json.dumps(data))
        assert load_records(str(f)) == data

    def test_single_json_object(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps({"id": 1}))
        assert load_records(str(f)) == [{"id": 1}]

    def test_jsonl(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"id": 1}\n{"id": 2}\n')
        assert load_records(str(f)) == [{"id": 1}, {"id": 2}]

    def test_jsonl_skips_blank_lines(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"id": 1}\n\n{"id": 2}\n')
        assert load_records(str(f)) == [{"id": 1}, {"id": 2}]

    def test_jsonl_warns_on_invalid_line(self, tmp_path, capsys):
        f = tmp_path / "data.jsonl"
        f.write_text('{"id": 1}\nnot-json\n{"id": 2}\n')
        result = load_records(str(f))
        assert result == [{"id": 1}, {"id": 2}]
        captured = capsys.readouterr()
        assert "Warning" in captured.err

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_records("/nonexistent/path/data.json")


# --- extract_fields ---

class TestExtractFields:
    def test_extract_specified_fields(self):
        record = {"id": 1, "name": "Alice", "age": 30}
        result = extract_fields(record, ["id", "name"], set(), "%Y-%m-%d %H:%M:%S")
        assert result == {"id": 1, "name": "Alice"}

    def test_extract_all_fields(self):
        record = {"id": 1, "name": "Alice"}
        result = extract_fields(record, None, set(), "%Y-%m-%d %H:%M:%S")
        assert result == {"id": 1, "name": "Alice"}

    def test_missing_field_returns_none(self):
        record = {"id": 1}
        result = extract_fields(record, ["id", "missing"], set(), "%Y-%m-%d %H:%M:%S")
        assert result is None

    def test_timestamp_conversion(self):
        record = {"id": 1, "ts": 1704067200.0}
        result = extract_fields(record, ["id", "ts"], {"ts"}, "%Y-%m-%d")
        assert result["id"] == 1
        assert "-" in result["ts"]  # converted to date string
        assert isinstance(result["ts"], str)

    def test_no_timestamp_conversion_for_non_numeric(self):
        record = {"ts": "2024-01-01"}
        result = extract_fields(record, ["ts"], {"ts"}, "%Y-%m-%d")
        assert result == {"ts": "2024-01-01"}

    def test_nested_field_extraction(self):
        record = {"user": {"name": "Alice"}, "id": 1}
        result = extract_fields(record, ["user.name"], set(), "%Y-%m-%d %H:%M:%S")
        assert result == {"user.name": "Alice"}


# --- multi-file and file label ---

class TestMultiFileAndLabel:
    def test_single_file_no_label(self, tmp_path, capsys):
        f = tmp_path / "a.json"
        f.write_text(json.dumps([{"id": 1}]))
        import sys
        argv_backup = sys.argv
        try:
            sys.argv = ["view-json", str(f)]
            main()
        finally:
            sys.argv = argv_backup
        out = capsys.readouterr().out
        assert "# " not in out

    def test_no_file_label_suppresses_label(self, tmp_path, capsys):
        f1 = tmp_path / "a.json"
        f2 = tmp_path / "b.json"
        f1.write_text(json.dumps([{"id": 1}]))
        f2.write_text(json.dumps([{"id": 2}]))
        import sys
        argv_backup = sys.argv
        try:
            sys.argv = ["view-json", str(f1), str(f2), "--no-file-label"]
            main()
        finally:
            sys.argv = argv_backup
        out = capsys.readouterr().out
        assert "# " not in out

    def test_multiple_files_processed_in_order(self, tmp_path, capsys):
        f1 = tmp_path / "a.json"
        f2 = tmp_path / "b.json"
        f1.write_text(json.dumps([{"id": 1}]))
        f2.write_text(json.dumps([{"id": 2}]))
        import sys
        argv_backup = sys.argv
        try:
            sys.argv = ["view-json", str(f1), str(f2), "--no-file-label"]
            main()
        finally:
            sys.argv = argv_backup
        out = capsys.readouterr().out
        lines = [l for l in out.splitlines() if l.strip()]
        assert json.loads(lines[0]) == {"id": 1}
        assert json.loads(lines[1]) == {"id": 2}

    def test_with_file_label_shows_label_for_single_file(self, tmp_path, capsys):
        f = tmp_path / "a.json"
        f.write_text(json.dumps([{"id": 1}]))
        import sys
        argv_backup = sys.argv
        try:
            sys.argv = ["view-json", str(f), "--with-file-label"]
            main()
        finally:
            sys.argv = argv_backup
        out = capsys.readouterr().out
        assert f"# {f}" in out

    def test_multiple_files_each_get_label(self, tmp_path, capsys):
        f1 = tmp_path / "a.json"
        f2 = tmp_path / "b.json"
        f1.write_text(json.dumps([{"id": 1}]))
        f2.write_text(json.dumps([{"id": 2}]))
        import sys
        argv_backup = sys.argv
        try:
            sys.argv = ["view-json", str(f1), str(f2)]
            main()
        finally:
            sys.argv = argv_backup
        out = capsys.readouterr().out
        assert f"# {f1}" in out
        assert f"# {f2}" in out


# --- bash wrapper ---

class TestBashWrapper:
    def test_help_passthrough(self):
        result = subprocess.run(
            ["bash", str(BASH_SCRIPT), "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Extract fields from JSON" in result.stdout

    def test_args_forwarded_verbatim(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps([{"id": 1, "name": "alpha"}]))
        result = subprocess.run(
            ["bash", str(BASH_SCRIPT), str(f), "--no-file-label", "-f", "id"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout.strip()) == {"id": 1}
