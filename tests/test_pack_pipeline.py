from klinescan.core.pack_pipeline import apply_overrides, diff_packs, target_id, vehicle_id


def _pack():
    return {"pack_id": "demo", "vehicles": [{
        "label": "Toyota Corolla", "make": "Toyota", "model": "Corolla", "year": "2002",
        "targets": [{"name": "Engine ECU", "protocol": "CAN", "ecu_addr": 2016}],
    }]}


def test_ids_and_overrides_are_stable():
    pack = _pack()
    vehicle = pack["vehicles"][0]
    target = vehicle["targets"][0]
    vid = vehicle_id("demo", vehicle)
    tid = target_id("demo", vehicle, target)
    patched = apply_overrides(pack, [{"target_id": tid, "set": {"confidence": "curated"}}])
    assert patched["vehicles"][0]["targets"][0]["confidence"] == "curated"
    assert vehicle_id("demo", vehicle) == vid


def test_diff_reports_added_changed_removed():
    old = _pack()
    new = _pack()
    new["vehicles"][0]["targets"][0]["baudrate"] = 500000
    new["vehicles"].append({"label": "Honda Civic", "make": "Honda", "model": "Civic", "year": "2003", "targets": []})
    report = diff_packs(old, new)
    assert report["summary"]["changed"] == 2  # variant plus its changed target
    assert report["summary"]["added"] == 1
