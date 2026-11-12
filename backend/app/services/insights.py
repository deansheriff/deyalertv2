"""Analytics for the panel, CSV exports and the weekly email report."""

import csv
import io
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RANGES = {"7": 7, "30": 30, "90": 90}

# Incidents in range, limited to the viewer's areas and optionally one LGA.
WINDOW = """
    i.created_at > NOW() - make_interval(days => :days)
    AND (CAST(:scope_all AS BOOLEAN) OR i.lga_id = ANY(CAST(:scope_lgas AS INT[]))
         OR i.ward_id = ANY(CAST(:scope_wards AS INT[])))
    AND (CAST(:lga AS INT) IS NULL OR i.lga_id = CAST(:lga AS INT))
    AND i.merged_into IS NULL
"""


async def overview(
    session: AsyncSession, days: int, scope: dict[str, Any], lga: int | None = None
) -> dict[str, Any]:
    params = {"days": days, "lga": lga, **scope}
    previous = {**params, "days": days * 2}
    kpi_sql = f"""
        SELECT count(*) AS reports,
               count(*) FILTER (WHERE confirmed_at IS NOT NULL) AS confirmed,
               count(*) FILTER (WHERE status = 'false_report') AS false_reports,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY EXTRACT(EPOCH FROM confirmed_at - created_at) / 60
               ) FILTER (WHERE confirmed_at IS NOT NULL) AS median_confirm_minutes
        FROM incidents i WHERE {WINDOW}
    """
    current = (await session.execute(text(kpi_sql), params)).one()
    both = (await session.execute(text(kpi_sql), previous)).one()
    users = (
        await session.execute(
            text(
                "SELECT count(DISTINCT user_id) FROM devices "
                "WHERE last_seen_at > NOW() - make_interval(days => :days)"
            ),
            {"days": days},
        )
    ).scalar_one()
    sos_count = (
        await session.execute(
            text(
                "SELECT count(*) FROM sos_alerts "
                "WHERE triggered_at > NOW() - make_interval(days => :days)"
            ),
            {"days": days},
        )
    ).scalar_one()
    per_day = (
        await session.execute(
            text(
                f"""
                SELECT date_trunc('day', i.created_at AT TIME ZONE 'Africa/Lagos')::date AS day,
                       i.severity, count(*) AS total
                FROM incidents i WHERE {WINDOW}
                GROUP BY 1, 2 ORDER BY 1
                """
            ),
            params,
        )
    ).all()
    by_type = (
        await session.execute(
            text(
                f"""
                SELECT t.label, count(*) AS total FROM incidents i
                JOIN incident_types t ON t.code = i.type_code
                WHERE {WINDOW} GROUP BY t.label ORDER BY total DESC
                """
            ),
            params,
        )
    ).all()
    by_lga = (
        await session.execute(
            text(
                f"""
                SELECT COALESCE(l.name, 'Unknown area') AS name, count(*) AS reports,
                       count(*) FILTER (WHERE i.confirmed_at IS NOT NULL) AS confirmed,
                       count(*) FILTER (WHERE i.status = 'false_report') AS false_reports,
                       round(CAST(percentile_cont(0.5) WITHIN GROUP (
                           ORDER BY EXTRACT(EPOCH FROM i.confirmed_at - i.created_at) / 60
                       ) FILTER (WHERE i.confirmed_at IS NOT NULL) AS numeric), 0) AS median_minutes
                FROM incidents i LEFT JOIN lgas l ON l.id = i.lga_id
                WHERE {WINDOW} GROUP BY 1 ORDER BY reports DESC LIMIT 25
                """
            ),
            params,
        )
    ).all()
    outcomes = (
        await session.execute(
            text(f"SELECT i.status, count(*) AS total FROM incidents i WHERE {WINDOW} GROUP BY 1"),
            params,
        )
    ).all()
    delivery = (
        await session.execute(
            text(
                "SELECT channel, count(*) AS total FROM notification_log "
                "WHERE sent_at > NOW() - make_interval(days => :days) GROUP BY channel"
            ),
            {"days": days},
        )
    ).all()
    broadcasts = (
        await session.execute(
            text(
                "SELECT coalesce(sum(push_sent), 0) AS push_sent, "
                "coalesce(sum(push_failed), 0) AS push_failed, "
                "coalesce(sum(sms_sent), 0) AS sms_sent FROM broadcasts "
                "WHERE sent_at > NOW() - make_interval(days => :days)"
            ),
            {"days": days},
        )
    ).one()

    def delta(now: int, earlier_total: int) -> int | None:
        earlier = earlier_total - now
        return None if earlier <= 0 else round((now - earlier) * 100 / earlier)

    rate = round(current.false_reports * 100 / current.reports, 1) if current.reports else 0.0
    return {
        "kpis": {
            "reports": current.reports,
            "reports_delta": delta(current.reports, both.reports),
            "confirmed": current.confirmed,
            "confirmed_delta": delta(current.confirmed, both.confirmed),
            "false_rate": rate,
            "median_confirm": round(current.median_confirm_minutes or 0),
            "active_users": users,
            "sos": sos_count,
        },
        "per_day": per_day,
        "by_type": by_type,
        "by_lga": by_lga,
        "outcomes": outcomes,
        "delivery": {row.channel: row.total for row in delivery},
        "broadcasts": broadcasts,
    }


def csv_report(data: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Metric", "Value"])
    for key, value in data["kpis"].items():
        writer.writerow([key, value])
    writer.writerow([])
    writer.writerow(["LGA", "Reports", "Confirmed", "False reports", "Median minutes to confirm"])
    for row in data["by_lga"]:
        writer.writerow(
            [row.name, row.reports, row.confirmed, row.false_reports, row.median_minutes]
        )
    writer.writerow([])
    writer.writerow(["Type", "Reports"])
    for row in data["by_type"]:
        writer.writerow([row.label, row.total])
    return out.getvalue()


def weekly_text(data: dict[str, Any]) -> str:
    k = data["kpis"]
    lines = [
        "Dey Alert weekly report (last 7 days)",
        "",
        f"Reports: {k['reports']}",
        f"Confirmed: {k['confirmed']}",
        f"False-report rate: {k['false_rate']}%",
        f"Median time to confirm: {k['median_confirm']} min",
        f"Active users: {k['active_users']}",
        f"SOS alerts: {k['sos']}",
        "",
        "Busiest areas:",
    ]
    lines += [
        f"- {row.name}: {row.reports} reports, {row.confirmed} confirmed"
        for row in data["by_lga"][:5]
    ]
    lines += ["", "The full figures are attached as CSV."]
    return "\n".join(lines)
