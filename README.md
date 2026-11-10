# Dey Alert

Community security incident reporting and alerts for Nigerian communities.
Flutter app, FastAPI backend, Supabase (Postgres + PostGIS, Auth, Realtime, Storage).

The one architecture rule: **clients read, the server writes.** Clients only reach
`incidents_public` and the other `*_public` views; the base tables hold true coordinates.

## Layout

| Path | What |
|------|------|
| `lib/` | Flutter app |
| `backend/app/` | FastAPI API and arq worker |
| `backend/migrations/` | Alembic: schema, RLS, views, functions, seeds (the only migration tool) |
| `backend/data/` | LGA and ward boundary importer |
| `backend/dev/supabase_stub.sql` | Supabase roles, `auth` schema, and default grants for plain PostGIS |
| `design/stitch/` | Screen manifest, prompts, and design tokens |

## Backend

Requires Docker with Compose and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
docker compose up --build
```

The API runs at http://127.0.0.1:8010 (`/health`, `/ready`, `/docs`). Migrations run on start.
Published ports are bound to localhost and chosen to avoid Coolify's.

Run the tests from `backend/`. `TEST_DATABASE_URL` is optional and its database name must
end in `_test`, because the tests wipe it. Without it, testcontainers starts PostGIS.

```bash
uv run ruff check .
```

```bash
uv run pytest -v
```

### Import boundaries

Import LGAs first, then wards. Check each dataset's licence and currency before using it.

```bash
uv run python -m data.import_boundaries lga --state Lagos --file lgas.geojson --name-field lganame
```

```bash
uv run python -m data.import_boundaries ward --state Lagos --file wards.geojson --name-field wardname
```

## Flutter

```bash
flutter pub get
```

```bash
flutter test
```

## Design gate

Every screen is generated in Stitch and marked `approved` in `design/stitch/manifest.json`
before any UI code is written. UI changes start in Stitch.
