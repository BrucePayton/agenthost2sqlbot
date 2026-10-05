from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Lock
from typing import Any

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_ITEMS = 32
MAX_SNAPSHOT_TOTAL_BYTES = 64 * 1024 * 1024
MAX_WIDGET_RESULT_BYTES = 48 * 1024


class SnapshotArtifactNotFound(ValueError):
    """Raised for missing, expired, or page-mismatched snapshot refs."""


@dataclass(frozen=True)
class _SnapshotArtifact:
    owner_key: str
    page_instance_id: str
    resource_id: str
    artifact: dict[str, Any]
    size_bytes: int
    expires_at: datetime


class SnapshotArtifactStore:
    """Short-lived in-memory snapshot store for the local Davinci MVP."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 30 * 60,
        max_items: int = MAX_SNAPSHOT_ITEMS,
        max_total_bytes: int = MAX_SNAPSHOT_TOTAL_BYTES,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_items < 1 or max_total_bytes < 1:
            raise ValueError("snapshot quota must be positive")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_items = max_items
        self._max_total_bytes = max_total_bytes
        self._now = now
        self._items: dict[str, _SnapshotArtifact] = {}
        self._total_bytes = 0
        self._lock = Lock()

    @property
    def item_count(self) -> int:
        with self._lock:
            return len(self._items)

    def create(
        self,
        *,
        owner_key: str,
        page_instance_id: str,
        resource_id: str,
        artifact: dict[str, Any],
    ) -> str:
        if (
            not owner_key
            or len(owner_key) > 200
            or not page_instance_id
            or len(page_instance_id) > 200
            or not resource_id
            or len(resource_id) > 200
        ):
            raise ValueError("invalid snapshot binding")
        canonical = json.dumps(
            artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        size_bytes = len(canonical.encode("utf-8"))
        if size_bytes > MAX_SNAPSHOT_BYTES or size_bytes > self._max_total_bytes:
            raise ValueError("snapshot exceeds 8 MiB")
        now = self._now()
        with self._lock:
            self._purge(now)
            while self._items and (
                len(self._items) >= self._max_items
                or self._total_bytes + size_bytes > self._max_total_bytes
            ):
                oldest_ref = next(iter(self._items))
                self._remove(oldest_ref)
            snapshot_ref = f"snap_{secrets.token_urlsafe(24)}"
            self._items[snapshot_ref] = _SnapshotArtifact(
                owner_key=owner_key,
                page_instance_id=page_instance_id,
                resource_id=resource_id,
                artifact=json.loads(canonical),
                size_bytes=size_bytes,
                expires_at=now + self._ttl,
            )
            self._total_bytes += size_bytes
        return snapshot_ref

    def read(
        self,
        snapshot_ref: str,
        *,
        owner_key: str,
        page_instance_id: str,
        resource_id: str,
    ) -> dict[str, Any]:
        now = self._now()
        with self._lock:
            self._purge(now)
            item = self._items.get(snapshot_ref)
            if (
                item is None
                or item.owner_key != owner_key
                or item.page_instance_id != page_instance_id
                or item.resource_id != resource_id
            ):
                raise SnapshotArtifactNotFound("snapshot unavailable")
            return json.loads(json.dumps(item.artifact, ensure_ascii=False))

    def _purge(self, now: datetime) -> None:
        for snapshot_ref, item in list(self._items.items()):
            if item.expires_at <= now:
                self._remove(snapshot_ref)

    def _remove(self, snapshot_ref: str) -> None:
        item = self._items.pop(snapshot_ref)
        self._total_bytes -= item.size_bytes


def snapshot_owner_key(user_id: str) -> str:
    return f"u-{sha256(user_id.encode('utf-8')).hexdigest()[:32]}"


def snapshot_owner_from_memory_scope(memory_scope_key: str) -> str:
    owner_key = memory_scope_key.split("/", 1)[0]
    if not owner_key:
        raise ValueError("invalid memory scope")
    return owner_key


def read_widget_data(
    store: SnapshotArtifactStore,
    *,
    owner_key: str,
    page_instance_id: str,
    resource_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Return a bounded view of a stored widget without using the frontend bridge."""
    snapshot_ref = arguments.get("snapshotRef")
    widget_id = arguments.get("widgetId")
    offset = arguments.get("offset", 0)
    limit = arguments.get("limit", 100)
    fields = arguments.get("fields")
    if (
        not isinstance(snapshot_ref, str)
        or not isinstance(widget_id, str)
        or not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or offset > 10000
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 100
        or (fields is not None and (not isinstance(fields, list) or len(fields) > 50))
    ):
        raise ValueError("invalid widget data request")
    artifact = store.read(
        snapshot_ref,
        owner_key=owner_key,
        page_instance_id=page_instance_id,
        resource_id=resource_id,
    )
    widget = next(
        (
            item
            for item in artifact.get("widgets", [])
            if isinstance(item, dict) and item.get("widgetId") == widget_id
        ),
        None,
    )
    if widget is None:
        raise SnapshotArtifactNotFound("snapshot unavailable")
    available_fields = [
        field for field in widget.get("fields", []) if isinstance(field, str)
    ]
    selected_fields = (
        available_fields
        if fields is None
        else [
            field
            for field in fields
            if isinstance(field, str) and field in available_fields
        ]
    )
    rows = widget.get("rows", [])
    selected_rows: list[list[Any]] = []
    truncated = False
    for row in rows[offset : offset + limit]:
        if isinstance(row, dict):
            values = [row.get(field) for field in selected_fields]
            candidate = [*selected_rows, values]
            if len(
                json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            ) > MAX_WIDGET_RESULT_BYTES:
                truncated = True
                break
            selected_rows.append(values)
    title = str(widget.get("title") or widget_id)[:200]
    result: dict[str, Any] = {
        "summary": (
            f"组件 {title}：返回 {len(selected_rows)} 行，"
            f"字段 {','.join(selected_fields)}"
        )[:2000],
        "widgetId": widget_id,
        "title": title,
        "fields": selected_fields,
        "rows": selected_rows,
        "offset": offset,
        "returnedRows": len(selected_rows),
    }
    if truncated or len(rows[offset : offset + limit]) > len(selected_rows):
        result["truncated"] = True
    context_version = artifact.get("contextVersion")
    if isinstance(context_version, int) and context_version >= 0:
        result["contextVersion"] = context_version
    return result
