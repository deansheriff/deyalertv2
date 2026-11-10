from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import AuthUser, get_auth_user
from app.services.profiles import Profile, load_or_create


async def current_profile(
    user: AuthUser = Depends(get_auth_user),
    session: AsyncSession = Depends(get_session),
) -> Profile:
    return await load_or_create(session, user)
