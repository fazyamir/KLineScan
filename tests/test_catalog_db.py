from klinescan.core.catalog import CatalogDatabase


def test_catalog_import_is_idempotent_and_searchable(tmp_path):
    pack = {
        "pack_id": "toyota",
        "name": "Toyota",
        "version": "1.0",
        "region": "Global",
        "vehicles": [{
            "label": "Toyota Corolla",
            "make": "Toyota",
            "model": "Corolla",
            "year": "2002-2007",
            "targets": [{
                "name": "Engine ECU",
                "protocol": "ISO 9141-2 (5-Baud Slow Init)",
                "baudrate": 10400,
                "pin": 7,
                "ecu_addr": 16,
                "tester_addr": 240,
            }],
        }],
    }
    db = CatalogDatabase(tmp_path / "catalog.sqlite3")
    assert db.import_pack(pack, "pack.json") == 1
    assert db.import_pack(pack, "pack.json") == 1
    rows = db.search_variants("corolla", make="Toyota")
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Toyota Corolla"
    assert db.connection.execute("SELECT count(*) FROM vehicle_variant").fetchone()[0] == 1
    db.close()
