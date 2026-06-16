# extract_messages

Command-line tools for reading and searching your Messages history on macOS.

- **`extract_messages.py`** — list threads and extract them to readable Markdown transcripts
- **`search_messages.py`** — search across all threads, modeled after `rg` (ripgrep)
- **`search_messages_web.py`** — minimal web UI for `search_messages.py`, for use from a phone or iPad
- **`dump_handles.py`** — enumerate handle IDs to help build a contacts file

All tools read directly from `~/Library/Messages/chat.db`.

## Prerequisites

- macOS with the Messages app
- Python 3.10+
- **Full Disk Access** granted to the terminal app (or app) running these scripts
  (System Settings → Privacy & Security → Full Disk Access)

## Contacts file

By default the tools look for a contacts file at
`~/.config/extract_messages/contacts.tsv`. It maps raw handle IDs (phone numbers,
email addresses) to display names:

```
# handle	name
+15035550100	Jason Williams
me@example.com	Work Account
```

Format: tab-separated, one entry per line, `#` comments skipped. Use
`dump_handles.py` to get a list of all handle IDs in your database, then fill in
names for the ones you care about.

Override the path with `--contacts FILE` on any script.

---

## `extract_messages.py`

Extract Messages threads from `chat.db` to Markdown transcripts.

### Modes

```
extract_messages.py --list [N]        # list N most recent threads (default 30)
extract_messages.py --id CHAT_ID      # extract thread by chat_id
extract_messages.py --name STRING     # find thread by name/handle substring
extract_messages.py --search TEXT     # search message text across all threads
```

### Options

```
--out FILE       write output to FILE (default: stdout)
--tail N         show only the last N messages of a thread
--results N      max results to show for --search and --list (default: 20)
--contacts FILE  path to contacts TSV
```

### Examples

```sh
# List your 10 most recent threads
extract_messages.py --list 10

# Extract a thread to a file
extract_messages.py --id 42 --out thread-42.md

# Find a thread and extract it
extract_messages.py --name Julia

# Quick message search
extract_messages.py --search "standing desk"
```

---

## `search_messages.py`

Full-featured search across Messages threads, modelled after `rg`.

### Usage

```
search_messages.py [OPTIONS] [PATTERN]
```

`PATTERN` is a regular expression. Required unless `-e` or `-f` is given.

### Pattern options

```
-e PAT, --regexp PAT    add a pattern (repeatable; OR semantics)
-f FILE, --file FILE    read patterns from FILE (one per line; # comments skipped)
-F, --fixed-strings     treat all patterns as literal strings
```

### Search options

```
-i, --ignore-case       case-insensitive
-S, --smart-case        case-insensitive iff all patterns are lowercase (default)
-m N, --max-count N     keep the N most recent matches per thread
```

### Scope options

```
--chat CHAT_ID          search only this thread (use extract_messages.py --list to find IDs)
--name STRING           restrict to threads matching STRING
```

### Output options

```
-n, --msg-number        show message number within thread (default when stdout is a TTY)
-N, --no-msg-number     suppress message numbers
-A N                    show N messages after each match
-B N                    show N messages before each match
-C N                    show N messages before and after each match
-w N, --max-width N     truncate each match to N chars, keeping the match
                        visible (default: 200; 0 = no limit; ignored with -A/-B/-C)
-p, --pretty            force color and message numbers
--color auto|always|never
--results N             max total matching messages (default: 20)
--all                   no results cap
--out FILE              write to FILE
--contacts FILE         path to contacts TSV
```

### Output modes (mutually exclusive)

```
-c, --count             matching-message count per thread
--count-matches         match-occurrence count per thread
-l, --threads-with-matches  print only thread labels with ≥1 match
```

### Examples

