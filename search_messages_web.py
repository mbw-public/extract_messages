#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = ["flask"]
# ///
"""
search_messages_web.py — minimal web UI for search_messages.py

A thin Flask front end that exposes the most useful search_messages.py
options as a form, runs the script as a subprocess, and renders its
--color=always output in the browser (ANSI -> HTML span conversion).

Intended for LAN/Tailscale access from a phone or iPad — bookmark the URL
or "Add to Home Screen" in Safari for an app-like icon.

Usage:
    uv run --no-project search_messages_web.py
    uv run --no-project search_messages_web.py --host 0.0.0.0 --port 8765

By default this binds to 127.0.0.1 only. Pass --host 0.0.0.0 to make it
reachable from other devices on your network. There is no authentication —
only do this on a trusted LAN, or behind something like Tailscale.
"""

import argparse
import html
import logging
import re
import subprocess
import sys
from pathlib import Path

from flask import Flask, Response, request

SCRIPT_DIR = Path(__file__).resolve().parent
SEARCH_SCRIPT = SCRIPT_DIR / "search_messages.py"

app = Flask(__name__)


# Browsers request these unprompted; answer quietly instead of letting them
# fall through to a logged 404 for every page load.
@app.route("/favicon.ico")
@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def _no_icon():
    return Response(status=204)


# ── ANSI -> HTML ──────────────────────────────────────────────────────────────

ANSI_RE = re.compile(r"\033\[([0-9;]*)m")

# Maps the small fixed set of codes search_messages.py emits (see its Style
# class) to CSS classes. "0" (reset) closes whatever span is currently open.
ANSI_CLASS = {
    "1": "sm-bold",
    "2": "sm-dim",
    "36": "sm-cyan",
    "32": "sm-green",
    "34": "sm-blue",
    "1;33": "sm-match",
}


def ansi_to_html(text: str) -> str:
    """Convert search_messages.py's ANSI output to HTML with CSS-class spans."""
    out = []
    open_spans = 0
    pos = 0
    for m in ANSI_RE.finditer(text):
        out.append(html.escape(text[pos : m.start()]))
        cls = ANSI_CLASS.get(m.group(1))
        if cls is None:  # reset (or anything unrecognized)
            out.append("</span>" * open_spans)
            open_spans = 0
        else:
            out.append(f'<span class="{cls}">')
            open_spans += 1
        pos = m.end()
    out.append(html.escape(text[pos:]))
    out.append("</span>" * open_spans)
    return "".join(out)


# ── Page template ─────────────────────────────────────────────────────────────

PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>search_messages</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    background: #1e1e1e;
    color: #d4d4d4;
    font-family: -apple-system, system-ui, sans-serif;
    margin: 0;
    padding: 0.75rem 1rem 2rem;
  }}
  h1 {{ font-size: 1.05rem; margin: 0 0 0.75rem; color: #fff; font-weight: 600; }}
  form {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    align-items: center;
    margin-bottom: 0.75rem;
  }}
  input[type=text], input[type=number] {{
    background: #2d2d2d;
    color: #d4d4d4;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 0.45rem 0.5rem;
    font-size: 1rem;
  }}
  input.pattern {{ flex: 1 1 220px; }}
  label {{ font-size: 0.85rem; color: #bbb; white-space: nowrap; }}
  fieldset {{
    border: 1px solid #3a3a3a;
    border-radius: 6px;
    padding: 0.4rem 0.7rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.7rem;
    align-items: center;
  }}
  legend {{ font-size: 0.75rem; color: #888; padding: 0 0.3rem; }}
  button {{
    background: #0a84ff;
    color: #fff;
    border: none;
    border-radius: 4px;
    padding: 0.5rem 1.1rem;
    font-size: 1rem;
  }}
  pre {{
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.82rem;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.45;
  }}
  .sm-bold {{ font-weight: 600; color: #fff; }}
  .sm-dim {{ color: #888; }}
  .sm-cyan {{ color: #4fc1ff; }}
  .sm-green {{ color: #6bcf6b; }}
  .sm-blue {{ color: #6ab0f3; }}
  .sm-match {{ font-weight: 700; color: #ffd34d; }}
  .meta {{ color: #777; font-size: 0.75rem; margin-bottom: 0.5rem;
           font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
</style>
</head>
<body>
<h1>search_messages</h1>
<form method="get">
  <input class="pattern" type="text" name="q" value="{q}"
         placeholder="search pattern (regex)" autofocus>
  <fieldset>
    <legend>options</legend>
    <label><input type="checkbox" name="i" {i_checked}> ignore case</label>
    <label><input type="checkbox" name="F" {F_checked}> fixed string</label>
    <label>name <input type="text" name="name" value="{name}" style="width:7em"></label>
    <label>context <input type="number" name="C" value="{C}" min="0" style="width:3.5em"></label>
  </fieldset>
  <fieldset>
    <legend>mode</legend>
    <label><input type="radio" name="mode" value="normal" {mode_normal}> matches</label>
    <label><input type="radio" name="mode" value="count" {mode_count}> count</label>
    <label><input type="radio" name="mode" value="threads" {mode_threads}> threads</label>
  </fieldset>
  <fieldset>
    <legend>results</legend>
    <label><input type="number" name="results" value="{results}" min="1" style="width:4.5em"> max</label>
    <label><input type="checkbox" name="all" {all_checked}> all</label>
  </fieldset>
  <button type="submit">Search</button>
</form>
{meta}<pre>{output}</pre>
</body>
</html>
"""


# ── Search ─────────────────────────────────────────────────────────────────────


def run_search(
    q: str,
    *,
    ignore_case: bool,
    fixed: bool,
    name: str,
    mode: str,
    context: int,
    results: int,
    show_all: bool,
) -> tuple[str, list[str]]:
    """Run search_messages.py and return (html_output, argv_used)."""
    args = [sys.executable, str(SEARCH_SCRIPT), "--color=always"]
    if ignore_case:
        args.append("-i")
    if fixed:
        args.append("-F")
    if name:
        args += ["--name", name]

    if mode == "count":
        args.append("-c")
    elif mode == "threads":
        args.append("-l")
    else:
        args.append("-n")
        if context > 0:
            args += ["-C", str(context)]
        if show_all:
            args.append("--all")
        else:
            args += ["--results", str(results)]

    args.append(q)

    proc = subprocess.run(args, capture_output=True, text=True)
    raw = proc.stdout or proc.stderr
    return ansi_to_html(raw), args[2:]


# ── Routes ────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    ignore_case = request.args.get("i") == "on"
    fixed = request.args.get("F") == "on"
    show_all = request.args.get("all") == "on"
    name = request.args.get("name", "").strip()
    mode = request.args.get("mode", "normal")

    try:
        context = int(request.args.get("C", "0") or 0)
    except ValueError:
        context = 0
    try:
        results = int(request.args.get("results", "20") or 20)
    except ValueError:
        results = 20
    context = max(context, 0)
    results = max(results, 1)

    output, meta = "", ""
    if q:
        output, argv = run_search(
            q,
            ignore_case=ignore_case,
            fixed=fixed,
            name=name,
            mode=mode,
            context=context,
            results=results,
            show_all=show_all,
        )
        meta = f'<div class="meta">$ search_messages {" ".join(argv)}</div>\n'

    return PAGE_TEMPLATE.format(
        q=html.escape(q),
        i_checked="checked" if ignore_case else "",
        F_checked="checked" if fixed else "",
        all_checked="checked" if show_all else "",
        results=results,
        C=context,
        name=html.escape(name),
        mode_normal="checked" if mode == "normal" else "",
        mode_count="checked" if mode == "count" else "",
        mode_threads="checked" if mode == "threads" else "",
        meta=meta,
        output=output,
    )


# ── Entry point ──────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description="Web UI for search_messages.py")
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (default: 127.0.0.1; use 0.0.0.0 for LAN access)",
    )
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="log every request (default: only warnings/errors, "
        "quiet enough to leave running in the background)",
    )
    args = ap.parse_args()

    if not args.verbose:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    print(f"search_messages web UI: http://{args.host}:{args.port}")
    if args.host != "127.0.0.1":
        print("  bound to a non-localhost address — no authentication is provided,")
        print("  so only do this on a trusted network (or behind Tailscale).")

    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
