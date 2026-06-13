#!/usr/bin/env python3
"""
search_messages.py — rg-style search over iMessage threads in chat.db

Searches messages the way ripgrep searches lines, with threads playing the
role of files. All DB access, attributedBody decoding, contact resolution,
and timestamp formatting is shared with extract_messages.py, which is
imported as a module.

Usage:
    search_messages.py "pattern"
    search_messages.py -i -C 2 "swift.*benchmark"
    search_messages.py -F -e LLM -e Nile --name marion
    search_messages.py -c kitty
    search_messages.py -l Codeberg
    search_messages.py --chat 42 -A 3 lunch

Design: see search_messages_spec.md.
"""

import argparse
import re
import sys
from pathlib import Path

import extract_messages as em

TRUNCATE_AT = 200  # chars shown per message in normal (no-context) mode
MATCH_LEAD = 40  # lead-in chars when a late match forces a text window

# Tapback adds are 2001-2006, removals 3001-3006 — exclude the whole band.
# (range membership testing is O(1) in Python.)
TAPBACK_BAND = range(2000, 4000)


# ── Styling ───────────────────────────────────────────────────────────────────


class Style:
    """ANSI escape codes, or empty strings when color is disabled."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.bold = "\033[1m" if enabled else ""
        self.dim = "\033[2m" if enabled else ""
        self.cyan = "\033[36m" if enabled else ""
        self.green = "\033[32m" if enabled else ""
        self.blue = "\033[34m" if enabled else ""
        self.match = "\033[1;33m" if enabled else ""  # bold yellow
        self.reset = "\033[0m" if enabled else ""


# ── Patterns ──────────────────────────────────────────────────────────────────


def gather_patterns(args) -> list[str]:
    """Collect patterns from the positional arg, -e flags, and -f file."""
    patterns = []
    if args.pattern is not None:
        patterns.append(args.pattern)
    patterns.extend(args.regexp or [])
    if args.file:
        path = Path(args.file)
        if not path.exists():
            sys.exit(f"Pattern file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    if not patterns:
        sys.exit("No pattern given — supply PATTERN, -e PAT, or -f FILE.")
    return patterns


def compile_patterns(patterns, fixed, force_ignore_case):
    """Combine all patterns into one alternation regex (OR semantics).

    Smart case (the default): case-insensitive iff every pattern is
    all-lowercase. -i forces case-insensitive regardless.
    """
    parts = [re.escape(p) if fixed else f"(?:{p})" for p in patterns]
    ignore = force_ignore_case or all(p == p.lower() for p in patterns)
    flags = re.IGNORECASE if ignore else 0
    try:
        return re.compile("|".join(parts), flags)
    except re.error as exc:
        sys.exit(f"Bad pattern: {exc}")


# ── Scope ─────────────────────────────────────────────────────────────────────


def resolve_scope(con, threads, args):
    """Return the set of chat_ids to search, or None for all threads."""
    if args.chat is not None:
        if not any(t["chat_id"] == args.chat for t in threads):
            sys.exit(
                f"Chat ID {args.chat} not found. "
                "Run extract_messages.py --list to see available threads."
            )
        return {args.chat}
    if args.name:
        matches = em.find_threads_by_name(con, args.name)
        if not matches:
            sys.exit(f"No threads found matching '{args.name}'")
        return {t["chat_id"] for t in matches}
    return None


# ── Row loading ───────────────────────────────────────────────────────────────


def open_row_cursor(con, chat_ids):
    """Cursor over all messages, grouped by thread, with per-thread indices.

    msg_idx is the 1-based position of the message within its thread,
    counting every message (including tapbacks and system events) so the
    numbering stays stable regardless of display filtering.
    """
    scope, params = "", []
    if chat_ids is not None:
        placeholders = ",".join("?" * len(chat_ids))
        scope = f"WHERE chat_id IN ({placeholders})"
        params = sorted(chat_ids)
    cur = con.cursor()
    cur.execute(
        f"""
        SELECT * FROM (
            SELECT
                m.ROWID                  AS msg_rowid,
                m.text,
                m.attributedBody,
                m.date,
                m.is_from_me,
                m.item_type,
                m.associated_message_type,
                m.group_title,
                m.cache_has_attachments,
                cmj.chat_id,
                h.id                     AS sender_id,
                ROW_NUMBER() OVER (
                    PARTITION BY cmj.chat_id
                    ORDER BY m.date, m.ROWID
                )                        AS msg_idx
            FROM message m
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            LEFT JOIN handle h ON m.handle_id = h.ROWID
        ) {scope}
        ORDER BY chat_id, msg_idx
        """,
        params,
    )
    return cur


# ── Matching ──────────────────────────────────────────────────────────────────


def is_displayable(row) -> bool:
    """True for rows that may appear in output (everything except tapbacks)."""
    amt = row["associated_message_type"] or 0
    return amt not in TAPBACK_BAND


def get_text(row) -> str:
    """Message text: text column first, decoded attributedBody as fallback.

    Cached on the row dict; releases the blob after first use.
    """
    if "_text" not in row:
        text = (row["text"] or "").strip()
        if not text:
            text = em.decode_attributed_body(row["attributedBody"]) or ""
        row["_text"] = text
        row["attributedBody"] = None
    return row["_text"]


def scan(cursor, rx, keep_rows, max_count):
    """Single pass over all rows; returns {chat_id: thread_result}.

    thread_result = {"rows": [displayable rows] or None,
                     "matches": [matching rows, chronological]}

    "rows" is populated only when context display needs neighbours
    (keep_rows=True); matching rows then carry "_idx" into that list.
    With max_count, only the N most recent matches per thread are kept.
    """
    results = {}
    rows, matches, current = [], [], None

    def flush(cid):
        nonlocal rows, matches
        kept = matches[-max_count:] if max_count else matches
        if kept:
            results[cid] = {"rows": rows if keep_rows else None, "matches": kept}
        rows, matches = [], []

    for r in cursor:
        row = dict(r)
        cid = row["chat_id"]
        if cid != current:
            if current is not None:
                flush(current)
            current = cid
        if not is_displayable(row):
            continue
        if keep_rows:
            row["_idx"] = len(rows)
            rows.append(row)
        if row["item_type"] == 0:
            text = get_text(row)
            if text and rx.search(text):
                matches.append(row)
    if current is not None:
        flush(current)
    return results


def apply_global_cap(results, limit):
    """Keep only the `limit` most recent matches across all threads."""
    if limit is None:
        return results
    everything = []
    for tr in results.values():
        everything.extend(tr["matches"])
    everything.sort(key=lambda row: row["date"] or 0, reverse=True)
    keep = {id(row) for row in everything[:limit]}
    capped = {}
    for cid, tr in results.items():
        kept = [row for row in tr["matches"] if id(row) in keep]
        if kept:
            capped[cid] = {"rows": tr["rows"], "matches": kept}
    return capped


# ── Rendering ─────────────────────────────────────────────────────────────────


def thread_order(results):
    """chat_ids ordered by their most recent match, newest first."""
    return sorted(
        results,
        key=lambda cid: max(row["date"] or 0 for row in results[cid]["matches"]),
        reverse=True,
    )


def heading(cid, label_map, st):
    label = label_map.get(cid, f"chat {cid}")
    return f"{st.bold}[{cid}] {label}{st.reset}"


def header_line(row, sep, show_num, st):
    num = f"{st.cyan}{row['msg_idx']}{st.reset}{sep} " if show_num else ""
    sender = "Me" if row["is_from_me"] else em.resolve(row["sender_id"] or "?")
    color = st.green if row["is_from_me"] else st.blue
    ts = em.fmt_ts(row["date"])
    return f"{num}{st.dim}{ts}{st.reset}  {color}{sender}{st.reset}"


def highlight(text, rx, st):
    if not st.enabled:
        return text
    return rx.sub(lambda m: f"{st.match}{m.group(0)}{st.reset}", text)


def clip(text, rx, max_width):
    """Truncate to max_width chars while keeping the first match visible.

    max_width == 0 disables truncation (full message shown).
    """
    if max_width == 0 or len(text) <= max_width:
        return text
    m = rx.search(text)
    start = m.start() if m else 0
    if start <= max_width - MATCH_LEAD:
        return text[:max_width] + "…"
    win_start = start - MATCH_LEAD
    window = text[win_start : win_start + max_width]
    suffix = "…" if win_start + max_width < len(text) else ""
    return "…" + window + suffix


def system_label(row) -> str:
    title = row["group_title"]
    if title:
        return f"[group renamed to: {title}]"
    item = row["item_type"]
    label = em.ITEM_TYPE.get(item, f"system event {item}")
    return f"[{label}]"


def body_lines(text):
    return [f"    {line}" for line in text.split("\n")]


def context_body(row, rx, st, is_match):
    if row["item_type"] != 0:
        return system_label(row)
    text = get_text(row)
    if not text:
        return "(photo/media)" if row["cache_has_attachments"] else "(no text)"
    return highlight(text, rx, st) if is_match else text


def merge_windows(match_indices, before, after, n_rows):
    """Expand each match index into [lo, hi]; merge overlapping/adjacent windows."""
    windows = []
    for i in match_indices:
        lo, hi = max(0, i - before), min(n_rows - 1, i + after)
        if windows and lo <= windows[-1][1] + 1:
            windows[-1][1] = max(windows[-1][1], hi)
        else:
            windows.append([lo, hi])
    return windows


def render_normal(results, label_map, st, show_num, rx, max_width):
    lines = []
    for t, cid in enumerate(thread_order(results)):
        if t:
            lines.append("")
        lines.append(heading(cid, label_map, st))
        msgs = sorted(results[cid]["matches"], key=lambda row: row["msg_idx"])
        for j, row in enumerate(msgs):
            if j:
                lines.append(f"{st.dim}--{st.reset}")
            lines.append(header_line(row, ":", show_num, st))
            lines.extend(
                body_lines(highlight(clip(get_text(row), rx, max_width), rx, st))
            )
    return lines


def render_context(results, label_map, st, show_num, rx, before, after):
    lines = []
    for t, cid in enumerate(thread_order(results)):
        if t:
            lines.append("")
        lines.append(heading(cid, label_map, st))
        rows = results[cid]["rows"]
        match_idx = sorted(row["_idx"] for row in results[cid]["matches"])
        match_set = set(match_idx)
        for w, (lo, hi) in enumerate(
            merge_windows(match_idx, before, after, len(rows))
        ):
            if w:
                lines.append(f"{st.dim}--{st.reset}")
            for i in range(lo, hi + 1):
                row = rows[i]
                is_match = i in match_set
                sep = ":" if is_match else "-"
                lines.append(header_line(row, sep, show_num, st))
                lines.extend(body_lines(context_body(row, rx, st, is_match)))
    return lines


def render_counts(results, label_map, st, rx, count_matches):
    items = []
    for cid, tr in results.items():
        if count_matches:
            n = sum(sum(1 for _ in rx.finditer(get_text(row))) for row in tr["matches"])
        else:
            n = len(tr["matches"])
        items.append((n, cid))
    items.sort(key=lambda pair: (-pair[0], pair[1]))

    lines, width = [], 0
    for n, cid in items:
        label = label_map.get(cid, f"chat {cid}")
        width = max(width, len(f"[{cid}] {label}: {n}"))
        lines.append(f"{st.bold}[{cid}] {label}{st.reset}: {n}")
    total = sum(n for n, _ in items)
    unit = "matches" if count_matches else "matching messages"
    lines.append(f"{st.dim}{'─' * width}{st.reset}")
    lines.append(f"Total: {total} {unit} in {len(items)} thread(s)")
    return lines


def render_thread_list(results, label_map):
    return [
        f"[{cid}] {label_map.get(cid, f'chat {cid}')}" for cid in thread_order(results)
    ]


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args():
    ap = argparse.ArgumentParser(
        prog="search_messages",
        description="rg-style search over iMessage threads in chat.db",
    )
    ap.add_argument("pattern", nargs="?", help="Regular expression to search for")

    pat = ap.add_argument_group("pattern options")
    pat.add_argument(
        "-e",
        "--regexp",
        action="append",
        metavar="PAT",
        help="Additional pattern (repeatable; a message matches if any pattern matches)",
    )
    pat.add_argument(
        "-f",
        "--file",
        metavar="FILE",
        help="Read patterns from FILE, one per line (# comments and blanks skipped)",
    )
    pat.add_argument(
        "-F",
        "--fixed-strings",
        action="store_true",
        help="Treat all patterns as literal strings",
    )

    srch = ap.add_argument_group("search options")
    srch.add_argument(
        "-i", "--ignore-case", action="store_true", help="Case-insensitive matching"
    )
    srch.add_argument(
        "-S",
        "--smart-case",
        action="store_true",
        help="Case-insensitive iff all patterns are lowercase (this is the default)",
    )
    srch.add_argument(
        "-m",
        "--max-count",
        type=int,
        metavar="N",
        help="Keep at most the N most recent matches per thread",
    )

    scope = ap.add_argument_group("scope options")
    scope.add_argument(
        "--chat", type=int, metavar="CHAT_ID", help="Restrict search to one thread"
    )
    scope.add_argument(
        "--name",
        metavar="STRING",
        help="Restrict to threads matching STRING (handle/name substring)",
    )

    out = ap.add_argument_group("output options")
    out.add_argument(
        "-n",
        "--msg-number",
        action="store_true",
        help="Show message numbers (default when stdout is a TTY)",
    )
    out.add_argument(
        "-N",
        "--no-msg-number",
        action="store_true",
        help="Suppress message numbers (wins over -n/-p)",
    )
    out.add_argument(
        "-A",
        "--after-context",
        type=int,
        default=0,
        metavar="N",
        help="Show N messages after each match",
    )
    out.add_argument(
        "-B",
        "--before-context",
        type=int,
        default=0,
        metavar="N",
        help="Show N messages before each match",
    )
    out.add_argument(
        "-C",
        "--context",
        type=int,
        metavar="N",
        help="Show N messages before and after each match",
    )
    out.add_argument(
        "-w",
        "--max-width",
        type=int,
        default=TRUNCATE_AT,
        metavar="N",
        help=f"Truncate each match to N chars, keeping the match visible "
        f"(default: {TRUNCATE_AT}; use 0 for no limit). "
        f"No effect in context mode, which always shows full messages.",
    )
    out.add_argument(
        "-p", "--pretty", action="store_true", help="Force color and message numbers"
    )
    out.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="When to use color (default: auto)",
    )
    out.add_argument(
        "--results",
        type=int,
        default=20,
        metavar="N",
        help="Max total matching messages to report (default: 20)",
    )
    out.add_argument(
        "--all", action="store_true", help="Report all matches (no --results cap)"
    )
    out.add_argument(
        "--out", metavar="FILE", help="Write output to FILE instead of stdout"
    )
    out.add_argument(
        "--contacts",
        metavar="FILE",
        help=f"TSV file of handle→name mappings (default: {em.DEFAULT_CONTACTS})",
    )

    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "-c",
        "--count",
        action="store_true",
        help="Print matching-message count per thread",
    )
    mode.add_argument(
        "--count-matches",
        action="store_true",
        help="Print match-occurrence count per thread",
    )
    mode.add_argument(
        "-l",
        "--threads-with-matches",
        action="store_true",
        help="Print only the labels of threads with matches",
    )

    args = ap.parse_args()
    if not args.all and args.results < 1:
        ap.error("--results must be >= 1 (use --all for no limit)")
    if args.max_count is not None and args.max_count < 1:
        ap.error("--max-count must be >= 1")
    if args.max_width < 0:
        ap.error("--max-width must be >= 0 (use 0 for no limit)")
    return args


def main():
    args = parse_args()
    patterns = gather_patterns(args)
    rx = compile_patterns(patterns, args.fixed_strings, args.ignore_case)

    em.load_contacts(Path(args.contacts) if args.contacts else em.DEFAULT_CONTACTS)
    con = em.connect()
    try:
        threads = em.load_threads(con)
        label_map = {t["chat_id"]: em.thread_label(t) for t in threads}
        chat_ids = resolve_scope(con, threads, args)

        before = args.context if args.context is not None else args.before_context
        after = args.context if args.context is not None else args.after_context
        count_mode = args.count or args.count_matches or args.threads_with_matches
        keep_rows = (before > 0 or after > 0) and not count_mode

        results = scan(open_row_cursor(con, chat_ids), rx, keep_rows, args.max_count)
    finally:
        con.close()

    if not results:
        print("No messages matched.")
        sys.exit(1)

    to_file = args.out is not None
    color_on = (
        args.color == "always"
        or (args.pretty and args.color != "never")
        or (args.color == "auto" and sys.stdout.isatty() and not to_file)
    )
    st = Style(color_on)
    if args.no_msg_number:
        show_num = False
    elif args.msg_number or args.pretty:
        show_num = True
    else:
        show_num = sys.stdout.isatty() and not to_file

    if args.count or args.count_matches:
        lines = render_counts(results, label_map, st, rx, args.count_matches)
    elif args.threads_with_matches:
        lines = render_thread_list(results, label_map)
    else:
        if not args.all:
            results = apply_global_cap(results, args.results)
        if keep_rows:
            lines = render_context(results, label_map, st, show_num, rx, before, after)
        else:
            lines = render_normal(results, label_map, st, show_num, rx, args.max_width)

    output = "\n".join(lines) + "\n"
    if to_file:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}")
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
