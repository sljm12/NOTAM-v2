#!/usr/bin/env python3
"""
notam_extractor.py

Extracts individual NOTAM entries from a NOTAM summary report (exported as
markdown/text, e.g. from a PDF-to-markdown conversion) and outputs them as
structured JSON.

Handles common artifacts found in paginated NOTAM summary exports:
  - "--- Page N ---" separators
  - "Page N of M" footers
  - Entries missing their opening "(" (OCR/export artifact)
  - Entries with no closing ")" at all (incomplete in source)
  - Trailing bare ICAO airport-code header lines (e.g. "WMKD") that bleed
    into an entry because the entry itself has no closing paren

Usage:
    python3 notam_extractor.py INPUT_FILE [-o OUTPUT_FILE]

Example:
    python3 notam_extractor.py SUMMARY_WMFC_A_06_2026.md -o notams.json
"""

import argparse
import json
import re
import sys

# Matches the start of a NOTAM entry, e.g.:
#   (A2112/26 NOTAMN
#   (A2005/26 NOTAMR A1997/26      <- replaces an earlier NOTAM
#   A2070/26 NOTAMN                <- missing leading '(' (export artifact)
ENTRY_START_RE = re.compile(r'\(?A\d+/\d+\s+NOTAM[A-Z]?(?:\s+A\d+/\d+)?')

# A single field-marker line like "E)" with nothing else on it -- this is
# NOT a genuine closing parenthesis for the whole entry.
BARE_FIELD_MARKER_RE = re.compile(r'^[A-G]\)$')

# Trailing bare ICAO airport code on its own line (e.g. "WMKD"), which can
# leak in after an entry whose body has no real closing parenthesis.
TRAILING_ICAO_RE = re.compile(r'\n+\s*WM[A-Z]{2}\s*$')

PAGE_SEPARATOR_RE = re.compile(r'\n?---\s*Page\s+\d+\s*---\n?')
PAGE_FOOTER_RE = re.compile(r'\n?\s*Page\s+\d+\s+of\s+\d+\s*\n?')

# Field-line regexes for structured parsing of an entry's body.
FIELD_LINE_RE = re.compile(r'^([A-G])\)\s*(.*)$')
HEADER_RE = re.compile(
    r'^\(?(A\d+/\d+)\s+(NOTAM[A-Z]?)(?:\s+(A\d+/\d+))?'
)

# Matches both coordinate formats:
#   011914N1034544E              (DDMMSS, no decimal)
#   023000.00N1051628.72E        (DDMMSS.ss, with decimal seconds)
# Latitude: 6 digits (DDMMSS) + optional decimal, then N/S
# Longitude: 7 digits (DDDMMSS) + optional decimal, then E/W
COORD_RE = re.compile(
    r'(\d{2})(\d{2})(\d{2}(?:\.\d+)?)([NS])\s*'
    r'(\d{3})(\d{2})(\d{2}(?:\.\d+)?)([EW])'
)


def dms_to_ddm(deg: int, minute: int, sec: float, hemisphere: str) -> str:
    """Convert degrees/minutes/seconds to Degrees Decimal Minutes (DDM),
    e.g. 03 00 24.44 N -> 03 00.407 N"""
    decimal_minutes = minute + sec / 60.0
    return f"{deg:02d} {decimal_minutes:06.3f} {hemisphere}"


def extract_locations(text: str) -> list:
    """Find every lat/lon coordinate pair in the given text and return a
    list of dicts with the original string, decimal degrees, and DDM."""
    locations = []
    for m in COORD_RE.finditer(text):
        lat_deg, lat_min, lat_sec, lat_hem, lon_deg, lon_min, lon_sec, lon_hem = m.groups()

        lat_deg_i, lat_min_i, lat_sec_f = int(lat_deg), int(lat_min), float(lat_sec)
        lon_deg_i, lon_min_i, lon_sec_f = int(lon_deg), int(lon_min), float(lon_sec)

        lat_dd = lat_deg_i + lat_min_i / 60.0 + lat_sec_f / 3600.0
        if lat_hem == 'S':
            lat_dd = -lat_dd
        lon_dd = lon_deg_i + lon_min_i / 60.0 + lon_sec_f / 3600.0
        if lon_hem == 'W':
            lon_dd = -lon_dd

        locations.append({
            'raw': m.group(0),
            'latitude': {
                'dms': f"{lat_deg}{lat_min}{lat_sec}{lat_hem}",
                'decimal_degrees': round(lat_dd, 6),
                'ddm': dms_to_ddm(lat_deg_i, lat_min_i, lat_sec_f, lat_hem),
            },
            'longitude': {
                'dms': f"{lon_deg}{lon_min}{lon_sec}{lon_hem}",
                'decimal_degrees': round(lon_dd, 6),
                'ddm': dms_to_ddm(lon_deg_i, lon_min_i, lon_sec_f, lon_hem),
            },
        })
    return locations


def clean_page_artifacts(text: str) -> str:
    """Remove page separator and footer lines from the raw export."""
    text = PAGE_SEPARATOR_RE.sub('\n', text)
    text = PAGE_FOOTER_RE.sub('\n', text)
    return text


