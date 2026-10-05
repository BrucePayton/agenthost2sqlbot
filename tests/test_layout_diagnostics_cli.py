import base64
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "layout_diagnostics.py"
RUN_ID = "00000000-0000-4000-8000-000000000001"
USERNAME = "test-operator"
PASSWORD = "test-only-layout-password-32-chars"
AUTHORIZATION = "Basic " + base64.b64encode(
    f"{USERNAME}:{PASSWORD}".encode()
).decode()


@pytest.fixture
def cli(tmp_path):
    # Isolate subprocesses from the developer's credentials, HOME and proxies.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LAYOUT_DIAGNOSTICS_", "SESSION_INSPECTOR_", "APP_SESSION_INSPECTOR_"))
        and not key.lower().endswith("_proxy")
    }
    env.update(HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")

    def run(*args, extra_env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=tmp_path,
            env={**env, **(extra_env or {})},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    return run


@pytest.fixture
def private_config(tmp_path):
    def create(base_url, *, path=None, mode=0o600, **values):
        path = path or tmp_path / ".config/shucao/layout-diagnostics.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "baseUrl": base_url, "username": USERNAME, "password": PASSWORD, **values,
        }))
        path.chmod(mode)
        return path

    return create


@pytest.fixture
def http_server():
    servers = []

    def start():
        state = SimpleNamespace(
            requests=[], status=200, headers={},
            body=json.dumps({"layoutRunId": RUN_ID}).encode(),
        )

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state.requests.append({
                    "method": self.command,
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "accept": self.headers.get("Accept"),
                    "body": self.rfile.read(int(self.headers.get("Content-Length", "0"))),
                })
                self.send_response(state.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(state.body)))
                for key, value in state.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(state.body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        servers.append((server, thread))
        state.url = f"http://127.0.0.1:{server.server_port}"
        return state

    yield start
    for server, thread in reversed(servers):
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def assert_private_failure(result, expected):
    assert result.returncode == 1
    assert result.stdout == ""
    assert expected in result.stderr
    assert PASSWORD not in result.stderr
    assert AUTHORIZATION not in result.stderr


def test_default_config_600_queries_run_with_read_only_get(cli, private_config, http_server):
    server = http_server()
    path = private_config(server.url + "/")
    before = path.read_bytes()

    result = cli("--run-id", RUN_ID)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"layoutRunId": RUN_ID}
    assert server.requests == [{
        "method": "GET",
        "path": f"/api/inspector/dashboardLayoutRuns/{RUN_ID}",
        "authorization": AUTHORIZATION,
        "accept": "application/json",
        "body": b"",
    }]
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o600


def test_custom_config_and_encoded_session_query(cli, private_config, http_server, tmp_path):
    server = http_server()
    payload = {"items": [{"layoutRunId": RUN_ID}]}
    server.body = json.dumps(payload).encode()
    path = private_config(server.url + "/host/", path=tmp_path / "operator.json")
    session = "session /+?&=\u4e2d\u6587"

    result = cli("--config", str(path), "--session", session)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == payload
    assert len(server.requests) == 1
    request = server.requests[0]
    parsed = urlsplit(request["path"])
    assert parsed.path == "/host/api/inspector/dashboardLayoutRuns"
    assert parse_qs(parsed.query) == {"sessionId": [session]}
    assert request["method"] == "GET"
    assert request["authorization"] == AUTHORIZATION
    assert request["body"] == b""


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o601])
def test_insecure_config_rejected_before_network_even_with_env(
    cli, private_config, http_server, mode,
):
    server = http_server()
    private_config(server.url, mode=mode)

    result = cli("--run-id", RUN_ID, extra_env={
        "LAYOUT_DIAGNOSTICS_BASE_URL": server.url,
        "SESSION_INSPECTOR_USERNAME": USERNAME,
        "SESSION_INSPECTOR_PASSWORD": PASSWORD,
    })

    assert_private_failure(result, "chmod 600")
    assert server.requests == []


def test_missing_config_and_environment_fails_cleanly(cli):
    result = cli("--run-id", RUN_ID)

    assert_private_failure(result, "LAYOUT_DIAGNOSTICS_BASE_URL")


