import os
from savemanager.store import new_version_id, game_dir, version_dir, atomic_copy


def test_new_version_id_format():
    assert new_version_id(1718600000000, "a1b2c3") == "v_1718600000000_a1b2c3"


def test_dir_helpers():
    assert game_dir("/data", 281990).endswith(os.path.join("games", "281990"))
    assert version_dir("/data", 281990, "v_1_x").endswith(
        os.path.join("games", "281990", "versions", "v_1_x")
    )


def test_atomic_copy_copies_content_and_creates_dirs(tmp_path):
    src = os.path.join(str(tmp_path), "src.bin")
    with open(src, "wb") as f:
        f.write(b"\x00\x01\x02hello")
    dst = os.path.join(str(tmp_path), "nested", "deep", "out.bin")
    atomic_copy(src, dst)
    with open(dst, "rb") as f:
        assert f.read() == b"\x00\x01\x02hello"
    assert not os.path.exists(dst + ".tmp")
