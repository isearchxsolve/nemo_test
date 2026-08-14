"""HTML report writer.

Produces a self-contained single-file HTML report with per-test timeline bar,
inline screenshots, video links, assertion table, and LLM reasoning log.
"""
from __future__ import annotations

import base64
import html
import json
import time
from pathlib import Path

from ..engine.playwright_runner import SuiteResult, TestResult

__all__ = ["write_html_report"]

_CSS = """
:root { font-family: system-ui, sans-serif; --pass: #2da44e; --fail: #cf222e; }
body { margin: 0; background: #f6f8fa; color: #1f2328; }
header { background: #0969da; color: #fff; padding: 1rem 2rem; }
header h1 { margin: 0; font-size: 1.4rem; }
.summary { display: flex; gap: 2rem; padding: 1rem 2rem; background: #fff;
           border-bottom: 1px solid #d0d7de; flex-wrap: wrap; align-items: center; }
.stat { font-size: 1.2rem; font-weight: 700; }
.stat.pass { color: var(--pass); }
.stat.fail { color: var(--fail); }
.timeline-section { padding: 1rem 2rem; background: #fff;
                    border-bottom: 1px solid #d0d7de; }
.timeline-section h2 { margin: 0 0 0.5rem; font-size: 1rem; }
.tl { display: flex; flex-direction: column; gap: 4px; }
.tl-row { display: flex; align-items: center; gap: 8px; font-size: 0.82rem; }
.tl-label { width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
            flex-shrink: 0; }
.tl-bar-wrap { flex: 1; background: #eee; border-radius: 3px; height: 14px; }
.tl-bar { height: 14px; border-radius: 3px; min-width: 2px; }
.tl-bar.pass { background: var(--pass); }
.tl-bar.fail { background: var(--fail); }
.tl-dur { width: 48px; text-align: right; color: #57606a; flex-shrink: 0; }
.tests { padding: 1rem 2rem; }
details { background: #fff; border: 1px solid #d0d7de; border-radius: 6px;
          margin-bottom: 0.75rem; overflow: hidden; }
summary { padding: 0.75rem 1rem; cursor: pointer; display: flex;
          align-items: center; gap: 0.75rem; font-weight: 600; list-style: none; }
summary::-webkit-details-marker { display: none; }
.badge { padding: 0.15rem 0.6rem; border-radius: 999px; font-size: 0.8rem;
         color: #fff; }
.badge.pass { background: var(--pass); }
.badge.fail { background: var(--fail); }
.kind-badge { background: #0969da; color: #fff; padding: 0.1rem 0.5rem;
              border-radius: 4px; font-size: 0.75rem; }
.test-body { padding: 1rem; border-top: 1px solid #d0d7de; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { padding: 0.4rem 0.75rem; text-align: left;
         border-bottom: 1px solid #d0d7de; }
th { background: #f6f8fa; }
.err { color: var(--fail); font-family: monospace; }
.ss { max-width: 100%; max-height: 400px; object-fit: contain;
      border: 1px solid #d0d7de; border-radius: 4px; margin-top: 0.5rem; }
.video-link { display: inline-block; margin-top: 0.5rem; padding: 0.3rem 0.8rem;
              background: #0969da; color: #fff; border-radius: 4px;
              text-decoration: none; font-size: 0.85rem; }
footer { text-align: center; padding: 2rem; color: #57606a; font-size: 0.85rem; }
"""


def _badge(passed: bool) -> str:
    cls = "pass" if passed else "fail"
    txt = "PASS" if passed else "FAIL"
    return f'<span class="badge {cls}">{txt}</span>'


def _screenshot_img(path: str) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    data = base64.b64encode(p.read_bytes()).decode()
    return f'<img class="ss" src="data:image/png;base64,{data}" alt="screenshot"/>'


def _video_link(path: str) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    uri = p.resolve().as_uri()
    return f'<a class="video-link" href="{html.escape(uri)}" target="_blank">▶ Watch video</a>'


