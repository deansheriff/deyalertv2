from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

POINT_SQL = "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)"

# ST_Covers, not ST_Contains: a point exactly on a boundary still belongs to an area.
WARD_QUERY = text(
    f"SELECT id, lga_id FROM wards WHERE ST_Covers(geom, {POINT_SQL}) ORDER BY id LIMIT 1"
)
LGA_QUERY = text(f"SELECT id FROM lgas WHERE ST_Covers(geom, {POINT_SQL}) ORDER BY id LIMIT 1")


@dataclass(frozen=True)
class Area:
    lga_id: int | None
    ward_id: int | None


async def resolve_area(session: AsyncSession, lat: float, lng: float) -> Area:
    point = {"lat": lat, "lng": lng}
    ward = (await session.execute(WARD_QUERY, point)).first()
    if ward is not None:
        return Area(lga_id=ward.lga_id, ward_id=ward.id)
    lga_id = (await session.execute(LGA_QUERY, point)).scalar()
    return Area(lga_id=lga_id, ward_id=None)