```sh
# Basic search (smart-case, 20 results)
search_messages.py "standing desk"

# Case-insensitive regex, show 5 messages of context
search_messages.py -i -C 2 "swift.*benchmark"

# Multiple patterns (OR), fixed strings, within one contact
search_messages.py -F -e "LLM" -e "Nile" --name Julia

# Count matches per thread, no results cap
search_messages.py -c --all homebrew

# List threads that mention Codeberg
search_messages.py -l Codeberg

# Show the full text of a match instead of the 200-char snippet
search_messages.py -w 0 "M5 Max"

# Write results to a file
search_messages.py --all --out results.md "kitty"
```

---

## `search_messages_web.py`

Minimal Flask-based web UI for `search_messages.py`, for use from an iPad or
phone where a terminal isn't convenient. Runs `search_messages.py` as a
subprocess for each request and renders its `--color=always` output as HTML
(ANSI codes mapped to CSS classes), with a form covering the most commonly
used flags: pattern, `-i`, `-F`, `--name`, `-C`, `-c`/`-l` modes, `--results`/`--all`.

### Usage

```sh
# Local only (default)
uv run --no-project search_messages_web.py

# Reachable from other devices on the network (e.g. an iPad)
uv run --no-project search_messages_web.py --host 0.0.0.0 --port 8765
```

Then visit `http://<hostname>.local:8765` from the iPad's browser — "Add to
Home Screen" in Safari gives it an app-like icon. Use `http://`, not
`https://` — the dev server only speaks plain HTTP, and some browsers try
HTTPS by default for bare hostnames, which shows up server-side as garbled
"Bad request version" lines in the log (a TLS handshake hitting an HTTP
parser). If a saved home-screen icon was created from a bad URL, delete it
and redo "Add to Home Screen" after loading the correct `http://` address.

There is **no authentication**. `--host 0.0.0.0` is fine on a trusted home LAN
or over Tailscale, but don't expose this to the open internet — it's a direct
window into your Messages history.

Declares its `flask` dependency via inline script metadata (PEP 723), so
`uv run --no-project` handles the environment automatically.

### Running it in the background

This is meant to be started once per machine and left running — e.g. one
instance per household member, each on their own Mac, searching their own
`chat.db`. To start it detached so it survives closing the terminal or ssh
session:

```sh
nohup uv run --no-project search_messages_web.py --host 0.0.0.0 --port 8765 \
    < /dev/null > search_messages_web.log 2>&1 &
```

All three redirects matter:

- `< /dev/null` detaches stdin. Without this, the process still holds a
  reference to the terminal's pty; if that terminal later closes (ssh
  disconnect, closed window, killed tmux pane), the now-orphaned process can
  be left with broken file descriptors. In practice this can resurface later
  as completely unrelated commands run in that same terminal session
  crashing at Python startup with `Fatal Python error: init_sys_streams` /
  `OSError: [Errno 9] Bad file descriptor` — `nohup` alone only blocks
  `SIGHUP`, it doesn't sever stdin.
- `> search_messages_web.log 2>&1` captures the startup banner and any
  warnings/errors instead of losing them once the terminal is gone.
- the trailing `&` backgrounds it so the shell returns immediately.

By default the server only logs warnings/errors, not every request, so the
log file should stay quiet during normal use. Pass `--verbose` before
backgrounding if you need full per-request logging while debugging.

To stop it later: `pkill -f search_messages_web.py`, or find the PID with
`ps aux | grep search_messages_web.py` and `kill` it.

---

## `dump_handles.py`

Prints every unique handle ID in the database, one per line, sorted. Useful for
bootstrapping a contacts file.

```sh
dump_handles.py > handles.txt
# then open handles.txt and fill in the name column
```

---

## Notes

- All scripts open the database read-only (`PRAGMA query_only = ON`).
- `search_messages.py` imports `extract_messages` as a module for shared DB
  access, decoding, and contact resolution. `search_messages_web.py` calls
  `search_messages.py` as a subprocess rather than importing it.
- Timestamps are stored as nanoseconds since 2001-01-01 (Apple epoch).
- Messages whose `text` column is NULL are decoded from the `attributedBody`
  NSArchiver blob.
- Tapbacks and system events are excluded from search results.
- Exit code from `search_messages.py` follows `rg` convention: 0 = matches
  found, 1 = no matches.
