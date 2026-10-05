import base64
import os
import struct
import zlib

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_QWEN_TESTS") != "1",
    reason="Set RUN_LIVE_QWEN_TESTS=1 to call the configured Qwen proxy.",
)

@pytest.mark.asyncio
async def test_qwen_plus_accepts_user_and_tool_result_images() -> None:
    from app.config import Settings

    settings = Settings()
    assert settings.claude_model == "qwen3.7-plus"
    image = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(_png_image(16, 16)).decode("ascii"),
        },
    }
    headers = {
        "x-api-key": settings.anthropic_api_key.get_secret_value(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    url = f"{str(settings.anthropic_base_url).rstrip('/')}/v1/messages"
    async with httpx.AsyncClient(timeout=90) as client:
        direct = await client.post(
            url,
            headers=headers,
            json={
                "model": settings.claude_model,
                "max_tokens": 32,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Reply OK."},
                            image,
                        ],
                    }
                ],
            },
        )
        tool_result = await client.post(
            url,
            headers=headers,
            json={
                "model": settings.claude_model,
                "max_tokens": 32,
                "tools": [
                    {
                        "name": "inspect_image",
                        "description": "Returns an image.",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
                "messages": [
                    {"role": "user", "content": "Inspect the tool image."},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_live_image",
                                "name": "inspect_image",
                                "input": {},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_live_image",
                                "content": [image],
                            }
                        ],
                    },
                ],
            },
        )

    assert direct.status_code == 200, direct.text
    assert tool_result.status_code == 200, tool_result.text
    assert direct.json()["model"] == "qwen3.7-plus"
    assert tool_result.json()["model"] == "qwen3.7-plus"


def _png_image(width: int, height: int) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = b"".join(
        b"\x00" + (b"\x2f\x6f\x9f" * width) for _ in range(height)
    )
    return (
        signature
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(scanlines))
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)
