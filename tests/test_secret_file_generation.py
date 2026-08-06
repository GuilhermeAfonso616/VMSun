from concurrent.futures import ThreadPoolExecutor

from app.core.config import _load_or_create_secret_file


def test_secret_file_generation_is_consistent_between_concurrent_process_starts(tmp_path):
    key_path = tmp_path / "shared" / "credential_key"
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _: _load_or_create_secret_file(key_path), range(16)))

    assert len(set(values)) == 1
    assert key_path.read_text(encoding="utf-8").strip() == values[0]
