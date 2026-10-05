from collections.abc import Iterable

import pytest
from starlette.requests import Request

Part = tuple[str, str | None, bytes, tuple[tuple[str, str], ...]]


def multipart_body(boundary: bytes, parts: Iterable[Part]) -> bytes:
    output = bytearray()
    for field_name, filename, content, extra_headers in parts:
        output.extend(b"--" + boundary + b"\r\n")
        disposition = f'Content-Disposition: form-data; name="{field_name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        output.extend(disposition.encode("utf-8") + b"\r\n")
        for name, value in extra_headers:
            output.extend(f"{name}: {value}\r\n".encode())
        output.extend(b"\r\n")
        output.extend(content)
        output.extend(b"\r\n")
    output.extend(b"--" + boundary + b"--\r\n")
    return bytes(output)


def field(name: str, value: str) -> Part:
    return (name, None, value.encode("utf-8"), ())


def uploaded_file(path: str, content: bytes, headers=()) -> Part:
    return ("files", path, content, tuple(headers))


def archive_file(filename: str, content: bytes, headers=()) -> Part:
    return ("archive", filename, content, tuple(headers))


async def parse_directory_request(
    chunks: list[bytes],
    limits,
    *,
    boundary: bytes = b"directory-boundary",
):
    from app.skills.uploads import read_multipart_directory

    position = 0

    async def receive():
        nonlocal position
        if position >= len(chunks):
            return {"type": "http.disconnect"}
        content = chunks[position]
        position += 1
        return {
            "type": "http.request",
            "body": content,
            "more_body": position < len(chunks),
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": [
                (
                    b"content-type",
                    b"multipart/form-data; boundary=" + boundary,
                )
            ],
        },
        receive,
    )
    result = await read_multipart_directory(request, limits)
    return result, position


async def parse_archive_request(
    chunks: list[bytes],
    maximum_size: int,
    *,
    boundary: bytes = b"archive-boundary",
):
    from app.skills.uploads import read_multipart_archive

    position = 0

    async def receive():
        nonlocal position
        if position >= len(chunks):
            return {"type": "http.disconnect"}
        content = chunks[position]
        position += 1
        return {
            "type": "http.request",
            "body": content,
            "more_body": position < len(chunks),
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": [
                (
                    b"content-type",
                    b"multipart/form-data; boundary=" + boundary,
                )
            ],
        },
        receive,
    )
    result = await read_multipart_archive(request, maximum_size)
    return result, position


@pytest.mark.asyncio
async def test_archive_multipart_returns_bytes_and_conflict_fields() -> None:
    boundary = b"archive-fields"
    body = multipart_body(
        boundary,
        [
            field("on_conflict", "overwrite"),
            field("expected_hash", "sha256:" + "a" * 64),
            archive_file("skill.zip", b"archive"),
        ],
    )

    upload, chunks_read = await parse_archive_request(
        [body], 1024, boundary=boundary
    )

    assert upload.raw == b"archive"
    assert upload.on_conflict == "overwrite"
    assert upload.expected_hash == "sha256:" + "a" * 64
    assert upload.target_name is None
    assert chunks_read == 1


@pytest.mark.asyncio
async def test_archive_multipart_defaults_to_fail_policy() -> None:
    boundary = b"archive-default"
    upload, _ = await parse_archive_request(
        [multipart_body(boundary, [archive_file("skill.skill", b"archive")])],
        1024,
        boundary=boundary,
    )

    assert upload.raw == b"archive"
    assert upload.on_conflict == "fail"
    assert upload.expected_hash is None
    assert upload.target_name is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parts",
    [
        [field("unknown", "value"), archive_file("skill.zip", b"archive")],
        [
            field("on_conflict", "fail"),
            field("on_conflict", "fail"),
            archive_file("skill.zip", b"archive"),
        ],
        [field("on_conflict", "invalid"), archive_file("skill.zip", b"archive")],
        [("expected_hash", None, b"\xff", ()), archive_file("skill.zip", b"archive")],
        [archive_file("first.zip", b"one"), archive_file("second.zip", b"two")],
    ],
)
async def test_archive_multipart_rejects_malformed_and_duplicate_controls(
    parts: list[Part],
) -> None:
    from app.errors import AppError

    boundary = b"archive-invalid-controls"
    with pytest.raises(AppError) as exc_info:
        await parse_archive_request(
            [multipart_body(boundary, parts)], 1024, boundary=boundary
        )

    assert (exc_info.value.status_code, exc_info.value.code) == (
        422,
        "invalid_skill_bundle",
    )


