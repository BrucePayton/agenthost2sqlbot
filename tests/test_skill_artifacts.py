import os
import zipfile
from pathlib import Path

import pytest

VALID_SKILL_MD = b"---\nname: review\ndescription: Review safely\n---\nRun review.\n"


def _bundle():
    from app.skills.bundle import build_bundle

    return build_bundle(
        VALID_SKILL_MD,
        [("references/policy.bin", b"\x00\xffpolicy")],
    )


def test_artifact_round_trip_is_deterministic(tmp_path: Path) -> None:
    from app.skills.artifacts import (
        FilesystemSkillArtifactStore,
        build_skill_artifact,
        load_skill_artifact,
    )
    from app.skills.bundle import SkillBundleLimits

    bundle = _bundle()
    first = build_skill_artifact(bundle)
    second = build_skill_artifact(bundle)
    assert first.archive_bytes == second.archive_bytes
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.artifact_key == (
        f"skills/sha256/{bundle.bundle_hash[7:9]}/{bundle.bundle_hash[7:]}.zip"
    )

    store = FilesystemSkillArtifactStore(tmp_path / "artifacts")
    store.initialize()
    store.put(first)
    raw = store.read(first.artifact_key, maximum_size=50 * 1024 * 1024)
    assert (
        load_skill_artifact(
            raw,
            expected_artifact_sha256=first.artifact_sha256,
            expected_bundle_hash=bundle.bundle_hash,
            limits=SkillBundleLimits(),
        )
        == bundle
    )


def test_artifact_key_matcher_owns_bundle_relationship() -> None:
    from app.skills.artifacts import (
        artifact_key_matches_bundle,
        build_skill_artifact,
    )

    bundle = _bundle()
    artifact = build_skill_artifact(bundle)

    assert artifact_key_matches_bundle(artifact.artifact_key, bundle.bundle_hash)
    assert not artifact_key_matches_bundle("opaque-or-tampered-key", bundle.bundle_hash)
    assert not artifact_key_matches_bundle(artifact.artifact_key, "sha256:not-a-digest")


def test_artifact_zip_uses_pinned_deflate_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.skills.artifacts import build_skill_artifact

    original_get_compressor = zipfile._get_compressor
    compression_levels: list[int | None] = []

    def record_compression_level(compress_type: int, compresslevel: int | None = None):
        compression_levels.append(compresslevel)
        return original_get_compressor(compress_type, compresslevel)

    monkeypatch.setattr(zipfile, "_get_compressor", record_compression_level)

    build_skill_artifact(_bundle())

    assert compression_levels == [9, 9]


def test_artifact_rejects_wrong_archive_sha256() -> None:
    from app.errors import AppError
    from app.skills.artifacts import build_skill_artifact, load_skill_artifact
    from app.skills.bundle import SkillBundleLimits

    artifact = build_skill_artifact(_bundle())
    with pytest.raises(AppError) as exc_info:
        load_skill_artifact(
            artifact.archive_bytes,
            expected_artifact_sha256="sha256:" + "0" * 64,
            expected_bundle_hash=artifact.bundle_hash,
            limits=SkillBundleLimits(),
        )

    assert exc_info.value.code == "skill_artifact_corrupt"
    assert exc_info.value.status_code == 500


def test_artifact_rejects_wrong_bundle_hash() -> None:
    from app.errors import AppError
    from app.skills.artifacts import build_skill_artifact, load_skill_artifact
    from app.skills.bundle import SkillBundleLimits

    artifact = build_skill_artifact(_bundle())
    with pytest.raises(AppError) as exc_info:
        load_skill_artifact(
            artifact.archive_bytes,
            expected_artifact_sha256=artifact.artifact_sha256,
            expected_bundle_hash="sha256:" + "0" * 64,
            limits=SkillBundleLimits(),
        )

    assert exc_info.value.code == "skill_artifact_corrupt"


def test_filesystem_store_rejects_absolute_parent_and_backslash_keys(
    tmp_path: Path,
) -> None:
    from app.errors import AppError
    from app.skills.artifacts import FilesystemSkillArtifactStore

    store = FilesystemSkillArtifactStore(tmp_path / "artifacts")
    store.initialize()
    for key in (
        "/skills/sha256/00/" + "0" * 64 + ".zip",
        "../outside",
        "skills\\sha256\\00",
    ):
        with pytest.raises(AppError) as exc_info:
            store.exists(key)
        assert exc_info.value.code == "skill_artifact_corrupt"


def test_filesystem_store_detects_existing_corrupt_object(tmp_path: Path) -> None:
    from app.errors import AppError
    from app.skills.artifacts import FilesystemSkillArtifactStore, build_skill_artifact

    artifact = build_skill_artifact(_bundle())
    store = FilesystemSkillArtifactStore(tmp_path / "artifacts")
    store.initialize()
    target = tmp_path / "artifacts" / artifact.artifact_key
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt")

    with pytest.raises(AppError) as exc_info:
        store.put(artifact)

    assert exc_info.value.code == "skill_artifact_corrupt"


def test_filesystem_store_leaves_no_visible_partial_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.skills.artifacts import FilesystemSkillArtifactStore, build_skill_artifact

    artifact = build_skill_artifact(_bundle())
    store = FilesystemSkillArtifactStore(tmp_path / "artifacts")
    store.initialize()
    target = tmp_path / "artifacts" / artifact.artifact_key

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        store.put(artifact)

    assert not target.exists()
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_filesystem_store_refuses_symlink_root_and_symlink_parent(
    tmp_path: Path,
) -> None:
    from app.errors import AppError
    from app.skills.artifacts import FilesystemSkillArtifactStore, build_skill_artifact

    target = tmp_path / "target"
    target.mkdir()
    symlink_root = tmp_path / "symlink-root"
    symlink_root.symlink_to(target, target_is_directory=True)
    with pytest.raises(AppError) as root_error:
        FilesystemSkillArtifactStore(symlink_root).initialize()
    assert root_error.value.code == "skill_artifact_corrupt"

    artifact = build_skill_artifact(_bundle())
    store_root = tmp_path / "artifacts"
    store = FilesystemSkillArtifactStore(store_root)
    store.initialize()
    parent_target = tmp_path / "parent-target"
    parent_target.mkdir()
    (store_root / "skills").symlink_to(parent_target, target_is_directory=True)
    with pytest.raises(AppError) as parent_error:
        store.put(artifact)
    assert parent_error.value.code == "skill_artifact_corrupt"


def test_filesystem_store_bounds_reads_before_allocating_excess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.errors import AppError
    from app.skills.artifacts import FilesystemSkillArtifactStore, build_skill_artifact

    artifact = build_skill_artifact(_bundle())
    store = FilesystemSkillArtifactStore(tmp_path / "artifacts")
    store.initialize()
    store.put(artifact)

    def fail_read_bytes(self: Path) -> bytes:
        raise AssertionError("must not read oversized artifact")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)
    with pytest.raises(AppError) as exc_info:
        store.read(artifact.artifact_key, maximum_size=artifact.size_bytes - 1)

    assert exc_info.value.code == "skill_artifact_corrupt"
