from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.config import Settings
from app.data_mcp.asset_mcp import AuthorizedAsset
from app.data_mcp.schemas import AuthorizedDataset, DatasetField
from app.errors import AppError

SYNC_JOB_REF = "mysql:knowledge.sync_job"


SYNC_JOB_FIELDS = (
    DatasetField(name="id", type="bigint", comment="同步任务ID", required=True),
    DatasetField(name="source_id", type="bigint", comment="数据源ID", required=True, aliases=["数据源"]),
    DatasetField(name="status", type="varchar", comment="同步状态", required=True, aliases=["状态"]),
    DatasetField(name="total_files", type="int", comment="文件总数", aliases=["总文件数"]),
    DatasetField(name="success_files", type="int", comment="成功文件数", aliases=["成功数"]),
    DatasetField(name="failed_files", type="int", comment="失败文件数", aliases=["失败数"]),
    DatasetField(name="delete_files", type="int", comment="删除文件数", aliases=["删除数"]),
    DatasetField(name="create_dt", type="datetime", comment="创建时间", required=True, aliases=["时间", "日期"]),
    DatasetField(name="update_dt", type="datetime", comment="更新时间", required=True, aliases=["更新时间"]),
)


@dataclass(frozen=True, slots=True)
class DatasetRuntime:
    ref: str
    name: str
    description: str
    virtual_table_name: str
    datasource_id: int
    fields: tuple[DatasetField, ...]
    base_sql: str


class KnowledgeMysqlProvider:
    """Real POC dataset with a fixed server-side table and column allowlist."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        configured_ref = settings.data_agent_runtime_asset_ref
        if configured_ref is None and settings.data_agent_allow_unverified_local_dataset:
            configured_ref = SYNC_JOB_REF
        self.runtime = DatasetRuntime(
            ref=configured_ref or "unconfigured:knowledge.sync_job",
            name="知识库同步任务",
            description="knowledge.sync_job 的受控只读问数视图",
            virtual_table_name="sync_job",
            datasource_id=_stable_numeric_id("knowledge.sync_job"),
            fields=SYNC_JOB_FIELDS,
            base_sql=(
                "SELECT id, source_id, status, total_files, success_files, "
                "failed_files, delete_files, create_dt, update_dt "
                "FROM knowledge.sync_job"
            ),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.data_agent_runtime_asset_ref
            or self.settings.data_agent_allow_unverified_local_dataset
        )

    def list_authorized(
        self, user_subject: str, asset: AuthorizedAsset | None = None
    ) -> list[AuthorizedDataset]:
        self.require_subject(user_subject)
        item = self.resolve_authorized(user_subject, self.runtime.ref, asset)
        return [AuthorizedDataset(
            ref=item.ref,
            name=item.name,
            description=item.description,
            source="java-acl" if asset is not None else "mysql-poc",
            fields=list(item.fields),
        )]

    def resolve(self, user_subject: str, dataset_ref: str) -> DatasetRuntime:
        return self.resolve_authorized(user_subject, dataset_ref, None)

    def resolve_authorized(
        self,
        user_subject: str,
        dataset_ref: str,
        asset: AuthorizedAsset | None,
    ) -> DatasetRuntime:
        self.require_subject(user_subject)
        if dataset_ref != self.runtime.ref:
            raise AppError("dataset_forbidden", "Dataset is unavailable.", 403)
        if asset is None:
            if not self.settings.data_agent_allow_unverified_local_dataset:
                raise AppError(
                    "asset_mapping_not_verified",
                    "The physical dataset has no verified Asset MCP mapping.",
                    503,
                )
            return self.runtime
        if asset.ref != dataset_ref:
            raise AppError(
                "asset_schema_mismatch", "Asset MCP schema identity mismatch.", 502
            )
        if asset.required_filters:
            raise AppError(
                "row_permission_not_supported",
                "This dataset requires row filters that the MySQL POC cannot enforce.",
                409,
            )
        remote_by_name = {field.name.casefold(): field for field in asset.fields}
        local_by_name = {field.name.casefold(): field for field in self.runtime.fields}
        missing = sorted(
            field.name
            for key, field in local_by_name.items()
            if key not in remote_by_name
        )
        if missing:
            raise AppError(
                "asset_physical_schema_mismatch",
                "Asset MCP schema does not match the configured physical table.",
                409,
                details={"missing_physical_fields": missing},
            )
        fields = tuple(
            DatasetField(
                name=local.name,
                type=local.type,
                comment=(remote_by_name[local.name.casefold()].description or local.comment),
                required=(local.required or remote_by_name[local.name.casefold()].required),
                aliases=local.aliases,
            )
            for local in self.runtime.fields
        )
        selected = ", ".join(field.name for field in fields)
        return DatasetRuntime(
            ref=asset.ref,
            name=asset.name,
            description=asset.description or self.runtime.description,
            virtual_table_name=self.runtime.virtual_table_name,
            datasource_id=_stable_numeric_id(asset.ref),
            fields=fields,
            base_sql=f"SELECT {selected} FROM knowledge.sync_job",
        )

    def require_subject(self, user_subject: str) -> None:
        if user_subject != self.settings.data_agent_subject:
            raise AppError("data_agent_forbidden", "Data-agent access denied.", 403)

    def connection_payload(self) -> dict[str, object]:
        password = self.settings.mysql_readonly_password
        if not self.settings.mysql_host or not self.settings.mysql_readonly_user or password is None:
            raise AppError(
                "mysql_runtime_not_configured",
                "The SQLBot MySQL read-only runtime account is not configured.",
                503,
            )
        return {
            "type": "mysql",
            "host": self.settings.mysql_host,
            "port": self.settings.mysql_port,
            "user": self.settings.mysql_readonly_user,
            "password": password.get_secret_value(),
            "dataBase": self.settings.mysql_database,
            "schema": "",
            "extraParams": "",
        }


def project_fields(
    fields: list[dict[str, object]], question: str, limit: int
) -> list[dict[str, object]]:
    """Project only authorized fields; mandatory grain/date fields always survive."""
    tokens = set(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", question.casefold()))

    def score(field: dict[str, object]) -> tuple[int, int, str]:
        text = " ".join(
            [str(field.get("name", "")), str(field.get("comment", "")), *map(str, field.get("aliases", []))]
        ).casefold()
        hits = sum(token in text for token in tokens)
        required = 1 if field.get("required") else 0
        return required, hits, str(field.get("name", ""))

    ranked = sorted(fields, key=score, reverse=True)
    selected = ranked[:limit]
    required_names = {str(item["name"]) for item in fields if item.get("required")}
    selected_names = {str(item["name"]) for item in selected}
    for field in fields:
        if field.get("name") in required_names - selected_names:
            selected.append(field)
    order = {str(field["name"]): index for index, field in enumerate(fields)}
    return sorted(selected, key=lambda item: order[str(item["name"])])[:limit]


def _stable_numeric_id(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:7], "big")
