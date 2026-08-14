"""Report writers: HTML, JSON, JUnit XML."""
from __future__ import annotations

from .html import write_html_report
from .junit import write_junit_xml, write_json_report

__all__ = ["write_html_report", "write_junit_xml", "write_json_report"]
