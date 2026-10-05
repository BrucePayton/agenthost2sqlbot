"""Per-user UI preferences (panel size, launcher position) kept as one JSON document."""

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.schemas import PreferencesIn
from app.db import Database
from app.db.models import UserPreferenceRecord

# Fractions finer than this are invisible on any screen and only churn the row.
_POSITION_DECIMALS = 4


def _normalize(patch: PreferencesIn) -> dict:
    """Turn the validated patch into the JSON values that get stored."""
    values: dict = {}
    for key in patch.model_fields_set:
        value = getattr(patch, key)
        if key == "launcher" and value is not None:
            value = {
                "x": round(value.x, _POSITION_DECIMALS),
                "y": round(value.y, _POSITION_DECIMALS),
            }
        values[key] = value
    return values


async def get_preferences(database: Database, user_id: str) -> dict:
    """Return the saved choices; a user who never saved anything has none."""
    async with database.session() as db:
        record = await db.get(UserPreferenceRecord, user_id)
    return json.loads(record.preferences_json) if record is not None else {}


async def merge_preferences(
    database: Database, user_id: str, patch: PreferencesIn
) -> dict:
    """Apply only the provided keys over the saved document and return the result."""
    async with database.session() as db:
        record = (
            await db.execute(
                select(UserPreferenceRecord)
                .where(UserPreferenceRecord.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        merged = json.loads(record.preferences_json) if record is not None else {}
        for key, value in _normalize(patch).items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        if record is None:
            record = UserPreferenceRecord(user_id=user_id)
            db.add(record)
        record.preferences_json = json.dumps(merged, ensure_ascii=False)
        record.updated_at = datetime.now(UTC)
        await db.commit()
    return merged