@pytest.mark.asyncio
async def test_archive_multipart_bounds_control_field_before_reading_tail() -> None:
    from app.errors import AppError

    boundary = b"archive-large-control"
    body = multipart_body(
        boundary,
        [
            field("target_name", "x" * 1025),
            archive_file("skill.zip", b"archive"),
        ],
    )
    with pytest.raises(AppError) as exc_info:
        await parse_archive_request(
            [body, b"unconsumed-tail"], 1024, boundary=boundary
        )

    assert (exc_info.value.status_code, exc_info.value.code) == (
        413,
        "skill_bundle_too_large",
    )


@pytest.mark.asyncio
async def test_directory_multipart_returns_files_and_conflict_fields() -> None:
    from app.skills.bundle import SkillBundleLimits

    boundary = b"success"
    body = multipart_body(
        boundary,
        [
            field("on_conflict", "overwrite"),
            field("expected_hash", "sha256:" + "a" * 64),
            uploaded_file("brainstorming/SKILL.md", b"skill"),
            uploaded_file("brainstorming/scripts/run.py", b"print('ok')"),
        ],
    )
    upload, chunks_read = await parse_directory_request(
        [body], SkillBundleLimits(), boundary=boundary
    )

    assert upload.files == (
        ("brainstorming/SKILL.md", b"skill"),
        ("brainstorming/scripts/run.py", b"print('ok')"),
    )
    assert upload.on_conflict == "overwrite"
    assert upload.expected_hash == "sha256:" + "a" * 64
    assert upload.target_name is None
    assert chunks_read == 1


@pytest.mark.asyncio
async def test_directory_multipart_defaults_to_fail_policy() -> None:
    from app.skills.bundle import SkillBundleLimits

    boundary = b"default-policy"
    upload, _ = await parse_directory_request(
        [
            multipart_body(
                boundary,
                [uploaded_file("brainstorming/SKILL.md", b"skill")],
            )
        ],
        SkillBundleLimits(),
        boundary=boundary,
    )
    assert upload.on_conflict == "fail"
    assert upload.expected_hash is None
    assert upload.target_name is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parts",
    [
        [field("unknown", "value"), uploaded_file("skill/SKILL.md", b"skill")],
        [
            field("on_conflict", "fail"),
            field("on_conflict", "fail"),
            uploaded_file("skill/SKILL.md", b"skill"),
        ],
        [("files", None, b"not-a-file", ())],
        [("archive", "skill/SKILL.md", b"skill", ())],
        [uploaded_file("skill/../SKILL.md", b"skill")],
    ],
)
async def test_directory_multipart_rejects_unknown_duplicate_and_unsafe_parts(
    parts: list[Part],
) -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits

    boundary = b"invalid-parts"
    with pytest.raises(AppError) as exc_info:
        await parse_directory_request(
            [multipart_body(boundary, parts)],
            SkillBundleLimits(),
            boundary=boundary,
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        422,
        "invalid_skill_bundle",
    )


