---
schema_version: "1.0"
title: Incremental Processing with Byte-Offset Resume
scope: state management
pattern_type: behavioral
transferable: true
---

# Incremental Processing with Byte-Offset Resume

## Overview

The system tracks processed files by inode, mtime, and byte offset to enable incremental re-processing without re-reading already-ingested data. When a file grows (same inode, newer mtime), processing resumes from the last known byte offset. When a file is replaced (different inode), it is reprocessed from the beginning.

## How It Works

`StateManager` stores a `ProcessedFile` record for each session file:

```
ProcessedFile:
  file_path: str       # canonical path
  inode: int           # filesystem inode number
  mtime: float         # last modification time
  last_byte_offset: int # byte position after last read
```

On each run, `should_reprocess(file_path)` returns:
- **(False, 0)** if inode and mtime match the stored record (skip, already processed)
- **(True, last_byte_offset)** if inode matches but mtime is newer (resume from offset)
- **(True, 0)** if inode differs or no record exists (full reprocess)

The parser uses the byte offset to `seek()` into the file, then discards the first partial line (to avoid landing mid-UTF8 or mid-JSON-line) before parsing subsequent lines.

State is persisted to `~/.opentraces/state.json` after each file is processed, providing crash safety at the file granularity.

## Key Files

- `src/opentraces/state.py` - StateManager, ProcessedFile, should_reprocess()
- `src/opentraces/parsers/claude_code.py` - parse_session(session_path, byte_offset)
- `src/opentraces/cli.py` - parse command orchestration with incremental resume

## How to Replicate

1. Define a processed-file record with path, inode, mtime, and byte offset
2. Before processing each file, check the stored record against current filesystem metadata
3. Use inode comparison to detect file replacement (log rotation, atomic writes)
4. Use mtime comparison to detect file growth (append-only logs)
5. On resume, seek to the stored offset and discard the first partial line
6. After processing, update the record with the new mtime and final byte offset
7. Persist state after each file to limit re-work on crash

## When to Use

- Processing append-only log files that grow over time
- Agent session files that are written incrementally during a session
- When reprocessing from scratch is expensive (large files, slow enrichment)
- When the data source does not provide a built-in offset mechanism (unlike Kafka)

## When NOT to Use

- When files are modified in-place (not append-only), byte offsets would read inconsistent data
- When files are small enough that full reprocessing is cheap
- When a message queue or change-data-capture system provides its own offset tracking
- When filesystem metadata (inode, mtime) is unreliable (network filesystems, some containers)
