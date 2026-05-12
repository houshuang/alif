import fcntl

from scripts import rotate_stale_sentences


def test_rotate_uses_shared_material_lock(tmp_path, monkeypatch):
    lock_path = tmp_path / "alif-update-material.lock"
    monkeypatch.setenv("ALIF_UPDATE_MATERIAL_LOCK", str(lock_path))
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    handle = lock_path.open("w")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert rotate_stale_sentences._try_acquire_material_update_lock() is None
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def test_rotate_material_lock_releases(tmp_path, monkeypatch):
    lock_path = tmp_path / "alif-update-material.lock"
    monkeypatch.setenv("ALIF_UPDATE_MATERIAL_LOCK", str(lock_path))

    handle = rotate_stale_sentences._try_acquire_material_update_lock()
    assert handle is not None
    rotate_stale_sentences._release_material_update_lock(handle)

    second = rotate_stale_sentences._try_acquire_material_update_lock()
    assert second is not None
    rotate_stale_sentences._release_material_update_lock(second)
