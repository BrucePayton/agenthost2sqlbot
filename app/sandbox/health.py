from typing import Protocol

import httpx


class BackendHealthProbe(Protocol):
    async def ready(self) -> bool: ...


class OpenSandboxHealthProbe:
    def __init__(
        self,
        api_url: str,
        *,
        timeout_seconds: float = 2.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._health_url = api_url.rstrip("/") + "/health"
        self._client = client
        self._timeout = timeout_seconds

    async def ready(self) -> bool:
        try:
            if self._client is not None:
                response = await self._client.get(
                    self._health_url,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(self._health_url)
            return response.status_code == 200
        except httpx.HTTPError:
            return False
