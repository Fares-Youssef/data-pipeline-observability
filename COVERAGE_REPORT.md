# Geographic coverage report

Report date: 2026-10-30 (Africa/Cairo)

## Live execution status

| Check | Result |
|---|---|
| MariaDB connectivity | Connected |
| Database | `depi` |
| Active geography source | Overture Maps Divisions `2026-10-21.0` |
| Egypt polygons imported | 8,994 |
| Verified source parent links | 8,993 |
| Repeated-import result | `inserted=0`, `changed=0` |
| API health | HTTP 200 / `status=ok` |

## Egypt polygon coverage

| Overture subtype | Polygons |
|---|---:|
| country | 1 |
| region | 28 |
| county | 365 |
| locality | 493 |
| macrohood | 32 |
| neighborhood | 7,984 |
| microhood | 91 |
| **Total** | **8,994** |

These are local database counts from land polygons, not advertised upstream estimates.

## Required neighborhood verification

| Target | Stored hierarchy | Result |
|---|---|---|
| أرض اللواء | Egypt → Aj Jiza → Kardasa → Ard Al Lwaa' / ارض اللواء | verified polygon |
| المهندسين | Egypt → Aj Jiza → Al-Aguza → Al Mohandessin / المهندسين | verified polygon |

Both results came from stored Overture polygons and source hierarchy identifiers. No coordinate-to-name test mapping is used.

## Known limitations

- Overture coverage is substantially deeper than geoBoundaries in Egypt but still depends on published OpenStreetMap/Overture coverage.
- A missing polygon remains null; point-only Places records are never treated as exact boundaries.
- Some names have Arabic and English variants while others provide only one language.
- Administrative and cultural neighborhood polygons may overlap. Reverse lookup follows verified Overture hierarchy and prefers the most specific matching polygon.
- Global import is explicit because the complete divisions download is several gigabytes: `python scripts/import_geography.py --all`.

