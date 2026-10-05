import io
import lzma
import os
import stat
import zipfile

import pytest

VALID_SKILL_MD = b"---\nname: review\ndescription: Review changes\n---\n# Review\n"


def test_bundle_hash_is_stable_across_file_order() -> None:
    from app.skills.bundle import build_bundle

    first = build_bundle(VALID_SKILL_MD, [("b.bin", b"\x00\x01"), ("a.txt", b"a")])
    second = build_bundle(VALID_SKILL_MD, [("a.txt", b"a"), ("b.bin", b"\x00\x01")])

    assert first.bundle_hash == second.bundle_hash
    assert [file.path for file in first.files] == ["a.txt", "b.bin"]
    assert first.files[1].content == b"\x00\x01"
    assert first.files[1].mime_type == "application/octet-stream"


def test_bundle_hash_matches_the_canonical_golden_vector() -> None:
    from app.skills.bundle import build_bundle

    bundle = build_bundle(
        b"---\nname: golden\ndescription: Golden vector\n---\n# Golden\n",
        [("bin/data.bin", b"\x00\xff"), ("a.txt", b"A")],
    )

    assert bundle.bundle_hash == (
        "sha256:df4e71c99ae61657159b4bc1a7101f9a3ad543547b335820066a0a0948625a12"
    )
    assert bundle.files[0].sha256 == (
        "sha256:559aead08264d5795d3909718cdd05abd49572e84fe55590eef31a88a08fdffd"
    )
    assert bundle.files[1].sha256 == (
        "sha256:06eb7d6a69ee19e5fbdf749018d3d2abfa04bcbd1365db312eb86dc7169389b8"
    )


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a\\b", "a/./b", "a//b", "\x00bad"])
def test_bundle_rejects_unsafe_paths(path: str) -> None:
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    with pytest.raises(AppError) as exc_info:
        build_bundle(VALID_SKILL_MD, [(path, b"x")])

    assert exc_info.value.code == "invalid_skill_bundle"
    assert exc_info.value.status_code == 422


def test_bundle_rejects_paths_that_cannot_be_encoded_as_utf8() -> None:
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    with pytest.raises(AppError) as exc_info:
        build_bundle(VALID_SKILL_MD, [("bad\ud800path.txt", b"x")])

    assert exc_info.value.code == "invalid_skill_bundle"
    assert exc_info.value.status_code == 422


def test_bundle_rejects_duplicate_paths() -> None:
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    with pytest.raises(AppError) as exc_info:
        build_bundle(VALID_SKILL_MD, [("same.txt", b"one"), ("same.txt", b"two")])

    assert exc_info.value.code == "invalid_skill_bundle"


def test_bundle_rejects_file_directory_path_collisions() -> None:
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    with pytest.raises(AppError) as exc_info:
        build_bundle(
            VALID_SKILL_MD,
            [("references", b"not a directory"), ("references/note.txt", b"note")],
        )

    assert exc_info.value.code == "invalid_skill_bundle"


def test_bundle_rejects_invalid_markdown() -> None:
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    with pytest.raises(AppError) as exc_info:
        build_bundle(b"# no frontmatter\n", [])

    assert exc_info.value.code == "invalid_skill_bundle"


def test_bundle_errors_have_stable_status_codes() -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits, build_bundle

    with pytest.raises(AppError) as invalid_error:
        build_bundle(b"not markdown", [])
    assert invalid_error.value.status_code == 422

    with pytest.raises(AppError) as large_error:
        build_bundle(
            VALID_SKILL_MD,
            [("too-large.txt", b"xx")],
            SkillBundleLimits(max_file_bytes=1),
        )
    assert large_error.value.status_code == 413


def test_root_skill_markdown_is_exempt_from_file_limit_but_counts_toward_total() -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits, build_bundle

    root_only_limits = SkillBundleLimits(
        max_file_bytes=1, max_total_bytes=len(VALID_SKILL_MD), max_files=0
    )
    assert build_bundle(VALID_SKILL_MD, [], root_only_limits).name == "review"

    with pytest.raises(AppError) as total_error:
        build_bundle(
            VALID_SKILL_MD,
            [("support.txt", b"x")],
            root_only_limits,
        )
    assert total_error.value.code == "skill_bundle_too_large"
    assert total_error.value.status_code == 413


