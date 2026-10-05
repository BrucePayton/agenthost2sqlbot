"""Explicit local test provider; no external ACL claims or production fallback."""
from __future__ import annotations

import json
import re

from app.config import Settings
from app.data_mcp.providers import DatasetRuntime, _stable_numeric_id
from app.data_mcp.schemas import AuthorizedDataset, DatasetField
from app.errors import AppError


class StarRocksPocProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.app_env not in {"development", "test"} or settings.identity_mode != "mock":
            raise ValueError("starrocks_poc requires development/test and mock identity")
        self.sources: dict[str, dict] = {}
        if settings.data_agent_starrocks_manifest:
            value = json.loads(settings.data_agent_starrocks_manifest.read_text())
            for source in value["datasources"]:
                database = source["dataBase"]
                if database not in {"hive.dw", "hive.dm", "hive.rpt"}:
                    raise ValueError("Unexpected StarRocks POC database")
                ref = f"starrocks:{database}"
                if ref in self.sources or not source["tables"]:
                    raise ValueError("Duplicate or empty dataset")
                names = set()
                for table in source["tables"]:
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table["name"]) or table["name"] in names:
                        raise ValueError("Invalid or duplicate table name")
                    names.add(table["name"])
                    fields = table["fields"]
                    if not fields or len({f["name"] for f in fields}) != len(fields):
                        raise ValueError("Missing or duplicate fields")
                    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", f["name"]) or not f["type"] for f in fields):
                        raise ValueError("Invalid field metadata")
                # Keep only metadata: a file cannot override credentials or add SQL/rules.
                self.sources[ref] = {
                    "name": source["name"], "description": source["description"], "dataBase": database,
                    "tables": [{"name": t["name"], "comment": t.get("comment") or "",
                                "fields": [{"name": f["name"], "type": f["type"], "comment": f.get("comment") or ""}
                                           for f in t["fields"]]} for t in source["tables"]],
                }

    @property
    def configured(self) -> bool:
        return len(self.sources) == 3

    @property
    def runtime_configured(self) -> bool:
        return bool(self.configured and self.settings.starrocks_host and self.settings.starrocks_user and self.settings.starrocks_password)

    def require_subject(self, subject: str) -> None:
        if subject != self.settings.data_agent_subject:
            raise AppError("data_agent_forbidden", "Data-agent access denied.", 403)

    def resolve_authorized(self, subject: str, ref: str, asset=None) -> DatasetRuntime:
        self.require_subject(subject)
        if not self.configured or ref not in self.sources:
            raise AppError("dataset_forbidden", "Dataset is unavailable.", 403)
        source = self.sources[ref]
        fields = tuple(DatasetField(name=f"{t['name']}.{f['name']}", type=f['type'], comment=f['comment'])
                       for t in source['tables'] for f in t['fields'])
        return DatasetRuntime(ref=ref, name=source['name'], description=source['description'],
                              virtual_table_name=source['dataBase'], datasource_id=_stable_numeric_id(ref),
                              fields=fields, base_sql="")

    def list_authorized(self, subject: str) -> list[AuthorizedDataset]:
        self.require_subject(subject)
        return [AuthorizedDataset(ref=ref, name=s['name'], description=s['description'], source='starrocks-poc',
                                  fields=list(self.resolve_authorized(subject, ref).fields))
                for ref, s in self.sources.items()]

    def callback_payload(self, subject: str, refs: list[str]) -> list[dict]:
        if not self.runtime_configured:
            raise AppError("starrocks_not_configured", "StarRocks test connection is not configured.", 503)
        result = []
        for ref in refs:
            self.resolve_authorized(subject, ref)
            source = self.sources[ref]
            result.append({
                **source, "id": _stable_numeric_id(ref), "type": "starrocks",
                "host": self.settings.starrocks_host, "port": self.settings.starrocks_port,
                "user": self.settings.starrocks_user, "password": self.settings.starrocks_password.get_secret_value(),
                "schema": "", "extraParams": "",
            })
        return result
