from app.runtime import inference, inference_pool


def test_legacy_inference_module_reexports_pool_contracts():
    assert inference.InferencePool is inference_pool.InferencePool
    assert inference.InferencePoolGroup is inference_pool.InferencePoolGroup
    assert inference.get_inference_pool is inference_pool.get_inference_pool
    assert inference.get_inference_pool_group is inference_pool.get_inference_pool_group


def test_pool_reset_releases_existing_services_without_creating_singletons(
    monkeypatch,
):
    calls = []

    class Resettable:
        def reset_services(self):
            calls.append(self)

    standalone = Resettable()
    group = Resettable()
    monkeypatch.setattr(inference_pool, "_POOL", standalone)
    monkeypatch.setattr(inference_pool, "_POOL_GROUP", group)

    inference_pool.reset_inference_pool_services()

    assert calls == [group, standalone]
    assert inference_pool._POOL is standalone
    assert inference_pool._POOL_GROUP is group
