from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _unique_dataset_refs(value: list[str]) -> list[str]:
    normalized = [item.strip() for item in value]
    if any(not item for item in normalized):
        raise ValueError("dataset references cannot be blank")
    if len(normalized) != len(set(normalized)):
        raise ValueError("dataset references must be unique")
    return normalized


class DatasetField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    comment: str = ""
    required: bool = False
    aliases: list[str] = Field(default_factory=list)


class AuthorizedDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    name: str
    description: str
    source: Literal["mysql-poc", "java-acl", "starrocks-poc"]
    fields: list[DatasetField]


class DataAgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    dataset_refs: list[str] = Field(min_length=1, max_length=5)

    @field_validator("dataset_refs")
    @classmethod
    def unique_dataset_refs(cls, value: list[str]) -> list[str]:
        return _unique_dataset_refs(value)


class DataAgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    dataset_refs: list[str] = Field(min_length=1, max_length=5)

    @field_validator("dataset_refs")
    @classmethod
    def unique_dataset_refs(cls, value: list[str]) -> list[str]:
        return _unique_dataset_refs(value)


class DataAgentDatasetOut(BaseModel):
    ref: str
    name: str
    description: str
    virtual_table_name: str
    field_count: int


class DataAgentOut(BaseModel):
    id: str
    name: str
    description: str
    status: Literal["draft", "published", "disabled"]
    sqlbot_assistant_id: int | None
    sqlbot_sync_status: str
    sqlbot_sync_error: str | None
    datasets: list[DataAgentDatasetOut]
    created_at: datetime
    updated_at: datetime


class DataAgentOpenOut(BaseModel):
    agent_id: str
    session_id: str
    workspace_id: str = "data-question"
    workbench_url: str


class DataAskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str | None = Field(default=None, alias="agentId")
    question: str = Field(min_length=1, max_length=4000)
    session_key: str | None = Field(default=None, alias="sessionKey")


class DataAskResult(BaseModel):
    result_id: str = Field(alias="resultId")
    agent_id: str = Field(alias="agentId")
    chat_id: int = Field(alias="chatId")
    record_id: int = Field(alias="recordId")
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int = Field(alias="rowCount")
    truncated: bool
    chart_hint: dict[str, Any] = Field(alias="chartHint")
    evidence: dict[str, Any]
    fields_used: list[str] = Field(alias="fieldsUsed")
    presentation: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


class DataAgentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=1, max_length=100)


class DataAgentHealth(BaseModel):
    provider: str = "knowledge_mysql"
    runtime_configured: bool = False
    custom_model: str = ""
    enabled: bool
    sqlbot_url: str
    sqlbot_admin_configured: bool
    sqlbot_secret_configured: bool
    mysql_runtime_configured: bool
    asset_mcp_url: str
    asset_mapping_configured: bool
    callback_source_restricted: bool
    callback_url: str