def test_max_files_counts_supporting_files_only() -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits, build_bundle

    limits = SkillBundleLimits(max_files=1)
    assert len(build_bundle(VALID_SKILL_MD, [("one.txt", b"1")], limits).files) == 1

    with pytest.raises(AppError) as exc_info:
        build_bundle(VALID_SKILL_MD, [("one.txt", b"1"), ("two.txt", b"2")], limits)
    assert exc_info.value.code == "skill_bundle_too_large"
    assert exc_info.value.status_code == 413


def test_frontmatter_limit_is_enforced_on_raw_bytes_before_yaml() -> None:
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    raw = b"---\n" + ("é".encode() * (32 * 1024 + 1)) + b"\n---\n# body\n"
    with pytest.raises(AppError) as exc_info:
        build_bundle(raw, [])

    assert exc_info.value.code == "invalid_skill_bundle"
    assert exc_info.value.status_code == 422


def test_archive_rejects_symlink() -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        link = zipfile.ZipInfo("references/link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, b"../../secret")

    with pytest.raises(AppError) as exc_info:
        load_bundle_from_archive(output.getvalue())

    assert exc_info.value.code == "invalid_skill_bundle"
    assert exc_info.value.status_code == 422


def test_archive_rejects_uncompressed_limit() -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits, load_bundle_from_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        archive.writestr("large.bin", b"0" * 1025)

    with pytest.raises(AppError) as exc_info:
        load_bundle_from_archive(
            output.getvalue(), SkillBundleLimits(max_total_bytes=1024)
        )

    assert exc_info.value.code == "skill_bundle_too_large"
    assert exc_info.value.status_code == 413


def test_archive_rejects_duplicate_skill_markdown_and_unsafe_paths() -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_archive

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("SKILL.md", VALID_SKILL_MD)
    with pytest.raises(AppError) as duplicate_error:
        load_bundle_from_archive(duplicate.getvalue())
    assert duplicate_error.value.code == "invalid_skill_bundle"

    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        archive.writestr("../outside.txt", b"no")
    with pytest.raises(AppError) as unsafe_error:
        load_bundle_from_archive(unsafe.getvalue())
    assert unsafe_error.value.code == "invalid_skill_bundle"


def test_archive_rejects_spoofed_unix_symlink_mode_and_nul_original_name() -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_archive

    spoofed_mode = io.BytesIO()
    with zipfile.ZipFile(spoofed_mode, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        link = zipfile.ZipInfo("link")
        link.create_system = 0
        link.external_attr = 0o120777 << 16
        archive.writestr(link, b"target")
    with pytest.raises(AppError) as mode_error:
        load_bundle_from_archive(spoofed_mode.getvalue())
    assert mode_error.value.code == "invalid_skill_bundle"
    assert mode_error.value.status_code == 422

    nul_name = io.BytesIO()
    with zipfile.ZipFile(nul_name, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        archive.writestr("bad0xxx", b"hidden")
    raw_with_nul_name = nul_name.getvalue().replace(b"bad0xxx", b"bad\x00xxx")
    with pytest.raises(AppError) as nul_error:
        load_bundle_from_archive(raw_with_nul_name)
    assert nul_error.value.code == "invalid_skill_bundle"
    assert nul_error.value.status_code == 422


def test_archive_rejects_dos_directory_metadata_without_a_trailing_slash() -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        directory = zipfile.ZipInfo("dos-directory-without-slash")
        directory.create_system = 0
        directory.external_attr = 0x10
        archive.writestr(directory, b"")

    with pytest.raises(AppError) as exc_info:
        load_bundle_from_archive(output.getvalue())
    assert exc_info.value.code == "invalid_skill_bundle"
    assert exc_info.value.status_code == 422


def test_archive_normalizes_unsupported_compression_to_invalid_bundle(monkeypatch) -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)

    def unsupported_open(self, name, *args, **kwargs):
        raise NotImplementedError("unsupported compression")

    monkeypatch.setattr(zipfile.ZipFile, "open", unsupported_open)
    with pytest.raises(AppError) as exc_info:
        load_bundle_from_archive(output.getvalue())
    assert exc_info.value.code == "invalid_skill_bundle"
    assert exc_info.value.status_code == 422


def test_archive_normalizes_lzma_reader_failure_to_invalid_bundle(monkeypatch) -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_LZMA) as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)

    def corrupt_lzma_open(self, name, *args, **kwargs):
        raise lzma.LZMAError("corrupt lzma stream")

    monkeypatch.setattr(zipfile.ZipFile, "open", corrupt_lzma_open)
    with pytest.raises(AppError) as exc_info:
        load_bundle_from_archive(output.getvalue())
    assert exc_info.value.code == "invalid_skill_bundle"
    assert exc_info.value.status_code == 422


