# Provider and coverage notes

Verified on 31 October 2026 with live Cairo requests:

| Model | Explicit Open-Meteo identifier | Endpoint | Returned horizon | Origin |
|---|---|---|---|---|
| NOAA GFS Global | `gfs_global` | `/v1/gfs` | 11 requested dates (D0–D+10) | NOAA/NCEP |
| ECMWF IFS HRES | `ecmwf_ifs` | `/v1/ecmwf` | 11 requested dates | ECMWF |
| ECMWF AIFS 0.25° Single | `ecmwf_aifs025_single` | `/v1/ecmwf` | 11 requested dates | ECMWF |

Open-Meteo is recorded as distributor, never as the originating meteorological organization. The free API is non-commercial, currently documents 10,000 calls/day, has no uptime guarantee, and supports multi-coordinate requests; nationwide activation requires measured API-call weighting and terms review. Customer keys switch to the customer endpoint without changing adapter behavior.

The local `depi` database contains exactly 8,994 Egyptian geographic units by `COUNT(*)`: level 0: 1, level 3: 28, level 5: 365, level 7: 493, level 9: 32, level 10: 7,984, level 11: 91. The Phase 1 importer stored polygon `representative_point()` coordinates. `locations --sync` deduplicates identical coordinates and explicitly maps every eligible unit to a representative forecast point.

Observation coverage is intentionally independent. GHCN-Daily station discovery filters real Egypt stations with TMAX/TMIN inventory records. Imported observations remain temporally ineligible until the source reporting-day convention and station-to-point match are verified; missing actuals remain unavailable rather than being replaced with model/reanalysis values.