def find_entry_chunks(text: str):
    """Split the cleaned text into raw per-entry chunks, trimmed to their
    correct boundaries (removing trailing leaked content like stray ICAO
    header lines)."""
    starts = list(ENTRY_START_RE.finditer(text))
    chunks = []

    for i, m in enumerate(starts):
        start = m.start()
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        chunk = text[start:end].rstrip()

        if not chunk.endswith(')'):
            last_paren = chunk.rfind(')')
            if last_paren != -1:
                line_start = chunk.rfind('\n', 0, last_paren) + 1
                line_so_far = chunk[line_start:last_paren + 1].strip()
                if BARE_FIELD_MARKER_RE.fullmatch(line_so_far):
                    # No genuine closing paren in source; just strip any
                    # leaked trailing ICAO header line.
                    chunk = TRAILING_ICAO_RE.sub('', chunk).rstrip()
                else:
                    chunk = chunk[:last_paren + 1]
            else:
                chunk = TRAILING_ICAO_RE.sub('', chunk).rstrip()

        chunks.append(chunk)

    return chunks


def normalize_chunk(chunk: str) -> str:
    """Collapse stray whitespace/blank lines (introduced by page breaks)
    and ensure the entry starts with '('."""
    lines = [l.strip() for l in chunk.splitlines()]
    lines = [l for l in lines if l]
    entry = '\n'.join(lines)
    if not entry.startswith('('):
        entry = '(' + entry
    return entry


def parse_entry(raw_text: str) -> dict:
    """Parse a normalized NOTAM entry string into structured fields."""
    header_match = HEADER_RE.match(raw_text)
    notam_id = header_match.group(1) if header_match else None
    notam_type = header_match.group(2) if header_match else None
    replaces = header_match.group(3) if header_match else None

    lines = raw_text.splitlines()

    fields = {}  # e.g. {'A': '...', 'B': '...', ...}
    current_field = None
    buffer = []

    def flush():
        if current_field is not None:
            fields[current_field] = '\n'.join(buffer).strip()

    for idx, line in enumerate(lines):
        l = line.strip()
        if idx == 0:
            # header line, e.g. "(A2112/26 NOTAMN" -- skip, already parsed
            continue
        m = FIELD_LINE_RE.match(l)
        if m:
            flush()
            current_field = m.group(1)
            buffer = [m.group(2)] if m.group(2) else []
        else:
            if current_field is not None:
                buffer.append(l)
    flush()

    # Field A typically looks like "WMFC B) 2605312300 C) 2606050300"
    # because A/B/C often share one line in these reports. Split it out.
    a_raw = fields.get('A', '')
    icao_match = re.match(r'^(\S+)', a_raw)
    fid = icao_match.group(1) if icao_match else None

    # If B and/or C got swallowed into A's buffer (same line as "A)"),
    # re-extract them.
    inline_bc = re.search(r'B\)\s*(\S+)(?:\s+C\)\s*(\S+))?', a_raw)
    if inline_bc:
        if 'B' not in fields or not fields['B']:
            fields['B'] = inline_bc.group(1)
        if inline_bc.group(2) and ('C' not in fields or not fields['C']):
            fields['C'] = inline_bc.group(2)

    # Field A should just be the ICAO/FIR identifier.
    fields['A'] = fid

    # Strip a trailing lone ')' that closes the whole NOTAM but is still
    # attached to the last field's text (e.g. "F) GND G) 1000FT AMSL)").
    last_field_letter = None
    if fields:
        last_field_letter = sorted(fields.keys())[-1]
    if last_field_letter and fields[last_field_letter].endswith(')'):
        fields[last_field_letter] = fields[last_field_letter][:-1].strip()

    # G often shares a line with F, e.g. "F) GND G) 1000FT AMSL". Split it.
    if 'F' in fields and 'G' not in fields:
        f_val = fields['F']
        g_split = re.search(r'^(.*?)\s*G\)\s*(.*)$', f_val)
        if g_split:
            fields['F'] = g_split.group(1).strip()
            fields['G'] = g_split.group(2).strip()
    elif 'F' in fields and 'G' in fields:
        # G) may have been duplicated/embedded inside F) text already
        f_val = fields['F']
        g_split = re.search(r'^(.*?)\s*G\)\s*(.*)$', f_val)
        if g_split:
            fields['F'] = g_split.group(1).strip()
            if not fields.get('G'):
                fields['G'] = g_split.group(2).strip()

    return {
        'id': notam_id,
        'type': notam_type,
        'replaces': replaces,
        'fir_or_icao': fields.get('A'),
        'start': fields.get('B'),
        'end': fields.get('C'),
        'schedule': fields.get('D'),
        'description': fields.get('E'),
        'lower_limit': fields.get('F'),
        'upper_limit': fields.get('G'),
        'locations': extract_locations(raw_text),
        'raw': raw_text,
    }


def extract_notams(text: str) -> list:
    """Top-level extraction: clean page artifacts, split into chunks,
    normalize, and parse each into structured JSON."""
    cleaned_text = clean_page_artifacts(text)
    chunks = find_entry_chunks(cleaned_text)
    entries = [normalize_chunk(c) for c in chunks]
    return [parse_entry(e) for e in entries]


def main():
    parser = argparse.ArgumentParser(
        description='Extract NOTAM entries from a NOTAM summary report and '
                     'output them as structured JSON.'
    )
    parser.add_argument('input_file', help='Path to the input markdown/text file')
    parser.add_argument(
        '-o', '--output',
        help='Path to write JSON output (default: stdout)',
        default=None,
    )
    parser.add_argument(
        '--indent', type=int, default=2,
        help='JSON indent level (default: 2)',
    )
    args = parser.parse_args()

    try:
        with open(args.input_file, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError as e:
        print(f"Error reading {args.input_file}: {e}", file=sys.stderr)
        sys.exit(1)

    notams = extract_notams(text)

    output = {
        'source_file': args.input_file,
        'count': len(notams),
        'notams': notams,
    }

    json_str = json.dumps(output, indent=args.indent, ensure_ascii=False)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(json_str)
        print(f"Extracted {len(notams)} NOTAM entries -> {args.output}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == '__main__':
    main()