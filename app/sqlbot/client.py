from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import httpx
import jwt

from app.config import Settings
from app.errors import AppError


class SQLBotClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=str(settings.sqlbot_base_url).rstrip("/"),
            timeout=httpx.Timeout(settings.sqlbot_request_timeout_seconds),
            trust_env=False,
            headers={"Accept-Language": "zh-CN"},
        )
        self._management_token: str | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def datasource_list(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/v1/datasource/list", token=await self._admin_token())
        if not isinstance(data, list):
            raise AppError("sqlbot_catalog_invalid", "SQLBot datasource list is invalid.", 502)
        return data

    async def datasource_connected(self, source_id: int) -> bool:
        data = await self._request("GET", f"/api/v1/datasource/check/{source_id}", token=await self._admin_token())
        if not isinstance(data, bool):
            raise AppError("sqlbot_catalog_invalid", "SQLBot connection status is invalid.", 502)
        return data

    async def datasource_tables(self, source_id: int) -> list[dict[str, Any]]:
        data = await self._request("POST", f"/api/v1/datasource/tableList/{source_id}", token=await self._admin_token())
        if not isinstance(data, list):
            raise AppError("sqlbot_catalog_invalid", "SQLBot table list is invalid.", 502)
        return data

    async def datasource_fields(self, source_id: int, table_id: int) -> list[dict[str, Any]]:
        data = await self._request("POST", f"/api/v1/datasource/fieldList/{source_id}/{table_id}",
                                   token=await self._admin_token(), json={"fieldName": None})
        if not isinstance(data, list):
            raise AppError("sqlbot_catalog_invalid", "SQLBot field list is invalid.", 502)
        return data

    async def datasource_live_fields(self, source_id: int, table_name: str) -> list[dict[str, Any]]:
        data = await self._request("POST", f"/api/v1/datasource/getFields/{source_id}/{table_name}",
                                   token=await self._admin_token())
        if not isinstance(data, list):
            raise AppError("sqlbot_catalog_invalid", "SQLBot live schema is invalid.", 502)
        return data

    async def ensure_assistant(
        self,
        *,
        assistant_id: int | None,
        agent_id: str,
        name: str,
        description: str,
    ) -> int:
        token = await self._admin_token()
        callback_url = f"{str(self.settings.data_agent_public_base_url).rstrip('/')}/api/sqlbot/datasources"
        configuration = json.dumps({
            "endpoint": callback_url,
            "timeout": 30,
            "encrypt": False,
            "aes_key": "",
            "aes_iv": "",
            "auto_ds": False,
            "certificate": [{
                "id": f"data-agent-{agent_id}",
                "type": "custom",
                "source": "data_ticket",
                "target": "header",
                "target_key": "X-Davinci-Ticket",
                "target_val": "",
            }],
        }, ensure_ascii=False, separators=(",", ":"))
        payload: dict[str, Any] = {
            "name": f"[DataAgent] {name}"[:50],
            "description": description[:200],
            "domain": str(self.settings.data_agent_browser_origin or self.settings.data_agent_public_base_url).rstrip("/"),
            "type": 1,
            "configuration": configuration,
            "oid": 1,
            "enable_custom_model": bool(self.settings.sqlbot_custom_model),
            "custom_model": self.settings.sqlbot_custom_model,
        }
        if assistant_id is not None:
            payload["id"] = assistant_id
            await self._request("PUT", "/api/v1/system/assistant", token=token, json=payload)
            return assistant_id
        data = await self._request("POST", "/api/v1/system/assistant", token=token, json=payload)
        candidate = data.get("id") if isinstance(data, dict) else None
        if candidate is not None:
            return int(candidate)
        assistants = await self._request("GET", "/api/v1/system/assistant", token=token)
        if isinstance(assistants, list):
            matching = [item for item in assistants if item.get("name") == payload["name"] and int(item.get("type", -1)) == 1]
            if matching:
                return int(matching[-1]["id"])
        raise AppError("sqlbot_assistant_sync_failed", "SQLBot did not return the Assistant id.", 502)

    def assistant_token(self, *, assistant_id: int, virtual_user_id: int) -> str:
        secret = self.settings.sqlbot_secret_key
        if secret is None:
            raise AppError(
                "sqlbot_secret_not_configured",
                "SQLBOT_SECRET_KEY is required for server-side Advanced Assistant access.",
                503,
            )
        payload = {
            "id": virtual_user_id,
            "account": "sqlbot-inner-assistant",
            "oid": 1,
            "assistant_id": assistant_id,
            "exp": datetime.now(UTC) + timedelta(minutes=15),
        }
        return jwt.encode(payload, secret.get_secret_value(), algorithm="HS256")

    async def start_chat(
        self,
        *,
        assistant_token: str,
        ticket: str,
        datasource_id: int | None,
        question: str,
    ) -> int:
        payload: dict[str, Any] = {"question": question, "origin": 2}
        if datasource_id is not None:
            payload["datasource"] = datasource_id
        data = await self._assistant_request(
            "POST", "/api/v1/chat/assistant/start", assistant_token, ticket,
            json=payload,
        )
        if not isinstance(data, dict) or data.get("id") is None:
            raise AppError("sqlbot_chat_start_failed", "SQLBot did not return a chat id.", 502)
        return int(data["id"])

    async def ask_question(
        self, *, assistant_token: str, ticket: str, chat_id: int, question: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        headers = self._assistant_headers(assistant_token, ticket)
        events: list[dict[str, Any]] = []
        try:
            async with self.client.stream(
                "POST", "/api/v1/chat/question", headers=headers,
                json={"question": question, "chat_id": chat_id, "generate_chart": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line.removeprefix("data:").strip())
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    event = self.public_details(event, (ticket, assistant_token))
                    if event.get("type") == "error":
                        raise _question_error(event.get("content"), events)
                    events.append(event)
                    if on_event is not None:
                        await on_event(event)
        except httpx.TimeoutException as exc:
            raise AppError("sqlbot_transport_timeout", "等待 SQLBot 响应超时；后台查询可能仍在执行。", 504) from exc
        except httpx.HTTPError as exc:
            raise AppError("sqlbot_unavailable", "SQLBot question request failed.", 502) from exc
        record_id = next((event.get("id") for event in events if event.get("type") == "id"), None)
        if record_id is None:
            raise AppError("sqlbot_record_missing", "SQLBot did not return a record id.", 502)
        sql = ""
        chart_hint: dict[str, Any] = {}
        for event in events:
            if event.get("type") == "sql":
                sql = str(event.get("content") or "")
            if event.get("type") == "chart":
                chart_hint = _chart_config(event.get("content"))
            if event.get("type") == "chart-type" and not chart_hint:
                value = event.get("content")
                chart_hint = {"type": value if isinstance(value, str) else None}
        # These stream receipts confirm saved SQL data and chart completion. Fetching the whole
        # chat here rebuilds the dynamic datasource and consumes another callback;
        # multi-turn questions already use both callbacks while loading history.
        executed = any(e.get("type") == "sql-data" and e.get("content") == "execute-success" for e in events)
        finished = any(e.get("type") == "finish" for e in events)
        if executed and finished and sql:
            record = {"id": record_id, "sql": sql, "finish": True, "chart": json.dumps(chart_hint, ensure_ascii=False) if chart_hint else None}
        else:
            chat = await self._assistant_request("GET", f"/api/v1/chat/{chat_id}", assistant_token, ticket)
            records = chat.get("records", []) if isinstance(chat, dict) else []
            record = next((r for r in records if str(r.get("id")) == str(record_id)), None)
        if record and record.get("error"):
            raise _question_error(self.public_details(record["error"], (ticket, assistant_token)), events)
        if record:
            sql = record.get("sql") or sql
            chart_hint = _chart_config(record.get("chart")) or chart_hint
            if not record.get("finish"):
                raise AppError("sqlbot_question_incomplete", "SQLBot record is not finished.", 502)
        presentation = await self.record_presentation(
            assistant_token=assistant_token, ticket=ticket, record_id=int(record_id), record=record,
        )
        return {"record_id": int(record_id), "sql": sql, "chart_hint": chart_hint,
                "events": events, "presentation": presentation}

    def public_details(self, value: Any, extra_secrets: tuple[str, ...] = ()) -> Any:
        """Only response/trace content is exported; redact configured credentials recursively."""
        secrets = list(extra_secrets)
        for name in type(self.settings).model_fields:
            secret = getattr(self.settings, name, None)
            if hasattr(secret, "get_secret_value"):
                secrets.append(secret.get_secret_value())
        def clean(item: Any) -> Any:
            if isinstance(item, dict):
                return {k: ("[redacted]" if any(term in k.lower() for term in
                        ("password", "authorization", "certificate", "secret_key", "ticket")) else clean(v))
                        for k, v in item.items()}
            if isinstance(item, list):
                return [clean(v) for v in item]
            if isinstance(item, str):
                for secret in secrets:
                    if secret and len(secret) >= 6:
                        item = item.replace(secret, "[redacted]")
            return item
        return clean(value)

    async def record_presentation(self, *, assistant_token: str, ticket: str,
                                  record_id: int, record: dict | None = None) -> dict:
        allowed = ("question", "sql_answer", "chart_answer", "analysis", "analysis_thinking",
                   "predict", "predict_content", "recommended_question", "chart", "duration",
                   "total_tokens", "create_time", "finish_time", "finish", "error")
        result = {"record": {k: v for k, v in (record or {}).items() if k in allowed}}
        try:
            result["execution"] = await self._assistant_request(
                "GET", f"/api/v1/chat/record/{record_id}/log", assistant_token, ticket)
        except AppError:
            # A log failure must not discard an otherwise successful query.
            result["warnings"] = ["执行明细暂时无法读取，查询结果仍然有效。"]
        return self.public_details(result, (ticket, assistant_token))

    async def followup(self, *, assistant_token: str, ticket: str, record_id: int,
                       action: str, on_event: Callable[[dict], Awaitable[None]]) -> dict:
        if action not in {"analysis", "predict", "recommend"}:
            raise AppError("invalid_data_action", "Unsupported action", 400)
        path = (f"/api/v1/chat/recommend_questions/{record_id}" if action == "recommend"
                else f"/api/v1/chat/record/{record_id}/{action}")
        events = []
        try:
            async with self.client.stream("POST", path, headers=self._assistant_headers(assistant_token, ticket), json={}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:])
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    event = self.public_details(event, (ticket, assistant_token))
                    if event.get("type") == "error":
                        raise _question_error(event.get("content"), events, record_id=record_id, stage=action)
                    await on_event(event)
                    events.append(event)
        except httpx.TimeoutException as exc:
            raise AppError("sqlbot_transport_timeout", "等待 SQLBot 后续分析响应超时。", 504) from exc
        except httpx.HTTPError as exc:
            raise AppError("sqlbot_unavailable", "SQLBot follow-up request failed.", 502) from exc
        child = next((e.get("id") for e in events if e.get("type") == "id"), record_id)
        result = await self.record_presentation(assistant_token=assistant_token, ticket=ticket, record_id=int(child))
        result["events"] = events
        if action == "predict" and any(e.get("type") == "predict-success" for e in events):
            result["predictionData"] = await self._assistant_request(
                "GET", f"/api/v1/chat/record/{child}/predict_data", assistant_token, ticket)
        return self.public_details(result, (ticket, assistant_token))

    async def record_data(
        self, *, assistant_token: str, ticket: str, record_id: int
    ) -> dict[str, Any]:
        data = await self._assistant_request(
            "GET", f"/api/v1/chat/record/{record_id}/data",
            assistant_token, ticket,
        )
        if not isinstance(data, dict):
            raise AppError("sqlbot_record_invalid", "SQLBot record data is invalid.", 502)
        return data

    async def _admin_token(self) -> str:
        if self._management_token:
            return self._management_token
        password = self.settings.sqlbot_admin_password
        if password is None:
            raise AppError(
                "sqlbot_admin_not_configured",
                "SQLBot management credentials are not configured.",
                503,
            )
        data = await self._request(
            "POST", "/api/v1/mcp/access_token",
            json={
                "username": self.settings.sqlbot_admin_account,
                "password": password.get_secret_value(),
            },
        )
        if not isinstance(data, dict) or not data.get("access_token"):
            raise AppError("sqlbot_login_failed", "SQLBot management login failed.", 502)
        self._management_token = str(data["access_token"])
        return self._management_token

    async def _assistant_request(
        self,
        method: str,
        path: str,
        assistant_token: str,
        ticket: str,
        **kwargs: Any,
    ) -> Any:
        try:
            response = await self.client.request(
                method, path, headers=self._assistant_headers(assistant_token, ticket), **kwargs
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AppError("sqlbot_unavailable", "SQLBot request failed.", 502) from exc
        return _unwrap(response.json())

    async def _request(
        self, method: str, path: str, *, token: str | None = None, **kwargs: Any
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["X-SQLBOT-TOKEN"] = f"Bearer {token}"
        try:
            response = await self.client.request(method, path, headers=headers, **kwargs)
            if response.status_code == 401 and token:
                self._management_token = None
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AppError("sqlbot_unavailable", "SQLBot management request failed.", 502) from exc
        return _unwrap(response.json())

    @staticmethod
    def _assistant_headers(assistant_token: str, ticket: str) -> dict[str, str]:
        certificate = json.dumps(
            [{"key": "X-Davinci-Ticket", "value": ticket, "target": "header"}],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        encoded = base64.b64encode(quote(certificate, safe="").encode("utf-8")).decode("ascii")
        return {
            "X-SQLBOT-ASSISTANT-TOKEN": f"Assistant {assistant_token}",
            "X-SQLBOT-ASSISTANT-CERTIFICATE": encoded,
        }


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "code" in value and "data" in value:
        if value.get("code") not in {0, 200}:
            raise AppError("sqlbot_error", _bounded_message(value.get("msg") or value.get("message")), 502)
        return value.get("data")
    return value


def _bounded_message(value: object) -> str:
    text = " ".join(str(value or "SQLBot request failed.").split())
    return text[:500]


def _question_error(value: object, events: list[dict], *, record_id=None, stage=None) -> AppError:
    """Classify upstream failures, without matching or rewriting user questions."""
    payload = value
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            pass
    message = payload.get("message", payload) if isinstance(payload, dict) else payload
    raw = str(message or "SQLBot request failed.")
    lower = raw.lower()
    if stage is None:
        stage = "sql_generation"
        for event in events:
            if event.get("type") == "sql":
                stage = "sql_execution"
            elif event.get("type") == "sql-data" and event.get("content") == "execute-success":
                stage = "chart_generation"
    record_id = next((e.get("id") for e in events if e.get("type") == "id"), record_id)
    details = {"record_id": record_id, "stage": stage, "upstream_message": _bounded_message(raw)}
    if "insufficient_quota" in lower or "arrearage" in lower:
        code, message = "sqlbot_model_quota_exceeded", "模型服务额度不足，当前步骤未完成。"
    elif "2013" in lower and ("timed out" in lower or "timeout" in lower):
        code, message = "sqlbot_database_timeout", "SQL 已生成，但等待数据库查询结果超时。"
    elif stage == "sql_execution":
        code, message = "sqlbot_sql_execution_failed", "SQL 执行失败，请查看数据库错误详情。"
    elif stage == "chart_generation":
        code, message = "sqlbot_chart_failed", "查询已完成，后续图表步骤失败。"
    else:
        code, message = "sqlbot_question_failed", _bounded_message(raw)
    return AppError(code, message, 502, details=details)


def _chart_config(value: Any) -> dict[str, Any]:
    """Preserve SQLBot's declarative chart contract, never execute model code."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}
