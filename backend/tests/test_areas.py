import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.areas import resolve_area
from data.import_boundaries import import_features
from tests.conftest import EAST_WARD_WKT, LGA_WKT, WEST_WARD_WKT


@pytest.fixture
async def session(database_url, migrated):
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection)
        lga_id = (
            await session.execute(
                text(
                    "INSERT INTO lgas (state, name, geom) "
                    "VALUES ('Test', 'Test LGA', ST_GeomFromText(:wkt, 4326)) RETURNING id"
                ),
                {"wkt": LGA_WKT},
            )
        ).scalar_one()
        for name, wkt in (("West", WEST_WARD_WKT), ("East", EAST_WARD_WKT)):
            await session.execute(
                text(
                    "INSERT INTO wards (lga_id, name, geom) "
                    "VALUES (:lga, :name, ST_GeomFromText(:wkt, 4326))"
                ),
                {"lga": lga_id, "name": name, "wkt": wkt},
            )
        session.info["lga_id"] = lga_id
        yield session
        await session.close()
        await transaction.rollback()
    await engine.dispose()


async def ward_id(session, name):
    return (
        await session.execute(text("SELECT id FROM wards WHERE name = :n"), {"n": name})
    ).scalar_one()


async def test_point_resolves_to_its_ward_and_lga(session):
    area = await resolve_area(session, lat=6.55, lng=3.32)
    assert area.ward_id == await ward_id(session, "West")
    assert area.lga_id == session.info["lga_id"]

    area = await resolve_area(session, lat=6.55, lng=3.38)
    assert area.ward_id == await ward_id(session, "East")


async def test_point_on_shared_border_resolves_to_one_ward(session):
    area = await resolve_area(session, lat=6.55, lng=3.35)
    assert area.ward_id in {await ward_id(session, "West"), await ward_id(session, "East")}


async def test_point_outside_every_area_resolves_to_nothing(session):
    area = await resolve_area(session, lat=9.0, lng=7.0)
    assert area.lga_id is None
    assert area.ward_id is None


def square(west: float, south: float, size: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [west + size, south],
                [west + size, south + size],
                [west, south + size],
                [west, south],
            ]
        ],
    }


def feature(name: str, geometry: dict | None) -> dict:
    return {"type": "Feature", "properties": {"n": name}, "geometry": geometry}


async def test_importer_places_wards_by_geometry_and_reports_misses(conn):
    lgas = [feature("Alpha", square(4.0, 7.0, 0.1))]
    written, skipped = await import_features(conn, "lga", "Importland", lgas, "n")
    assert (written, skipped) == (1, [])

    wards = [
        feature("Inside", square(4.01, 7.01, 0.02)),
        feature("Outside", square(9.0, 9.0, 0.02)),
        feature("No shape", None),
    ]
    written, skipped = await import_features(conn, "ward", "Importland", wards, "n")
    assert written == 1
    assert skipped == ["Outside", "No shape"]

    row = await conn.fetchrow(
        "SELECT l.name AS lga, GeometryType(w.geom) AS kind FROM wards w "
        "JOIN lgas l ON l.id = w.lga_id WHERE w.name = 'Inside'"
    )
    assert row["lga"] == "Alpha"
    assert row["kind"] == "MULTIPOLYGON"


async def test_importer_is_repeatable(conn):
    lgas = [feature("Beta", square(5.0, 7.0, 0.1))]
    for _ in range(2):
        await import_features(conn, "lga", "Importland", lgas, "n")
    assert await conn.fetchval("SELECT count(*) FROM lgas WHERE name = 'Beta'") == 1
