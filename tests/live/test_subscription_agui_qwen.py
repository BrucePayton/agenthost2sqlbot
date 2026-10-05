import json
import os
import shutil
import time
import uuid
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agui.contracts import CONTRACT_PATH, load_contract_registry

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DAVINCI_SUBSCRIPTION_AGUI") != "1",
    reason=(
        "Set RUN_LIVE_DAVINCI_SUBSCRIPTION_AGUI=1 to call the workspace model."
    ),
)

RESIDENT_TOOLS = (
    "page.get_context",
    "workspace.list_dashboards",
    "ui.open_dashboard",
    "dashboard.get_structure",
    "dashboard.get_errors",
    "dashboard.get_widget_config",
    "dashboard.get_widget_data",
    "dashboard.refresh_widget",
    "dashboard.get_widget_edit_capabilities",
    "dashboard.apply_widget_edits",
    "dashboard.get_publish_readiness",
    "dashboard.publish",
)
MESSAGE_RULE_TOOLS = (
    "space.message_rule.get_context",
    "space.message_rule.search_options",
    "space.message_rule.start_draft",
    "space.message_rule.apply_draft",
    "space.message_rule.review_draft",
    "space.message_rule.save_draft",
)
SUBSCRIPTION_REQUEST = (
    "帮我配置：每天 09:00，把奢侈品回收数据按区域经理分组推送给对应员工；"
    "指标用回收金额，维度用城市，只看已完成订单，"
    "消息包含富文本和数据表。"
)


def _evaluation_model() -> str:
    """Use an explicit evaluation override or the managed workspace's model."""
    import yaml

    override = os.environ.get("DAVINCI_SUBSCRIPTION_EVAL_MODEL", "").strip()
    if override:
        return override
    source = Path(__file__).resolve().parents[2] / "workspaces/davinci-dashboard/workspace.yaml"
    model = yaml.safe_load(source.read_text(encoding="utf-8")).get("model")
    assert isinstance(model, str) and model.strip(), "managed workspace must select a model"
    return model.strip()


