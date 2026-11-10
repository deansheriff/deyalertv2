from io import BytesIO
from uuid import UUID

import httpx
from PIL import Image, ImageOps
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings

MAX_SIDE = 1280
THUMB_SIDE = 320
JPEG_QUALITY = 70


def render(data: bytes, max_side: int) -> tuple[bytes, int, int]:
    """Re-encodes an image, which also drops EXIF and other metadata."""
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((max_side, max_side))
    output = BytesIO()
    image.save(output, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return output.getvalue(), image.width, image.height


async def process(
    session: AsyncSession, settings: Settings, client: httpx.AsyncClient, media_id: UUID
) -> None:
    row = (
        await session.execute(
            text(
                "SELECT id, incident_id, kind, storage_path FROM incident_media "
                "WHERE id = :id AND moderation_status = 'pending'"
            ),
            {"id": media_id},
        )
    ).first()
    if row is None or row.kind != "image":
        return

    headers = {"Authorization": f"Bearer {settings.supabase_service_role_key}"}
    base = f"{settings.supabase_url}/storage/v1/object"
    download = await client.get(
        f"{base}/{settings.media_quarantine_bucket}/{row.storage_path}", headers=headers, timeout=60
    )
    download.raise_for_status()

    full, width, height = render(download.content, MAX_SIDE)
    thumb, _, _ = render(download.content, THUMB_SIDE)
    full_path = f"{row.incident_id}/{row.id}.jpg"
    thumb_path = f"{row.incident_id}/{row.id}_thumb.jpg"
    for path, body in ((full_path, full), (thumb_path, thumb)):
        upload = await client.post(
            f"{base}/{settings.media_public_bucket}/{path}",
            headers={**headers, "Content-Type": "image/jpeg", "x-upsert": "true"},
            content=body,
            timeout=60,
        )
        upload.raise_for_status()

    # Blurred until a moderator approves it, because nothing here can judge graphic content.
    await session.execute(
        text(
            "UPDATE incident_media SET storage_path = :full, thumb_path = :thumb, width = :w, "
            "height = :h, size_bytes = :size, moderation_status = 'blurred' WHERE id = :id"
        ),
        {
            "full": full_path,
            "thumb": thumb_path,
            "w": width,
            "h": height,
            "size": len(full),
            "id": row.id,
        },
    )
    await session.commit()
    await client.delete(
        f"{base}/{settings.media_quarantine_bucket}/{row.storage_path}", headers=headers, timeout=30
    )