def test_archive_rejects_a_path_below_root_skill_markdown() -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        archive.writestr("SKILL.md/nested.txt", b"not possible on disk")

    with pytest.raises(AppError) as exc_info:
        load_bundle_from_archive(output.getvalue())

    assert exc_info.value.code == "invalid_skill_bundle"


def test_archive_rejects_supporting_file_count_and_file_size_limits() -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits, load_bundle_from_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        archive.writestr("one.txt", b"xx")
        archive.writestr("two.txt", b"2")

    assert len(
        load_bundle_from_archive(output.getvalue(), SkillBundleLimits(max_files=2)).files
    ) == 2
    with pytest.raises(AppError) as count_error:
        load_bundle_from_archive(output.getvalue(), SkillBundleLimits(max_files=1))
    assert count_error.value.code == "skill_bundle_too_large"
    assert count_error.value.status_code == 413

    with pytest.raises(AppError) as size_error:
        load_bundle_from_archive(output.getvalue(), SkillBundleLimits(max_file_bytes=1))
    assert size_error.value.code == "skill_bundle_too_large"
    assert size_error.value.status_code == 413


def test_archive_rejects_actual_content_larger_than_declared_size(monkeypatch) -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits, load_bundle_from_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        archive.writestr("one-byte.txt", b"x")
    original_open = zipfile.ZipFile.open

    def inflated_open(self, name, *args, **kwargs):
        info = name if isinstance(name, zipfile.ZipInfo) else self.getinfo(name)
        if info.filename == "one-byte.txt":
            return io.BytesIO(b"xx")
        return original_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", inflated_open)
    with pytest.raises(AppError) as exc_info:
        load_bundle_from_archive(
            output.getvalue(), SkillBundleLimits(max_file_bytes=1)
        )
    assert exc_info.value.code == "skill_bundle_too_large"
    assert exc_info.value.status_code == 413


def test_directory_rejects_a_symlink_and_loads_regular_files(tmp_path) -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_directory

    (tmp_path / "SKILL.md").write_bytes(VALID_SKILL_MD)
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "note.txt").write_bytes(b"note")
    bundle = load_bundle_from_directory(tmp_path)
    assert [file.path for file in bundle.files] == ["references/note.txt"]

    os.symlink(tmp_path / "references" / "note.txt", tmp_path / "link.txt")
    with pytest.raises(AppError) as exc_info:
        load_bundle_from_directory(tmp_path)
    assert exc_info.value.code == "invalid_skill_bundle"


def test_directory_fails_closed_when_fd_listdir_is_unsupported(tmp_path, monkeypatch) -> None:
    import app.skills.bundle as bundle_module
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_directory

    (tmp_path / "SKILL.md").write_bytes(VALID_SKILL_MD)
    monkeypatch.setattr(bundle_module.os, "supports_fd", set())

    with pytest.raises(AppError) as exc_info:
        load_bundle_from_directory(tmp_path)
    assert exc_info.value.code == "invalid_skill_bundle"
    assert exc_info.value.status_code == 422


def test_directory_rejects_a_symlink_ancestor_and_hard_link(tmp_path) -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_directory

    real_parent = tmp_path / "real-parent"
    root = real_parent / "bundle"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_bytes(VALID_SKILL_MD)
    linked_parent = tmp_path / "linked-parent"
    os.symlink(real_parent, linked_parent)
    with pytest.raises(AppError) as ancestor_error:
        load_bundle_from_directory(linked_parent / "bundle")
    assert ancestor_error.value.code == "invalid_skill_bundle"
    assert ancestor_error.value.status_code == 422

    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"hard link")
    os.link(outside, root / "hard-link.txt")
    with pytest.raises(AppError) as link_error:
        load_bundle_from_directory(root)
    assert link_error.value.code == "invalid_skill_bundle"
    assert link_error.value.status_code == 422


def test_directory_rejects_non_regular_files(tmp_path) -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_directory

    (tmp_path / "SKILL.md").write_bytes(VALID_SKILL_MD)
    fifo = tmp_path / "not-a-file"
    os.mkfifo(fifo)
    assert stat.S_ISFIFO(fifo.lstat().st_mode)

    with pytest.raises(AppError) as exc_info:
        load_bundle_from_directory(tmp_path)
    assert exc_info.value.code == "invalid_skill_bundle"


