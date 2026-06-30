# NOTAM List Extractor

Extracts structured **Notice to Air Missions (NOTAM)** entries from raw NOTAM list reports into clean JSON or GeoJSON.

## Scripts

| Script | Source | Input format |
|---|---|---|
| `extract_notams.py` | CAAS (Singapore FIR) | `+`-prefixed entries with `Lower:`/`From:` fields |
| `extract_notam_caam.py` | CAAM (Malaysia FIR) | ICAO field-marked entries `(A… NOTAMN` with `A)`…`G)` sections |

Both scripts share coordinate parsing and GeoJSON serialisation via `notam_common.py`.

## Usage

```bash
python extract_notams.py <input_file> [output_file] [-f json|geojson]
python extract_notam_caam.py <input_file> [output_file] [-f json|geojson]
```

- `-f json` — plain JSON array (default)
- `-f geojson` — GeoJSON FeatureCollection

If no output file is given, output is written to `<input_stem>.json` or `<input_stem>.geojson`.

## Input formats

- **CAAS**: Raw text from the Singapore NOTAM List (`WSSS-NOTAM-List-…`). `+`-prefixed entries with structured `Lower:`, `Upper:`, `From:`, `To:`, `Time schedule:` labels.
- **CAAM**: Markdown/text from the Malaysia NOTAM Summary (`SUMMARY WMFC …`). ICAO-standard `(A…/.. NOTAMN` entries with `A)`…`G)` field markers.

## Output fields (normalised)

| Field | Description |
|---|---|
| `id` | NOTAM identifier, e.g. `A1737/26` |
| `type` | Single-letter code (`N`, `R`) or `NOTAMN`/`NOTAMR` |
| `message` | Body text with normalised whitespace |
| `lower` | Lower altitude limit, or `null` |
| `upper` | Upper altitude limit, or `null` |
| `from` | Effective-from datetime, or `null` |
| `to` | Effective-to datetime, or `null` |
| `time_schedule` | Time schedule string, or `null` |
| `locations` | List of dicts with `raw`/`latitude`/`longitude` (decimal degrees) |
| `replaces` | Replaced NOTAM id (CAAM only), or `null` |
| `fir_or_icao` | FIR/ICAO identifier (CAAM only), or `null` |
| `raw` | Original unmodified entry text for reference |

## Example

```json
{
  "id": "A1737/26",
  "type": "N",
  "message": "PYROTECHNIC DISPLAY WILL TAKE PLACE SAFETY RADIUS 247FT CENTRED ON 011517N1034922E (WI WSAP CTR).",
  "lower": "SFC",
  "upper": "247FT AMSL",
  "from": "01 Jun 2026 00:00",
  "to": "31 Aug 2026 15:30",
  "time_schedule": "JUN 01-30 0000-1530, JUL 01-31 0000-1530, AUG 01-31 0000-1530",
  "locations": [
    { "raw": "011517N 1034922E", "latitude": 1.254722, "longitude": 103.822778 }
  ]
}
```

## Sample files

| File | Description |
|---|---|
| `WSSS-NOTAM-List-06-26.md` | Raw CAAS NOTAM list (Singapore) |
| `WSSS-NOTAM-List-06-26.json` | CAAS output (JSON) |
| `WSSS-NOTAM-List-06-26.geojson` | CAAS output (GeoJSON) |
| `SUMMARY WMFC A 06 2026.md` | Raw CAAM NOTAM summary (Malaysia) |
| `notam_common.py` | Shared coordinate parsing & GeoJSON module |
