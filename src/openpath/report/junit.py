"""JUnit XML and JSON report writers."""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from ..engine.playwright_runner import SuiteResult, TestResult

__all__ = ["write_junit_xml", "write_json_report"]


def write_junit_xml(result: SuiteResult, out_file: Path) -> None:
    """Write a JUnit-compatible XML file to ``out_file``."""
    suite = ET.Element(
        "testsuite",
        name="openpath",
        tests=str(result.total),
        failures=str(result.failed),
        errors="0",
        time=f"{result.duration_s:.3f}",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(result.start_time)),
    )
    for t in result.tests:
        tc = ET.SubElement(
            suite,
            "testcase",
            name=t.name,
            classname=f"openpath.{t.kind}",
            time=f"{t.duration_s:.3f}",
        )
        if not t.passed:
            fail = ET.SubElement(tc, "failure", message=t.error or "test failed")
            details = []
            for ar in t.assertion_results:
                if not ar.passed:
                    details.append(ar.message)
            fail.text = "\n".join(details) or t.error

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(out_file), encoding="unicode", xml_declaration=True)


def write_json_report(result: SuiteResult, out_file: Path) -> None:
    """Write a structured JSON report to ``out_file``."""
    data = {
        "run_id": result.run_id,
        "base_url": result.base_url,
        "start_time": result.start_time,
        "end_time": result.end_time,
        "duration_s": round(result.duration_s, 3),
        "total": result.total,
        "passed": result.passed,
        "failed": result.failed,
        "tests": [_test_to_dict(t) for t in result.tests],
    }
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _test_to_dict(t: TestResult) -> dict:
    return {
        "name": t.name,
        "kind": t.kind,
        "passed": t.passed,
        "duration_s": t.duration_s,
        "error": t.error,
        "screenshot": t.screenshot,
        "video": t.video,
        "assertions": [
            {
                "type": ar.assertion.type,
                "value": ar.assertion.value,
                "passed": ar.passed,
                "message": ar.message,
            }
            for ar in t.assertion_results
        ],
        "har": t.har,
        "steps": [
            {"action": s.action, "passed": s.passed, "message": s.message}
            for s in t.steps
        ],
    }