def _timeline(tests: list[TestResult]) -> str:
    """Render a proportional timeline bar chart for all tests."""
    if not tests:
        return ""
    max_dur = max((t.duration_s for t in tests), default=1.0) or 1.0
    rows = []
    for t in tests:
        pct = min(100.0, 100.0 * t.duration_s / max_dur)
        cls = "pass" if t.passed else "fail"
        label = html.escape(t.name[:36] + ("…" if len(t.name) > 36 else ""))
        rows.append(
            f'<div class="tl-row">'
            f'<span class="tl-label" title="{html.escape(t.name)}">{label}</span>'
            f'<div class="tl-bar-wrap">'
            f'<div class="tl-bar {cls}" style="width:{pct:.1f}%"></div>'
            f'</div>'
            f'<span class="tl-dur">{t.duration_s:.2f}s</span>'
            f'</div>'
        )
    return (
        '<div class="timeline-section">'
        '<h2>Test Timeline</h2>'
        f'<div class="tl">{"".join(rows)}</div>'
        '</div>'
    )


def _render_test(t: TestResult) -> str:
    rows = []
    for ar in t.assertion_results:
        stat = _badge(ar.passed)
        rows.append(
            f"<tr><td>{html.escape(ar.assertion.type)}</td>"
            f"<td>{html.escape(str(ar.assertion.value or ''))}</td>"
            f"<td>{stat}</td>"
            f"<td class='err'>{html.escape(ar.message)}</td></tr>"
        )
    assertion_table = ""
    if rows:
        assertion_table = (
            "<table><tr><th>Type</th><th>Value</th><th>Status</th><th>Message</th></tr>"
            + "".join(rows)
            + "</table>"
        )

    step_rows = []
    for s in t.steps:
        stat = _badge(s.passed)
        step_rows.append(
            f"<tr><td>{html.escape(s.action)}</td><td>{stat}</td>"
            f"<td class='err'>{html.escape(s.message)}</td></tr>"
        )
    step_table = ""
    if step_rows:
        step_table = (
            "<table><tr><th>Action</th><th>Status</th><th>Message</th></tr>"
            + "".join(step_rows)
            + "</table>"
        )

    error_div = f"<p class='err'>{html.escape(t.error)}</p>" if t.error else ""

    llm_div = ""
    if t.llm_log:
        llm_div = (
            "<details><summary>LLM reasoning log</summary>"
            f"<pre style='font-size:0.8rem;overflow:auto'>"
            f"{html.escape(json.dumps(t.llm_log, indent=2))}</pre></details>"
        )

    media = _screenshot_img(t.screenshot) + _video_link(t.video)

    return (
        f"<details><summary>"
        f"{_badge(t.passed)}"
        f" <span class='kind-badge'>{html.escape(t.kind)}</span>"
        f" {html.escape(t.name)}"
        f" <span style='margin-left:auto;font-size:0.85rem;font-weight:400'>"
        f"{t.duration_s:.2f}s</span>"
        f"</summary>"
        f"<div class='test-body'>"
        f"{error_div}"
        f"{assertion_table}"
        f"{step_table}"
        f"{media}"
        f"{llm_div}"
        f"</div></details>"
    )


def write_html_report(result: SuiteResult, out_file: Path) -> None:
    """Write a self-contained HTML report with timeline to ``out_file``."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(result.start_time))
    test_html = "\n".join(_render_test(t) for t in result.tests)
    pct = f"{100 * result.passed // result.total}%" if result.total else "—"

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>OpenPath Report — {html.escape(result.run_id)}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <h1>OpenPath Test Report</h1>
  <div style="font-size:0.9rem;margin-top:0.25rem">
    Run: {html.escape(result.run_id)} &nbsp;|&nbsp;
    URL: {html.escape(result.base_url)} &nbsp;|&nbsp;
    {html.escape(ts)}
  </div>
</header>
<div class="summary">
  <div><div class="stat">{result.total}</div>Total</div>
  <div><div class="stat pass">{result.passed}</div>Passed</div>
  <div><div class="stat fail">{result.failed}</div>Failed</div>
  <div><div class="stat">{pct}</div>Pass rate</div>
  <div><div class="stat">{result.duration_s:.1f}s</div>Duration</div>
</div>
{_timeline(result.tests)}
<div class="tests">
{test_html}
</div>
<footer>Generated by <strong>OpenPath</strong> &mdash; AI-native test automation</footer>
</body>
</html>
"""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(page, encoding="utf-8")
