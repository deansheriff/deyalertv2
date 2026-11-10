"""Import LGA or ward polygons for one state from a GeoJSON file.

Usage:
    python -m data.import_boundaries lga --state Lagos --file lgas.geojson --name-field lganame
    python -m data.import_boundaries ward --state Lagos --file wards.geojson --name-field wardname

Import LGAs first. Each ward is assigned to the LGA that covers a point inside it, so
differences in LGA spelling between datasets do not matter.
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import asyncpg

from app.core.config import get_settings

GEOM_SQL = (
    "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON($1), 4326)), 3))"
)

UPSERT_LGA = f"""
    INSERT INTO lgas (state, name, inec_code, geom)
    VALUES ($2, $3, $4, {GEOM_SQL})
    ON CONFLICT (state, name) DO UPDATE
        SET geom = EXCLUDED.geom, inec_code = COALESCE(EXCLUDED.inec_code, lgas.inec_code)
"""

UPSERT_WARD = f"""
    WITH shape AS (SELECT {GEOM_SQL} AS geom)
    INSERT INTO wards (lga_id, name, geom)
    SELECT l.id, $3, s.geom
    FROM shape s
    JOIN lgas l ON l.state = $2 AND ST_Covers(l.geom, ST_PointOnSurface(s.geom))
    ORDER BY l.id
    LIMIT 1
    ON CONFLICT (lga_id, name) DO UPDATE SET geom = EXCLUDED.geom
"""


async def import_features(
    conn: asyncpg.Connection,
    level: str,
    state: str,
    features: list[dict[str, Any]],
    name_field: str,
    code_field: str | None = None,
) -> tuple[int, list[str]]:
    """Returns the number of rows written and the names that could not be placed."""
    written = 0
    skipped: list[str] = []
    for feature in features:
        properties = feature.get("properties") or {}
        name = str(properties.get(name_field) or "").strip()
        geometry = feature.get("geometry")
        if not name or geometry is None:
            skipped.append(name or "<unnamed>")
            continue
        shape = json.dumps(geometry)
        if level == "lga":
            code = properties.get(code_field) if code_field else None
            await conn.execute(UPSERT_LGA, shape, state, name, code)
            written += 1
        else:
            result = await conn.execute(UPSERT_WARD, shape, state, name)
            if result.endswith(" 0"):
                skipped.append(name)
            else:
                written += 1
    return written, skipped


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", choices=["lga", "ward"])
    parser.add_argument("--state", required=True)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--name-field", required=True)
    parser.add_argument("--code-field")
    args = parser.parse_args()

    features = json.loads(args.file.read_text(encoding="utf-8"))["features"]
    dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            written, skipped = await import_features(
                conn, args.level, args.state, features, args.name_field, args.code_field
            )
    finally:
        await conn.close()
    print(f"Imported {written} {args.level} rows for {args.state}; skipped {len(skipped)}.")
    for name in skipped:
        print(f"  skipped: {name}")


if __name__ == "__main__":
    asyncio.run(main())
