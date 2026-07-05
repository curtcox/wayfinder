#!/usr/bin/env python3
"""Build the GitHub Pages reports index and conformance matrix."""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _parse_junit(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    root = ET.parse(path).getroot()
    rows: list[dict[str, str]] = []
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        if "conformance" not in classname and "conformance" not in name:
            continue
        match = re.search(r"15_(\d+)", name)
        section = f"§15.{match.group(1)}" if match else name
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if failure is not None or error is not None:
            status = "fail"
            detail = (failure.text if failure is not None else error.text if error is not None else "") or ""
        elif skipped is not None:
            status = "skip"
            detail = skipped.get("message", "") or skipped.text or ""
        else:
            status = "pass"
            detail = ""
        source = _source_for_test(name)
        rows.append(
            {
                "section": section,
                "name": name,
                "status": status,
                "detail": detail.strip().splitlines()[0] if detail else "",
                "source": source,
            },
        )
    rows.sort(
        key=lambda row: [int(part) if part.isdigit() else part for part in re.findall(r"\d+|\D+", row["section"])],
    )
    return rows


def _source_for_test(test_name: str) -> str:
    root = _repo_root()
    for path in sorted((root / "tests" / "conformance").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if f"def {test_name}(" in text:
            return f"tests/conformance/{path.name}"
    return "tests/conformance/"


def _capture_examples(root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for script in sorted((root / "examples").glob("*/run.sh")):
        proc = subprocess.run(
            ["bash", str(script), "--scripted"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        combined = (proc.stdout + proc.stderr).strip()
        last_line = combined.splitlines()[-1] if combined else ""
        if proc.returncode != 0 and not last_line:
            last_line = f"exit {proc.returncode}"
        rows.append(
            {
                "name": script.parent.name,
                "status": "pass" if proc.returncode == 0 else "fail",
                "detail": last_line,
            },
        )
    return rows


def _tool_summary(path: Path, *, label: str) -> dict[str, str]:
    text = _read_text(path).strip()
    if not text:
        return {"label": label, "status": "missing", "detail": "report not captured"}
    lowered = text.lower()
    if "error" in lowered or "failed" in lowered or "violations found" in lowered:
        status = "fail"
    else:
        status = "pass"
    first_line = text.splitlines()[0] if text else ""
    return {"label": label, "status": status, "detail": first_line}


def _write_html(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _page(title: str, *, sha: str, built_at: str, inner: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.5; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; }}
    th {{ background: #f5f5f5; }}
    .pass {{ color: #0a7a2f; font-weight: 600; }}
    .fail {{ color: #b00020; font-weight: 600; }}
    .skip {{ color: #8a6d00; font-weight: 600; }}
    code {{ background: #f5f5f5; padding: 0.1rem 0.35rem; border-radius: 0.25rem; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p>Commit <code>{html.escape(sha)}</code> · {html.escape(built_at)}</p>
  {inner}
</body>
</html>
"""


def _table(headers: list[str], rows: list[list[str]], *, status_col: int | None = None) -> str:
    head = "".join(f"<th>{html.escape(item)}</th>" for item in headers)
    body_rows: list[str] = []
    for row in rows:
        cells: list[str] = []
        for index, value in enumerate(row):
            if status_col == index and value in {"pass", "fail", "skip", "missing"}:
                cells.append(f'<td class="{html.escape(value)}">{html.escape(value)}</td>')
            else:
                cells.append(f"<td>{html.escape(value)}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def build_reports(
    *,
    reports_dir: Path,
    site_reports_dir: Path,
    sha: str,
    examples: list[dict[str, str]] | None = None,
) -> None:
    built_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    root = _repo_root()
    if examples is None:
        examples = _capture_examples(root)

    conformance = _parse_junit(reports_dir / "pytest" / "junit.xml")
    tools = [
        _tool_summary(reports_dir / "tools" / "ruff.txt", label="ruff"),
        _tool_summary(reports_dir / "tools" / "mypy.txt", label="mypy"),
        _tool_summary(reports_dir / "tools" / "bandit.txt", label="bandit"),
        _tool_summary(reports_dir / "tools" / "pip-audit.txt", label="pip-audit"),
    ]

    conformance_rows = [
        [row["section"], row["name"], row["status"], row["source"], row["detail"]]
        for row in conformance
    ]
    conformance_html = _page(
        "Conformance Matrix",
        sha=sha,
        built_at=built_at,
        inner=_table(["Vector", "Test", "Status", "Source", "Detail"], conformance_rows, status_col=2),
    )
    _write_html(site_reports_dir / "conformance.html", conformance_html)

    example_rows = [[row["name"], row["status"], row["detail"]] for row in examples]
    examples_html = _page(
        "Scripted Examples",
        sha=sha,
        built_at=built_at,
        inner=_table(["Example", "Status", "Last line"], example_rows, status_col=1),
    )
    _write_html(site_reports_dir / "examples.html", examples_html)

    tool_rows = [[row["label"], row["status"], row["detail"]] for row in tools]
    index_inner = f"""
<h2>Reports</h2>
<ul>
  <li><a href="pytest/report.html">pytest HTML report</a></li>
  <li><a href="coverage/index.html">coverage HTML report</a></li>
  <li><a href="coverage/coverage.json">coverage JSON</a></li>
  <li><a href="conformance.html">conformance matrix</a></li>
  <li><a href="examples.html">scripted examples</a></li>
</ul>
<h2>Tool summaries</h2>
{_table(["Tool", "Status", "Summary"], tool_rows, status_col=1)}
"""
    _write_html(site_reports_dir / "index.html", _page("Wayfinder Reports", sha=sha, built_at=built_at, inner=index_inner))

    summary = {
        "sha": sha,
        "built_at": built_at,
        "conformance_pass": sum(1 for row in conformance if row["status"] == "pass"),
        "conformance_total": len(conformance),
        "examples_pass": sum(1 for row in examples if row["status"] == "pass"),
        "examples_total": len(examples),
    }
    (site_reports_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, default=Path("reports-out"))
    parser.add_argument("--site-reports-dir", type=Path, default=Path("site/reports"))
    parser.add_argument("--sha", default="unknown")
    parser.add_argument(
        "--examples-json",
        type=Path,
        help="Optional precomputed examples results JSON array",
    )
    args = parser.parse_args()

    examples: list[dict[str, str]] | None = None
    if args.examples_json and args.examples_json.is_file():
        loaded = json.loads(args.examples_json.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            examples = [dict(row) for row in loaded]

    build_reports(
        reports_dir=args.reports_dir,
        site_reports_dir=args.site_reports_dir,
        sha=args.sha,
        examples=examples,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
