from klinescan.core.pack_manager import PackManager


def test_pack_manager_builds_runtime_catalog():
    manager = PackManager()
    rows = manager.catalog.search_variants("toyota")
    assert rows
    assert rows[0]["pack_id"] == "toyota_honda"
