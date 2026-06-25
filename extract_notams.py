#!/usr/bin/env python3
"""
NOTAM List Cleaner — JSON output
=================================
Extracts NOTAM entries prefixed with '+' from a raw NOTAM report file,
strips the preamble header and page-break footers, then writes a JSON
array where each element represents one NOTAM with parsed fields.

Output fields per NOTAM:
  id            – e.g. "A1737/26"
  type          – single-letter code, e.g. "N", "R"
  message       – body text (before structured fields), whitespace normalised
  lower         – lower altitude limit, or null
  upper         – upper altitude limit, or null
  from          – effective-from datetime string, or null
  to            – effective-to datetime string, or null
  time_schedule – time schedule string, or null
  raw           – the unmodified block text for reference

Usage:
    python extract_notams.py <input_file> [output_file]

    If no output file is given, output is written to
    <input_stem>.json in the same directory.
"""

import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

PAGE_FOOTER_RE = re.compile(
    r"^\s*\d{1,2}\s+\w{3}\s+\d{4}\s+-\s+\d{2}:\d{2}:\d{2}\s+Page\s+\d+/\d+\s*$"
)

# The header '+' line:  "+ A1737/26 N   <text…>"
NOTAM_HEADER_RE = re.compile(
    r"^\+\s+(?P<id>[A-Z]\d+/\d+)\s+(?P<type>[A-Z])\s*(?P<rest>.*)"
)

# Structured field lines (matched against individual stripped lines)
LOWER_UPPER_LINE_RE = re.compile(
    r"^Lower:\s*(?P<lower>\S+)\s+Upper:\s*(?P<upper>.+)$", re.IGNORECASE
)
FROM_TO_LINE_RE = re.compile(
    r"^From:\s*(?P<from>.+?)\s{2,}To:\s*(?P<to>.+)$", re.IGNORECASE
)
TIME_SCHED_LINE_RE = re.compile(
    r"^Time schedule:\s*(?P<sched>.*)$", re.IGNORECASE
)

# Labels that mark the start of a structured section — used to stop
# collecting message lines
STRUCTURED_LABELS_RE = re.compile(
    r"^(Lower:|From:|Time schedule:)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Block collection
# ---------------------------------------------------------------------------

def is_page_footer(line: str) -> bool:
    return bool(PAGE_FOOTER_RE.match(line))


def find_first_notam_line(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line.lstrip().startswith("+"):
            return i
    return len(lines)


def collect_raw_blocks(lines: list[str]) -> list[list[str]]:
    """Return one list-of-lines per '+' NOTAM entry, page footers dropped."""
    start = find_first_notam_line(lines)
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines[start:]:
        stripped = line.rstrip()
        if is_page_footer(stripped):
            continue
        if stripped.lstrip().startswith("+"):
            if current:
                blocks.append(current)
            current = [stripped]
        elif current:
            current.append(stripped)

    if current:
        blocks.append(current)
    return blocks


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def normalise_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_block(block: list[str]) -> dict:
    # Trim trailing blank lines
    while block and not block[-1].strip():
        block.pop()

    raw_text = "\n".join(block)

    # Parse the header line
    header_line = block[0].strip()
    hm = NOTAM_HEADER_RE.match(header_line)
    notam_id   = hm.group("id")   if hm else None
    notam_type = hm.group("type") if hm else None
    first_rest = (hm.group("rest") if hm else header_line.lstrip("+ ")).strip()

    # All lines after the header, stripped
    cont_lines = [ln.strip() for ln in block[1:]]

    # Walk lines to assign each to message / structured fields
    # The message is everything up to the first structured-field line.
    message_parts: list[str] = [first_rest] if first_rest else []
    lower = upper = from_dt = to_dt = None
    sched_parts: list[str] = []
    in_sched = False

    for line in cont_lines:
        if not line:
            in_sched = False   # blank line ends a multi-line time schedule
            continue

        lu_m = LOWER_UPPER_LINE_RE.match(line)
        ft_m = FROM_TO_LINE_RE.match(line)
        ts_m = TIME_SCHED_LINE_RE.match(line)

        if lu_m:
            in_sched = False
            lower = lu_m.group("lower").strip()
            upper = lu_m.group("upper").strip()
        elif ft_m:
            in_sched = False
            from_dt = ft_m.group("from").strip()
            to_dt   = ft_m.group("to").strip()
        elif ts_m:
            in_sched = True
            sched_text = ts_m.group("sched").strip()
            if sched_text:
                sched_parts.append(sched_text)
        elif in_sched:
            # Continuation of a multi-line time schedule
            sched_parts.append(line)
        elif lower is None and from_dt is None:
            # Still in the message body (no structured field seen yet)
            message_parts.append(line)
        # else: orphan continuation after structured fields — skip

    message = normalise_ws(" ".join(message_parts))
    time_sched = normalise_ws(" ".join(sched_parts)) or None

    return {
        "id":            notam_id,
        "type":          notam_type,
        "message":       message,
        "lower":         lower,
        "upper":         upper,
        "from":          from_dt,
        "to":            to_dt,
        "time_schedule": time_sched,
        "raw":           raw_text,
    }

def extract_from_text(text):
    lines=text.splitlines()
    blocks = collect_raw_blocks(lines)
    notams = [parse_block(b) for b in blocks]
    return notams

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"Error: file not found – {input_path}")
        sys.exit(1)

    output_path = (
        Path(sys.argv[2]) if len(sys.argv) >= 3
        else input_path.with_suffix(".json")
    )

    lines = input_path.read_text(encoding="utf-8").splitlines()
    blocks = collect_raw_blocks(lines)
    notams = [parse_block(b) for b in blocks]

    output_path.write_text(
        json.dumps(notams, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Done. {len(notams)} NOTAM entries written to {output_path}")


if __name__ == "__main__":
    main()