def test_uploaded_directory_strips_one_root_and_preserves_binary_files() -> None:
    from app.skills.bundle import load_bundle_from_uploaded_files

    uploaded = load_bundle_from_uploaded_files(
        [
            (
                "brainstorming/SKILL.md",
                b"---\nname: brainstorming\ndescription: Generate ideas\n---\nBody\n",
            ),
            ("brainstorming/scripts/run.py", b"print('ok')\n"),
            ("brainstorming/assets/icon.bin", b"\x00\xff"),
        ]
    )

    assert uploaded.source_name == "brainstorming"
    assert uploaded.bundle.name == "brainstorming"
    assert [(item.path, item.content) for item in uploaded.bundle.files] == [
        ("assets/icon.bin", b"\x00\xff"),
        ("scripts/run.py", b"print('ok')\n"),
    ]


@pytest.mark.parametrize(
    "paths",
    [
        ["SKILL.md"],
        ["one/SKILL.md", "two/script.py"],
        ["one/nested/SKILL.md"],
        ["one/../SKILL.md"],
        ["one\\SKILL.md"],
        ["/one/SKILL.md"],
        ["one/SKILL.md", "one/SKILL.md"],
        ["one/SKILL.md", "one/scripts", "one/scripts/run.py"],
    ],
)
def test_uploaded_directory_rejects_invalid_roots_and_paths(paths: list[str]) -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_uploaded_files

    entries = [
        (
            path,
            b"---\nname: uploaded\ndescription: Uploaded\n---\nBody\n"
            if path.endswith("SKILL.md")
            else b"support",
        )
        for path in paths
    ]
    with pytest.raises(AppError) as exc_info:
        load_bundle_from_uploaded_files(entries)
    assert (exc_info.value.status_code, exc_info.value.code) == (
        422,
        "invalid_skill_bundle",
    )


def test_uploaded_directory_enforces_exact_file_and_total_limits() -> None:
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits, load_bundle_from_uploaded_files

    exact_limits = SkillBundleLimits(
        max_file_bytes=4,
        max_total_bytes=len(VALID_SKILL_MD) + 4,
        max_files=1,
    )
    uploaded = load_bundle_from_uploaded_files(
        [("skill/SKILL.md", VALID_SKILL_MD), ("skill/note.txt", b"1234")],
        exact_limits,
    )
    assert uploaded.bundle.files[0].content == b"1234"

    invalid_entries = [
        [("skill/SKILL.md", VALID_SKILL_MD), ("skill/note.txt", b"12345")],
        [
            ("skill/SKILL.md", VALID_SKILL_MD),
            ("skill/one.txt", b"1"),
            ("skill/two.txt", b"2"),
        ],
    ]
    for entries in invalid_entries:
        with pytest.raises(AppError) as exc_info:
            load_bundle_from_uploaded_files(entries, exact_limits)
        assert (exc_info.value.status_code, exc_info.value.code) == (
            413,
            "skill_bundle_too_large",
        )


def test_rename_bundle_changes_only_uploaded_identity_and_rehashes() -> None:
    from app.skills.bundle import build_bundle, rename_bundle

    original = build_bundle(
        b"---\nname: original\ndescription: Keep this text\nmetadata: kept\n---\n# Body\n",
        [("references/policy.bin", b"\x00\xff")],
    )
    renamed = rename_bundle(original, "renamed-copy")

    assert original.name == "original"
    assert renamed.name == "renamed-copy"
    assert renamed.description == original.description
    assert "metadata: kept" in renamed.content
    assert renamed.content.endswith("# Body\n")
    assert renamed.files == original.files
    assert renamed.bundle_hash != original.bundle_hash


@pytest.mark.parametrize("target_name", ["", "   ", "bad/name", "a" * 129])
def test_rename_bundle_rejects_invalid_target_name(target_name: str) -> None:
    from app.errors import AppError
    from app.skills.bundle import build_bundle, rename_bundle

    original = build_bundle(VALID_SKILL_MD, [])
    with pytest.raises(AppError) as exc_info:
        rename_bundle(original, target_name)
    assert (exc_info.value.status_code, exc_info.value.code) == (
        422,
        "invalid_skill_bundle",
    )
