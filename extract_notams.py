#!/usr/bin/env python3
"""
NOTAM List Cleaner
==================
Extracts NOTAM entries prefixed with '+' from a raw NOTAM report file,
strips the preamble header and page-break footers, then writes structured
output in either JSON or GeoJSON format.

JSON output fields per NOTAM:
  id            – e.g. "A1737/26"
  type          – single-letter code, e.g. "N", "R"
  message       – body text (before structured fields), whitespace normalised
  lower         – lower altitude limit, or null
  upper         – upper altitude limit, or null
  from          – effective-from datetime string, or null
  to            – effective-to datetime string, or null
  time_schedule – time schedule string, or null
  locations     – list of dicts with raw token + decimal-degree lat/lon
  raw           – the unmodified block text for reference

GeoJSON output:
  Produces a FeatureCollection. Each NOTAM with coordinates becomes one or
  more Features:
    • 1 coordinate  → Point
    • 2 coordinates → LineString
    • 3+ coordinates → Polygon (ring automatically closed if needed)
  NOTAMs with no coordinates are included as Features with geometry: null.
  All parsed fields (except raw locations list) are stored in properties.

Usage:
    python extract_notams.py <input_file> [output_file] [--format json|geojson]

    -f / --format   json     Plain JSON array (default)
                    geojson  GeoJSON FeatureCollection

    If no output file is given, the output is written to
    <input_stem>.json or <input_stem>.geojson in the same directory.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from notam_common import normalise_ws, parse_coords
from notam_common import notam_to_geojson_feature, to_geojson


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
    locations = parse_coords(raw_text)

    return {
        "id":            notam_id,
        "type":          notam_type,
        "message":       message,
        "lower":         lower,
        "upper":         upper,
        "from":          from_dt,
        "to":            to_dt,
        "time_schedule": time_sched,
        "locations":     locations,
        "raw":           raw_text,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract NOTAM entries from a raw report file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input",  help="Path to the raw NOTAM report file")
    parser.add_argument("output", nargs="?", help="Output file path (optional)")
    parser.add_argument(
        "-f", "--format",
        choices=["json", "geojson"],
        default="json",
        help="Output format: 'json' (default) or 'geojson'",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: file not found – {input_path}")
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        suffix = ".geojson" if args.format == "geojson" else ".json"
        output_path = input_path.with_suffix(suffix)

    # Parse
    lines = input_path.read_text(encoding="utf-8").splitlines()
    blocks = collect_raw_blocks(lines)
    notams = [parse_block(b) for b in blocks]

    # Serialise
    if args.format == "geojson":
        output_data = to_geojson(notams)
    else:
        output_data = notams

    output_path.write_text(
        json.dumps(output_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    loc_count = sum(1 for n in notams if n["locations"])
    print(f"Done. {len(notams)} NOTAM entries written to {output_path}")
    print(f"      {loc_count} entries have coordinates, "
          f"{len(notams) - loc_count} have no location (null geometry in GeoJSON).")


if __name__ == "__main__":
    main()