@pytest.mark.asyncio
async def test_directory_multipart_accepts_exact_file_count_and_byte_limits() -> None:
    from app.skills.bundle import SkillBundleLimits

    boundary = b"exact-limits"
    upload, _ = await parse_directory_request(
        [
            multipart_body(
                boundary,
                [
                    uploaded_file("skill/SKILL.md", b"root"),
                    uploaded_file("skill/note.txt", b"1234"),
                ],
            )
        ],
        SkillBundleLimits(max_file_bytes=4, max_total_bytes=8, max_files=1),
        boundary=boundary,
    )
    assert upload.files == (
        ("skill/SKILL.md", b"root"),
        ("skill/note.txt", b"1234"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parts", "limits"),
    [
        (
            [
                uploaded_file("skill/SKILL.md", b"root"),
                uploaded_file("skill/note.txt", b"12345"),
            ],
            (4, 20, 1),
        ),
        (
            [
                uploaded_file("skill/SKILL.md", b"root!"),
                uploaded_file("skill/note.txt", b"1234"),
            ],
            (4, 8, 1),
        ),
        (
            [
                uploaded_file("skill/SKILL.md", b"root"),
                uploaded_file("skill/one.txt", b"1"),
                uploaded_file("skill/two.txt", b"2"),
            ],
            (4, 20, 1),
        ),
    ],
)
async def test_directory_multipart_rejects_limits_before_reading_tail(
    parts: list[Part], limits: tuple[int, int, int]
) -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits

    boundary = b"limit-error"
    body = multipart_body(boundary, parts)
    chunks = [body, b"unconsumed-tail"]
    with pytest.raises(AppError) as exc_info:
        await parse_directory_request(
            chunks,
            SkillBundleLimits(
                max_file_bytes=limits[0],
                max_total_bytes=limits[1],
                max_files=limits[2],
            ),
            boundary=boundary,
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        413,
        "skill_bundle_too_large",
    )


@pytest.mark.asyncio
async def test_directory_multipart_stops_after_closing_boundary() -> None:
    from app.skills.bundle import SkillBundleLimits

    boundary = b"closed"
    body = multipart_body(
        boundary, [uploaded_file("skill/SKILL.md", b"skill")]
    )
    upload, chunks_read = await parse_directory_request(
        [body, b"tail-one", b"tail-two"],
        SkillBundleLimits(),
        boundary=boundary,
    )
    assert upload.files == (("skill/SKILL.md", b"skill"),)
    assert chunks_read == 1


@pytest.mark.asyncio
async def test_directory_multipart_bounds_preamble_and_headers() -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits

    boundary = b"overhead"
    cases = [
        [b"p" * (64 * 1024 + 1), b"unconsumed-tail"],
        [
            multipart_body(
                boundary,
                [
                    uploaded_file(
                        "skill/SKILL.md",
                        b"skill",
                        headers=(("X-Large", "x" * (8 * 1024 + 1)),),
                    )
                ],
            ),
            b"unconsumed-tail",
        ],
    ]
    for chunks in cases:
        with pytest.raises(AppError) as exc_info:
            await parse_directory_request(
                chunks, SkillBundleLimits(), boundary=boundary
            )
        assert (exc_info.value.status_code, exc_info.value.code) == (
            413,
            "skill_bundle_too_large",
        )


@pytest.mark.asyncio
async def test_directory_multipart_bounds_aggregate_headers() -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits

    boundary = b"aggregate-headers"
    long_paths = [
        uploaded_file(f"skill/{index:02d}-{'x' * 1400}.txt", b"x")
        for index in range(30)
    ]
    body = multipart_body(
        boundary,
        [uploaded_file("skill/SKILL.md", b"root"), *long_paths],
    )
    with pytest.raises(AppError) as exc_info:
        await parse_directory_request(
            [body, b"unconsumed-tail"],
            SkillBundleLimits(max_file_bytes=1, max_total_bytes=34, max_files=30),
            boundary=boundary,
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        413,
        "skill_bundle_too_large",
    )


@pytest.mark.asyncio
async def test_directory_multipart_bounds_headers_per_part() -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits

    boundary = b"header-count"
    body = multipart_body(
        boundary,
        [
            uploaded_file(
                "skill/SKILL.md",
                b"root",
                headers=tuple((f"X-{index}", "value") for index in range(32)),
            )
        ],
    )
    with pytest.raises(AppError) as exc_info:
        await parse_directory_request(
            [body, b"unconsumed-tail"],
            SkillBundleLimits(),
            boundary=boundary,
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        413,
        "skill_bundle_too_large",
    )
