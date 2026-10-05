from urllib.parse import quote

import httpx

from app.auth.membership import AuthorityMembership, MembershipAuthorityUnavailable
from app.config import Settings


class HttpSpaceMembershipAuthority:
    def __init__(
        self, settings: Settings, *, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.http_client = http_client

    async def fetch_membership(
        self, subject: str, workspace_external_id: str
    ) -> AuthorityMembership | None:
        base = str(self.settings.space_authority_url).rstrip("/")
        url = (
            f"{base}/v1/spaces/{quote(workspace_external_id, safe='')}"
            f"/members/{quote(subject, safe='')}"
        )
        headers = {
            "Authorization": (
                "Bearer "
                + self.settings.space_authority_token.get_secret_value()
            )
        }
        try:
            if self.http_client is not None:
                response = await self.http_client.get(url, headers=headers)
            else:
                timeout = httpx.Timeout(
                    connect=self.settings.space_connect_timeout_seconds,
                    read=self.settings.space_read_timeout_seconds,
                    write=self.settings.space_read_timeout_seconds,
                    pool=self.settings.space_connect_timeout_seconds,
                )
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise MembershipAuthorityUnavailable("Space authority is unavailable") from exc
        if response.status_code == 404:
            return None
        try:
            response.raise_for_status()
            payload = response.json()
            role = payload["role"]
            version = payload["source_version"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise MembershipAuthorityUnavailable(
                "Space authority returned an invalid response"
            ) from exc
        if role not in {"owner", "admin", "member"} or not isinstance(version, str):
            raise MembershipAuthorityUnavailable(
                "Space authority returned an invalid membership"
            )
        return AuthorityMembership(role=role, source_version=version)