class SubscriptionLiveReport:
    """Record only scenario evidence, never settings, environment or stderr."""

    def __init__(self, root: Path, scenario: str, model: str) -> None:
        self.path = root / "subscription-live-evaluation.json"
        self.started = time.perf_counter()
        self.data = {
            "scenario": scenario,
            "model": model,
            "execution": "real-model-real-host-simulated-page-tools",
            "realBusinessQueries": 0,
            "realPersistedRules": 0,
            "outcome": "running",
            "terminal": None,
            "userUtterances": [SUBSCRIPTION_REQUEST],
            "rounds": [],
            "finalAssistant": "",
            "clarificationRounds": 0,
            "skillLoadCount": 0,
            "runtimeToolNames": [],
        }

    def __enter__(self):
        """Keep a report even when later setup or model execution fails."""
        self.flush()
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        """Persist a sanitized failure category without exception text or traces."""
        self.data["outcome"] = "failed" if exception_type else "passed"
        if exception_type:
            self.data["failureType"] = exception_type.__name__
        self.flush()

    def flush(self) -> None:
        """Write the bounded test-owned JSON evidence to pytest's temporary path."""
        self.data["totalElapsedSeconds"] = round(time.perf_counter() - self.started, 3)
        calls = [call for turn in self.data["rounds"] for call in turn["tools"]]
        self.data["hostTurns"] = len(self.data["rounds"])
        self.data.setdefault("modelTurns", None)
        self.data["toolCalls"] = len(calls)
        self.data["ruleListRequests"] = sum(
            call["name"] == "space.message_rule.get_context"
            and call["arguments"].get("includeRules", False) for call in calls
        )
        self.data["dataPreviewRequests"] = sum(
            call["name"] == "space.message_rule.review_draft"
            and call["arguments"].get("includeDataCheck", False) for call in calls
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def capture_runtime_tools(self, messages: list[dict]) -> None:
        """Extract only tool names from Host events; exclude their output bodies."""
        names = [message.get("payload", {}).get("name") for message in messages
                 if message.get("event_type") == "tool.started"]
        self.data["runtimeToolNames"] = [name for name in names if isinstance(name, str)]
        self.data["skillLoadCount"] = names.count("Skill")
        usage = [item.get("payload", {}) for item in messages if item.get("event_type") == "usage.updated"]
        # A program turn is a Host round but makes zero API calls. Keep unknown
        # usage unknown when a provider fails before delivering a result.
        self.data["modelTurns"] = sum(item.get("model_api_turns", 0) for item in usage) if usage else None
        self.data["runtimeTimings"] = [item["runtime_timing"] for item in usage if "runtime_timing" in item]
        self.data["failedRuntimeTimings"] = [item.get("payload", {}).get("runtime_timing", {})
            for item in messages if item.get("event_type") == "runtime.diagnostic" and
            item.get("payload", {}).get("code") == "SUBSCRIPTION_RUNTIME_FAILURE_TIMING"]
        self.data["thinkingDiagnostics"] = [item.get("payload", {}) for item in messages
            if item.get("event_type") == "runtime.diagnostic" and
            item.get("payload", {}).get("code") == "THINKING_CONFIG_MISMATCH"]
        self.data["runtimeConfig"] = [item.get("payload", {}) for item in messages
            if item.get("event_type") == "runtime.config"]
        self.flush()


async def _recorded_page_turn(client, body: dict, report: SubscriptionLiveReport):
    """Record a real Host turn and its deferred calls without exposing raw logs."""
    round_record = {
        "userMessages": [message["content"] for message in body["messages"]
                         if message.get("role") == "user"],
        "assistant": "",
        "tools": [],
    }
    report.data["rounds"].append(round_record)
    for utterance in round_record["userMessages"]:
        if utterance not in report.data["userUtterances"]:
            report.data["userUtterances"].append(utterance)
    started = time.perf_counter()
    try:
        response = await client.post(
            "/api/ag-ui", headers={"Accept": "text/event-stream"}, json=body,
        )
        round_record["httpStatus"] = response.status_code
        assert response.status_code == 200, "Host did not return an AG-UI response"
        calls, assistant = _frontend_calls(response.text)
        round_record["assistant"] = assistant
        round_record["tools"] = [{**call, "simulatedResult": None} for call in calls]
        report.data["finalAssistant"] = assistant
        # A streamed error also uses HTTP 200; do not mistake it for clarification.
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        if any(event.get("type") == "RUN_ERROR" for event in events):
            report.data["terminal"] = "host-run-error"
            pytest.fail("Host emitted RUN_ERROR; see sanitized scenario report")
        return calls, assistant
    finally:
        round_record["elapsedSeconds"] = round(time.perf_counter() - started, 3)
        report.flush()


async def _capture_session_report(client, session_id: str, report: SubscriptionLiveReport) -> None:
    """Read safe Host tool counters without masking the original scenario failure."""
    try:
        messages = (await client.get(f"/api/sessions/{session_id}/messages")).json()
        assert isinstance(messages, list)
        report.capture_runtime_tools(messages)
    except Exception as error:
        report.data["runtimeCaptureFailureType"] = type(error).__name__
        report.flush()


def _write_isolated_workspace(root: Path, model: str | None = None) -> None:
    """Copy only the managed subscription Skill into an MCP-free workspace."""
    source = Path(__file__).resolve().parents[2] / "workspaces/davinci-dashboard"
    workspace = root / "actual"
    skill_target = (
        workspace / ".claude/skills/configure-subscription-rule"
    )
    skill_target.parent.mkdir(parents=True)
    shutil.copytree(
        source / ".claude/skills/configure-subscription-rule",
        skill_target,
    )
    shutil.copy2(source / "CLAUDE.md", workspace / "CLAUDE.md")
    selected_model = model or _evaluation_model()
    (workspace / "workspace.yaml").write_text(
        f"""version: 1
id: actual
name: Davinci Subscription Live Gate
description: Isolated Native V2 subscription live-model gate.
model: {json.dumps(selected_model)}
skills:
  - configure-subscription-rule
allowed_tools:
  - Read
  - Grep
  - Skill
mcp_servers: {{}}
""",
        encoding="utf-8",
    )


def _native_body(session_id: str, run_id: str, model: str | None = None) -> dict:
    """Build an owner/self-view page input with one resident tool catalog."""
    registry = load_contract_registry(
        CONTRACT_PATH.with_name("davinci-agent-v2.json")
    )
    tools = []
    for name in RESIDENT_TOOLS + MESSAGE_RULE_TOOLS:
        contract = registry.get(name)
        tools.append(
            {
                "name": contract.action,
                "description": contract.description,
                "parameters": dict(contract.input_schema),
            }
        )
    return {
        "threadId": session_id,
        "runId": run_id,
        "state": {
            "schemaVersion": "davinci-page-state-v1",
            "page": {
                "instanceId": "subscription-live-gate-page",
                "kind": "collaborative-space",
                "route": "/share/collaborative-space/83/message",
                "space": {
                    "id": "83",
                    "name": "Live Gate Space",
                    "role": "owner",
                },
                "viewMode": "self",
            },
            "permissions": {
                "canRead": True,
                "canOperate": True,
                "canPersist": True,
            },
            "ui": {"busy": False, "activeFilters": []},
            "revisions": {"routeRevision": 1, "resourceRevision": 1},
            "dataStatus": {
                "loadingWidgetIds": [],
                "errorWidgetIds": [],
            },
        },
        "messages": [
            {
                "id": "subscription-live-gate-user",
                "role": "user",
                "content": SUBSCRIPTION_REQUEST,
            }
        ],
        "tools": tools,
        "context": [],
        "forwardedProps": {
            "workspaceId": "actual",
            "profile": "davinci-agui-native-v2",
            "model": model or _evaluation_model(),
            "effort": "medium",
            "profileId": "space",
            "catalogDigest": "subscription-live-gate-v1",
            "toolSetId": "subscription-live-gate-v1",
            "toolSetChanges": 0,
            "catalogDigestChanges": 0,
        },
    }


def _frontend_calls(response_text: str) -> tuple[list[dict], str]:
    """Read deferred page calls and assistant text from one AG-UI response."""
    events = [
        json.loads(line[6:])
        for line in response_text.splitlines()
        if line.startswith("data: ")
    ]
    calls: dict[str, dict] = {}
    order: list[str] = []
    assistant: list[str] = []
    for event in events:
        event_type = event.get("type")
        tool_call_id = event.get("toolCallId")
        if event_type == "TOOL_CALL_START":
            calls[tool_call_id] = {
                "id": tool_call_id,
                "name": event.get("toolCallName"),
                "rawArguments": "",
            }
            order.append(tool_call_id)
        elif event_type == "TOOL_CALL_ARGS" and tool_call_id in calls:
            calls[tool_call_id]["rawArguments"] += event.get("delta", "")
        elif event_type == "TEXT_MESSAGE_CONTENT":
            assistant.append(str(event.get("delta", "")))
    parsed = []
    for tool_call_id in order:
        call = calls[tool_call_id]
        parsed.append(
            {
                "id": call["id"],
                "name": call["name"],
                "arguments": json.loads(call["rawArguments"] or "{}"),
            }
        )
    return parsed, "".join(assistant)


def _option(ref: str, label: str, **metadata) -> dict:
    """Build one bounded opaque option for the live page simulator."""
    return {"ref": ref, "label": label, **metadata}


class SubscriptionPageSimulator:
    """Schema-checked model fixture, not an implementation of native validators.

    Native operation/flow tests own real normalization, policy and persistence
    behavior. This test double checks only the declared luxury-report intent;
    it never queries business data, creates a real rule or confirms for a user.
    """

    def __init__(self, *, allow_save: bool = False) -> None:
        self.registry = load_contract_registry(
            CONTRACT_PATH.with_name("davinci-agent-v2.json")
        )
        self.revision = 0
        self.scene = "dashboard-push"
        self.operations: dict[str, dict] = {}
        self.allow_save = allow_save
        self.confirmed_revision: int | None = None
        self.reviewed_revision: int | None = None
        self.receipt: dict | None = None
        self.query_ref = "opaque-query-luxury"
        self.issued_refs: set[str] = set()

    def confirm_native(self, revision: int) -> None:
        """Simulate an explicit native confirmation for an exact reviewed revision."""
        assert self.allow_save, "configuration-only scenario must not confirm save"
        assert revision == self.revision == self.reviewed_revision
        self.confirmed_revision = revision

    def result(self, name: str, arguments: dict) -> str:
        """Validate canonical arguments and result, then return one Tool envelope."""
        contract = self.registry.get(name)
        Draft202012Validator(dict(contract.input_schema)).validate(arguments)
        data = self._result(name, arguments)
        Draft202012Validator(dict(contract.output_schema)).validate(data)
        return json.dumps(
            {"status": "success", "data": data, "issues": []},
            ensure_ascii=False,
        )

    def _result(self, name: str, arguments: dict) -> dict:
        """Emulate only fixture evidence and lifecycle, without real persistence."""
        if name == "space.message_rule.get_context":
            scope = {
                "kind": "space",
                "recipientPolicy": "configurable",
                "spaceName": "Live Gate Space",
                "canConfigure": True,
                "canSave": True,
            }
            result = {
                "summary": json.dumps({"scope": scope, "records": []}),
                "contextVersion": 1,
                "scope": scope,
                "rulesIncluded": arguments.get("includeRules", False),
            }
            if self.revision and self.receipt is None:
                result["activeDraft"] = {
                    **self._draft(), "lifecycle": "active", "editable": True
                }
            return result
        if name == "space.message_rule.search_options":
            kind = arguments["kind"]
            parent = {"field": "datasetRef", "enum_value": "fieldRef"}.get(kind)
            if parent:
                assert arguments[parent] in self.issued_refs, "unissued parent reference"
            candidates = self._options(arguments)
            self.issued_refs.update(item["ref"] for item in candidates)
            return {
                "contextVersion": 1,
                "kind": kind,
                "results": candidates,
                "summary": f"找到 {len(candidates)} 个候选",
                "truncated": False,
                **({"parentRef": arguments[parent]} if parent else {}),
            }
        if name == "space.message_rule.start_draft":
            assert not self.revision, "start_draft must not replace an active draft"
            assert arguments["mode"] == "blank", "fixture has no selected template"
            self.scene = arguments["scene"]
            for operation in arguments.get("operations", []):
                self._check_refs(operation)
            for operation in arguments.get("operations", []):
                self._apply(operation)
            self.revision = 1
            return self._draft()
        assert self.revision and self.receipt is None, "no editable draft"
        assert arguments.get("expectedRevision", self.revision) == self.revision, "stale revision"
        if name == "space.message_rule.apply_draft":
            for operation in arguments["operations"]:
                self._check_refs(operation)
            before = deepcopy(self.operations)
            for operation in arguments["operations"]:
                self._apply(operation)
            if self.operations != before:
                self.revision += 1
                self.reviewed_revision = None
                self.confirmed_revision = None
            return {
                "status": "success",
                "revision": self.revision,
                "appliedOps": len(arguments["operations"]),
                "summary": self._summary(),
                "invalidated": [],
                "warnings": [],
                **self._public_configuration(),
            }
        if name == "space.message_rule.review_draft":
            assert not arguments.get("includeDataCheck", False), "fixture did not request data preview"
            errors = self._fixture_errors()
            if not errors:
                self.reviewed_revision = self.revision
            return {
                "complete": not errors,
                "revision": self.revision,
                "errors": errors,
                "warnings": [],
                "dataChecks": [],
                "summary": self._summary(),
            }
        if name == "space.message_rule.save_draft":
            assert self.allow_save, "help configure must stop at an editable draft"
            assert self.confirmed_revision == self.reviewed_revision == self.revision
            assert not self._fixture_errors()
            self.receipt = {
                "status": "success",
                "persisted": True,
                "ruleRef": "opaque-simulated-rule",
                "finalStatus": arguments["desiredStatus"],
                "ownershipScope": "space",
                "summary": "测试回执：规则已创建；并未写入真实服务",
                "contextVersion": 2,
            }
            return self.receipt
        raise AssertionError(f"Unexpected simulated page tool: {name}")

    def _check_refs(self, value: object, key: str = "") -> None:
        """Reject fabricated handles before mutating this fixture's operation log."""
        if isinstance(value, dict):
            for child_key, child in value.items():
                self._check_refs(child, child_key)
        elif isinstance(value, list):
            for child in value:
                self._check_refs(child, key[:-1] if key.endswith("Refs") else key)
        elif key.lower().endswith("queryref"):
            assert value == self.query_ref and "upsert_dataset_query" in self.operations, "unissued queryRef"
        elif key.endswith("Ref"):
            assert value in self.issued_refs, f"unissued option: {key}"

    def _apply(self, operation: dict) -> None:
        """Retain sequential patches for the sole query in this scenario."""
        kind = operation["operation"]
        if kind == "remove_dataset_query":
            self.operations.pop("upsert_dataset_query", None)
            return
        saved = {**self.operations.get(kind, {}), **operation}
        if kind == "set_content" and "components" in operation:
            for key in ("richTextMarkdown", "richTextBindings", "includeDataTable", "dataTableQueryRef", "dataTableFieldRefs", "dataTableOutputRefs"):
                saved.pop(key, None)
        if kind == "set_push_mode" and operation.get("mode") == "all":
            saved = {"operation": kind, "mode": "all"}
        if kind == "upsert_dataset_query":
            saved["queryRef"] = self.query_ref
        self.operations[kind] = saved

    def _draft(self) -> dict:
        """Publish the same public section names and string summary as native."""
        return {
            "status": "active",
            "revision": self.revision,
            "source": "blank",
            "scene": self.scene,
            "summary": self._summary(),
            "sections": ["trigger", "datasets", "pushMode", "receivers", "sendContent", "finalize"],
            "locks": [],
            **self._public_configuration(),
        }

    def _public_configuration(self) -> dict:
        """Expose current operation projection, not a hidden always-valid result."""
        query = self.operations.get("upsert_dataset_query")
        outputs = []
        if query:
            selected = set(query.get("dimensionRefs", []) + query.get("metricRefs", []))
            selected.update(metric["fieldRef"] for metric in query.get("metrics", []))
            outputs = [{
                "outputRef": f"output-{item['ref']}",
                "fieldRef": item["ref"],
                "label": item["label"],
                "isComparison": False,
                **{key: value for key, value in item.items() if key not in {"ref", "label"}},
            } for item in self._options({"kind": "field"}) if item["ref"] in selected]
            self.issued_refs.update(item["outputRef"] for item in outputs)
        return {
            "queries": [] if not query else [{
                "queryRef": self.query_ref,
                "label": query.get("name", "奢侈品回收数据"),
                "datasetRef": query["datasetRef"],
                "outputs": outputs,
                "filtersComplete": isinstance(query.get("filters", []), list),
            }],
            "images": [],
            "recipientPolicy": "configurable",
            "configuration": {
                "operations": list(self.operations.values()),
                "unresolved": [],
            },
        }

    def _summary(self) -> str:
        """Keep configured query handles in a bounded legacy-compatible summary."""
        return json.dumps({
            "revision": self.revision,
            "queries": self._public_configuration()["queries"],
            "configuredSections": list(self.operations),
            "status": "disabled",
        }, ensure_ascii=False)

    def _fixture_errors(self) -> list[dict]:
        """Assert this scenario's requested outcome, not generic domain validity."""
        ops = self.operations
        query = ops.get("upsert_dataset_query", {})
        schedule = ops.get("set_schedule", {})
        push = ops.get("set_push_mode", {})
        receivers = ops.get("set_recipients", {})
        content = ops.get("set_content", {})
        finalize = ops.get("set_finalize", {})
        components = content.get("components", [])
        metrics = query.get("metricRefs", []) + [
            metric.get("fieldRef") for metric in query.get("metrics", [])
        ]
        checks = {
            "trigger": schedule.get("frequency") == "daily"
            and schedule.get("times") == ["09:00"]
            and ops.get("set_send_rule", {}).get("sendRule") == "scheduled_dataset",
            "datasets": query.get("datasetRef") == "ref-dataset-luxury"
            and "ref-field-amount" in metrics
            and {"ref-field-city", "ref-field-manager", "ref-field-employee"}.issubset(query.get("dimensionRefs", []))
            and any(item.get("fieldRef") == "ref-field-status"
                    and item.get("operator") in {"eq", "=", "in"}
                    and (item.get("valueRefs") == ["ref-value-completed"]
                         or item.get("values") == ["已完成"])
                    for item in query.get("filters", [])),
            "pushMode": push.get("mode") == "group"
            and push.get("queryRef") == self.query_ref
            and push.get("groupByFieldRef") == "ref-field-manager",
            "receivers": "ref-field-employee" in query.get("dimensionRefs", [])
            and ("ref-field-employee" in receivers.get("fieldRefs", [])
            or any(item.get("queryRef") == self.query_ref
                   and item.get("fieldRef") == "ref-field-employee"
                   for item in receivers.get("fieldRecipients", []))),
            "sendContent": bool(content.get("title"))
            and (bool(content.get("richTextMarkdown"))
                 or any(item.get("type") == "richtext" and item.get("markdown") for item in components))
            and ((content.get("includeDataTable") is True
                  and content.get("dataTableQueryRef") == self.query_ref)
                 or any(item.get("type") == "data-table"
                        and item.get("queryRef") == self.query_ref for item in components)),
            "finalize": bool(finalize.get("ruleName", "").strip())
            and finalize.get("desiredStatus", "disabled") == "disabled",
        }
        return [{"section": section, "path": section, "code": "FIXTURE_REQUIREMENT_MISSING",
                 "message": f"测试需求尚未完整配置：{section}"}
                for section, valid in checks.items() if not valid]

    @staticmethod
    def _options(arguments: dict) -> list[dict]:
        """Resolve exact fixture candidates by the requested option kind."""
        kind = arguments.get("kind")
        if kind == "dataset":
            return [_option("ref-dataset-luxury", "奢侈品回收数据")]
        if kind == "field":
            return [
                _option(
                    "ref-field-amount",
                    "回收金额",
                    dataType="number",
                    role="metric",
                ),
                _option(
                    "ref-field-city",
                    "城市",
                    dataType="string",
                    role="dimension",
                ),
                _option("ref-field-status", "订单状态", dataType="string"),
                _option("ref-field-manager", "区域经理", dataType="string"),
                _option(
                    "ref-field-employee",
                    "对应员工",
                    dataType="string",
                    role="dimension",
                    isEmployeeAccount=True,
                    employeeAccountType="ob",
                ),
            ]
        if kind == "enum_value":
            return [_option("ref-value-completed", "已完成")]
        if kind == "recipient_member":
            return [_option("ref-member-employee", "对应员工")]
        if kind == "recipient_group":
            return [_option("ref-group-manager", "区域经理组")]
        if kind == "tag":
            return [_option("ref-tag-daily", "每日经营推送")]
        return []


@pytest.mark.asyncio
async def test_real_qwen_loads_subscription_bundle_once_and_reads_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate direct Skill routing to the first resident page read."""
    model = _evaluation_model()
    with SubscriptionLiveReport(tmp_path, "subscription-routing", model) as report:
        import httpx

        from app.config import Settings

        workspaces_root = tmp_path / "workspaces"
        _write_isolated_workspace(workspaces_root, model)
        monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces_root))
        monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
        from app.main import create_app

        settings = Settings(
            workspaces_root=workspaces_root,
            app_data_dir=tmp_path / "data",
            mock_personal_workspace_id="actual",
            mock_workspace_roles={"actual": "owner"},
            turn_timeout_seconds=180,
            claude_model=model,
            claude_selectable_models=model,
        )
        app = create_app(settings=settings)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=240,
        ) as client:
            session = (await client.post("/api/workspaces/actual/sessions")).json()
            run_id = str(uuid.uuid4())
            try:
                calls, _ = await _recorded_page_turn(
                    client, _native_body(session["id"], run_id, model), report,
                )
                turn = (await client.get(f"/api/turns/{run_id}")).json()
            finally:
                await _capture_session_report(client, session["id"], report)

        assert turn["status"] == "completed"
        assert report.data["runtimeToolNames"][0] == "Skill"
        assert "ToolSearch" not in report.data["runtimeToolNames"]
        assert turn["tool_search_calls"] == 0
        assert [call["name"] for call in calls] == ["space.message_rule.get_context"]
        report.data["terminal"] = "context-read-requested"


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DAVINCI_SUBSCRIPTION_FLOW") != "1",
    reason=(
        "Set RUN_LIVE_DAVINCI_SUBSCRIPTION_FLOW=1 for the multi-turn live gate."
    ),
)
@pytest.mark.parametrize("save_after_review", [False, True])
@pytest.mark.asyncio
async def test_real_qwen_finishes_at_reviewed_draft_or_confirmed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    save_after_review: bool,
) -> None:
    """Check model behavior using schema-checked simulated native results only."""
    model = _evaluation_model()
    scenario = "subscription-confirmed-simulated-save" if save_after_review else "subscription-draft-only"
    with SubscriptionLiveReport(tmp_path, scenario, model) as report:
        import httpx

        from app.config import Settings

        workspaces_root = tmp_path / "workspaces"
        _write_isolated_workspace(workspaces_root, model)
        monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces_root))
        monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
        from app.main import create_app

        settings = Settings(
            workspaces_root=workspaces_root,
            app_data_dir=tmp_path / "data",
            mock_personal_workspace_id="actual",
            mock_workspace_roles={"actual": "owner"},
            turn_timeout_seconds=180,
            claude_model=model,
            claude_selectable_models=model,
        )
        app = create_app(settings=settings)
        simulator = SubscriptionPageSimulator(allow_save=save_after_review)
        sequence: list[str] = []
        save_requested = False
        final_assistant = ""
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=240,
        ) as client:
            session = (await client.post("/api/workspaces/actual/sessions")).json()
            body = _native_body(session["id"], str(uuid.uuid4()), model)
            try:
                for step in range(24):
                    calls, assistant = await _recorded_page_turn(client, body, report)
                    if not calls:
                        if simulator.reviewed_revision == simulator.revision:
                            if not save_after_review or simulator.receipt is not None:
                                final_assistant = assistant
                                report.data["terminal"] = "simulated-saved-disabled" if simulator.receipt else "reviewed-draft"
                                break
                            assert not save_requested, "model did not finish after simulated save"
                            save_requested = True
                            body = _native_body(session["id"], str(uuid.uuid4()), model)
                            body["messages"] = [{
                                "id": "subscription-explicit-save",
                                "role": "user",
                                "content": "确认保存刚才检查过的草稿，规则保持停用。",
                            }]
                            continue
                        # A clarification is useful evidence, not permission to teach
                        # operation names or silently change this to a second-shot pass.
                        report.data["clarificationRounds"] += 1
                        report.data["terminal"] = "clarification-needed"
                        pytest.fail("First-shot scenario needs clarification; see recorded business dialogue")

                    sequence.extend(call["name"] for call in calls)
                    results = []
                    for index, call in enumerate(calls):
                        if call["name"] == "space.message_rule.save_draft":
                            assert save_requested, "model tried to persist a configuration request"
                            simulator.confirm_native(call["arguments"]["expectedRevision"])
                        content = simulator.result(call["name"], call["arguments"])
                        report.data["rounds"][-1]["tools"][index]["simulatedResult"] = json.loads(content)
                        report.flush()
                        results.append({
                            "id": f"result-{step}-{index}", "role": "tool",
                            "toolCallId": call["id"], "content": content,
                        })
                    body = _native_body(session["id"], str(uuid.uuid4()), model)
                    body["messages"] = results
                else:
                    report.data["terminal"] = "turn-limit-exceeded"
                    pytest.fail("Requested terminal outcome not reached within 24 model turns")
            finally:
                report.data["finalDraft"] = simulator._public_configuration()
                report.data["simulatedReceipt"] = simulator.receipt
                await _capture_session_report(client, session["id"], report)

        assert sequence[0] == "space.message_rule.get_context"
        assert sequence.count("space.message_rule.start_draft") == 1
        assert simulator.reviewed_revision == simulator.revision
        assert simulator._fixture_errors() == []
        assert final_assistant
        assert report.data["clarificationRounds"] == 0
        assert "ToolSearch" not in report.data["runtimeToolNames"]
        assert sequence.count("space.message_rule.save_draft") == int(save_after_review)
        if save_after_review:
            assert simulator.receipt is not None
            assert simulator.receipt["persisted"] is True
            assert simulator.receipt["finalStatus"] == "disabled"
            assert simulator.receipt["ownershipScope"] == "space"
            assert simulator.receipt["ruleRef"]
        else:
            assert simulator.receipt is None
            assert simulator.confirmed_revision is None
