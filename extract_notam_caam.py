#!/usr/bin/env python3
"""
NOTAM Extractor (CAAM — Malaysia)
===================================
Extracts individual NOTAM entries from a NOTAM summary report (exported as
markdown/text, e.g. from a PDF-to-markdown conversion) and outputs them as
structured JSON or GeoJSON.

Handles common artifacts found in paginated NOTAM summary exports:
  - "--- Page N ---" separators
  - "Page N of M" footers
  - Entries missing their opening "(" (OCR/export artifact)
  - Entries with no closing ")" at all (incomplete in source)
  - Trailing bare ICAO airport-code header lines (e.g. "WMKD") that bleed
    into an entry because the entry itself has no closing paren

Usage:
    python3 extract_notam_caam.py INPUT_FILE [-o OUTPUT_FILE] [-f json|geojson]

Example:
    python3 extract_notam_caam.py SUMMARY_WMFC_A_06_2026.md -o notams.json
    python3 extract_notam_caam.py SUMMARY_WMFC_A_06_2026.md -f geojson
"""

import argparse
import json
import re
import sys
from pathlib import Path

from notam_common import parse_coords, normalise_ws
from notam_common import notam_to_geojson_feature, to_geojson


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

    fields = {}
    current_field = None
    buffer = []

    def flush():
        if current_field is not None:
            fields[current_field] = '\n'.join(buffer).strip()

    for idx, line in enumerate(lines):
        l = line.strip()
        if idx == 0:
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

    a_raw = fields.get('A', '')
    icao_match = re.match(r'^(\S+)', a_raw)
    fid = icao_match.group(1) if icao_match else None

    inline_bc = re.search(r'B\)\s*(\S+)(?:\s+C\)\s*(\S+))?', a_raw)
    if inline_bc:
        if 'B' not in fields or not fields['B']:
            fields['B'] = inline_bc.group(1)
        if inline_bc.group(2) and ('C' not in fields or not fields['C']):
            fields['C'] = inline_bc.group(2)

    fields['A'] = fid

    last_field_letter = None
    if fields:
        last_field_letter = sorted(fields.keys())[-1]
    if last_field_letter and fields[last_field_letter].endswith(')'):
        fields[last_field_letter] = fields[last_field_letter][:-1].strip()

    if 'F' in fields and 'G' not in fields:
        f_val = fields['F']
        g_split = re.search(r'^(.*?)\s*G\)\s*(.*)$', f_val)
        if g_split:
            fields['F'] = g_split.group(1).strip()
            fields['G'] = g_split.group(2).strip()
    elif 'F' in fields and 'G' in fields:
        f_val = fields['F']
        g_split = re.search(r'^(.*?)\s*G\)\s*(.*)$', f_val)
        if g_split:
            fields['F'] = g_split.group(1).strip()
            if not fields.get('G'):
                fields['G'] = g_split.group(2).strip()

    return {
        'id':            notam_id,
        'type':          notam_type,
        'replaces':      replaces,
        'fir_or_icao':   fid,
        'from':          fields.get('B'),
        'to':            fields.get('C'),
        'time_schedule': fields.get('D'),
        'message':       fields.get('E'),
        'lower':         fields.get('F'),
        'upper':         fields.get('G'),
        'locations':     parse_coords(raw_text),
        'raw':           raw_text,
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
        description='Extract NOTAM entries from a NOTAM summary report.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('input_file', help='Path to the input markdown/text file')
    parser.add_argument('output', nargs='?', help='Output file path (optional)')
    parser.add_argument(
        '-f', '--format',
        choices=['json', 'geojson'],
        default='json',
        help="Output format: 'json' (default) or 'geojson'",
    )
    parser.add_argument(
        '--indent', type=int, default=2,
        help='JSON indent level (default: 2)',
    )
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: file not found – {args.input_file}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        suffix = '.geojson' if args.format == 'geojson' else '.json'
        output_path = input_path.with_suffix(suffix)

    try:
        text = input_path.read_text(encoding='utf-8')
    except OSError as e:
        print(f"Error reading {args.input_file}: {e}", file=sys.stderr)
        sys.exit(1)

    notams = extract_notams(text)

    if args.format == 'geojson':
        output_data = to_geojson(notams)
    else:
        output_data = notams

    output_path.write_text(
        json.dumps(output_data, indent=args.indent, ensure_ascii=False),
        encoding='utf-8',
    )

    loc_count = sum(1 for n in notams if n['locations'])
    print(f"Done. {len(notams)} NOTAM entries written to {output_path}")
    print(f"      {loc_count} entries have coordinates, "
          f"{len(notams) - loc_count} have no location (null geometry in GeoJSON).")


if __name__ == '__main__':
    main()
