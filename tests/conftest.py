"""Pytest configuration: fail the run if any test is skipped.

Skipped tests in CI usually mean a missing dependency (e.g. docker) and
should be treated as a failure rather than silently passing.
"""

from __future__ import annotations


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    skipped = reporter.stats.get("skipped", [])
    if skipped:
        names = [getattr(r, "nodeid", str(r)) for r in skipped]
        reporter.write_sep("=", "Skipped tests are not allowed", red=True)
        for name in names:
            reporter.write_line(f"  SKIPPED  {name}")
        session.exitstatus = 1
