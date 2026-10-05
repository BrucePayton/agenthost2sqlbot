"""Read protected layout diagnostics without SSH or exposing credentials in argv."""
import argparse
import base64
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


class NoRedirect(HTTPRedirectHandler):
    """Never forward operator credentials to redirects."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    """Print one run, or recent run IDs in a known session, as structured JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id", type=UUID)
    group.add_argument("--session")
    parser.add_argument("--config", type=Path,
        default=Path.home() / ".config/shucao/layout-diagnostics.json")
    args = parser.parse_args()
    config = {}
    try:
        if args.config.exists():
            if args.config.stat().st_mode & 0o077:
                raise ValueError("Configuration must have owner-only permissions (chmod 600).")
            config = json.loads(args.config.read_text())
        base = os.environ.get("LAYOUT_DIAGNOSTICS_BASE_URL") or config.get("baseUrl", "")
        user = os.environ.get("SESSION_INSPECTOR_USERNAME") or config.get("username", "inspector")
        password = os.environ.get("SESSION_INSPECTOR_PASSWORD") or config.get("password", "")
        parsed = urlsplit(base)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Configure a valid LAYOUT_DIAGNOSTICS_BASE_URL or baseUrl.")
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Remote diagnostics require HTTPS; HTTP is allowed only on loopback.")
        if not password:
            raise ValueError("Configure SESSION_INSPECTOR_PASSWORD or a private config file.")
        suffix = f"/{args.run_id}" if args.run_id else "?" + urlencode({"sessionId": args.session})
        url = base.rstrip("/") + "/api/inspector/dashboardLayoutRuns" + suffix
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        request = Request(url, headers={"Authorization": "Basic " + auth, "Accept": "application/json"})
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
            if len(payload) > 2 * 1024 * 1024:
                raise ValueError("Diagnostic response exceeds the size limit.")
            print(json.dumps(json.loads(payload), ensure_ascii=False, indent=2))
        return 0
    except HTTPError as error:
        print(f"Diagnostic request failed (HTTP {error.code}); verify deployment, access or retention.", file=sys.stderr)
    except URLError:
        print("Diagnostic server unavailable; verify network and TLS.", file=sys.stderr)
    except (OSError, ValueError) as error:
        # Config parse errors may contain local paths; never print config values.
        print(str(error) if type(error) is ValueError else "Cannot read diagnostic configuration/response.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