def test_missing_password_rejects_server_prefixed_environment(cli, http_server):
    server = http_server()

    result = cli("--run-id", RUN_ID, extra_env={
        "LAYOUT_DIAGNOSTICS_BASE_URL": server.url,
        "APP_SESSION_INSPECTOR_ENABLED": "true",
        "APP_SESSION_INSPECTOR_USERNAME": USERNAME,
        "APP_SESSION_INSPECTOR_PASSWORD": PASSWORD,
    })

    assert_private_failure(result, "SESSION_INSPECTOR_PASSWORD")
    assert server.requests == []


@pytest.mark.parametrize("config_exists", [False, True])
def test_script_environment_without_config_or_overriding_config(
    cli, private_config, http_server, config_exists,
):
    server = http_server()
    if config_exists:
        private_config("https://unused.example.invalid", username="unused", password="unused")

    result = cli("--run-id", RUN_ID, extra_env={
        "LAYOUT_DIAGNOSTICS_BASE_URL": server.url,
        "SESSION_INSPECTOR_USERNAME": USERNAME,
        "SESSION_INSPECTOR_PASSWORD": PASSWORD,
    })

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"layoutRunId": RUN_ID}
    assert len(server.requests) == 1
    assert server.requests[0]["authorization"] == AUTHORIZATION


def test_remote_http_is_rejected_without_sending_credentials(cli, private_config, http_server):
    proxy = http_server()
    private_config("http://remote.example.invalid")

    # Any attempted HTTP request would be observable at this local proxy.
    result = cli("--run-id", RUN_ID, extra_env={"http_proxy": proxy.url})

    assert_private_failure(result, "Remote diagnostics require HTTPS")
    assert proxy.requests == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("cross_origin", [False, True])
def test_redirects_never_forward_credentials(
    cli, private_config, http_server, status, cross_origin,
):
    origin = http_server()
    target = http_server() if cross_origin else origin
    origin.status = status
    origin.headers = {"Location": target.url + "/credential-sink"}
    origin.body = json.dumps({"secret": PASSWORD}).encode()
    private_config(origin.url)

    result = cli("--run-id", RUN_ID)

    assert_private_failure(result, f"HTTP {status}")
    assert len(origin.requests) == 1
    assert origin.requests[0]["path"] == f"/api/inspector/dashboardLayoutRuns/{RUN_ID}"
    assert origin.requests[0]["authorization"] == AUTHORIZATION
    if cross_origin:
        assert target.requests == []
    assert all(request["path"] != "/credential-sink" for request in target.requests)


@pytest.mark.parametrize("status", [401, 404, 500])
def test_http_error_does_not_print_response_secrets(cli, private_config, http_server, status):
    server = http_server()
    server.status = status
    server.body = json.dumps({"password": PASSWORD, "authorization": AUTHORIZATION}).encode()
    private_config(server.url)

    result = cli("--run-id", RUN_ID)

    assert_private_failure(result, f"HTTP {status}")
    assert len(server.requests) == 1


def test_oversized_response_is_rejected(cli, private_config, http_server):
    server = http_server()
    server.body = b" " * (2 * 1024 * 1024 + 1)
    private_config(server.url)

    result = cli("--run-id", RUN_ID)

    assert_private_failure(result, "Diagnostic response exceeds the size limit")


def test_invalid_json_config_does_not_print_contents(cli, private_config, http_server):
    server = http_server()
    path = private_config(server.url)
    path.write_text('{"password": "' + PASSWORD + '", invalid}')

    result = cli("--run-id", RUN_ID)

    assert_private_failure(result, "Cannot read diagnostic configuration/response")
    assert server.requests == []


@pytest.mark.parametrize("args", [
    [],
    ["--run-id", "not-a-uuid"],
    ["--run-id", RUN_ID, "--session", "session"],
])
def test_invalid_arguments_fail_before_network(cli, private_config, http_server, args):
    server = http_server()
    private_config(server.url)

    result = cli(*args)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage:" in result.stderr
    assert PASSWORD not in result.stderr
    assert server.requests == []
