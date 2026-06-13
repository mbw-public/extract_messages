# extract_messages

Command-line tools for reading and searching your Messages history on macOS.

- **`extract_messages.py`** — list threads and extract them to readable Markdown transcripts
- **`search_messages.py`** — search across all threads, modeled after `rg` (ripgrep)
- **`dump_handles.py`** — enumerate handle IDs to help build a contacts file

All three tools read directly from `~/Library/Messages/chat.db`.

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

## `dump_handles.py`

Prints every unique handle ID in the database, one per line, sorted. Useful for
bootstrapping a contacts file.

```sh
dump_handles.py > handles.txt
# then open handles.txt and fill in the name column
```

---

## Notes

- All three scripts open the database read-only (`PRAGMA query_only = ON`).
- `search_messages.py` imports `extract_messages` as a module for shared DB
  access, decoding, and contact resolution.
- Timestamps are stored as nanoseconds since 2001-01-01 (Apple epoch).
- Messages whose `text` column is NULL are decoded from the `attributedBody`
  NSArchiver blob.
- Tapbacks and system events are excluded from search results.
- Exit code from `search_messages.py` follows `rg` convention: 0 = matches
  found, 1 = no matches.
