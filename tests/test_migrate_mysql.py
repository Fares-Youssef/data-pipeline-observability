from scripts.migrate import mysql_statement


def test_add_column_translated_or_skipped():
    sql = "ALTER TABLE t\n    ADD COLUMN IF NOT EXISTS c INT NULL AFTER b"
    assert mysql_statement(sql, lambda *a: False) == "ALTER TABLE t\n    ADD COLUMN c INT NULL AFTER b"
    assert mysql_statement(sql, lambda *a: True) is None


def test_create_index_translated_or_skipped():
    sql = "CREATE UNIQUE INDEX IF NOT EXISTS ux ON t (a, b)"
    seen = []
    assert mysql_statement(sql, lambda *a: seen.append(a) or False) == "CREATE UNIQUE INDEX ux ON t (a, b)"
    assert seen == [("index", "t", "ux")]
    assert mysql_statement(sql, lambda *a: True) is None


def test_geometry_column_gets_srid():
    sql = "CREATE TABLE IF NOT EXISTS g (\n    geometry GEOMETRY NOT NULL,\n    x INT)"
    assert "geometry GEOMETRY NOT NULL SRID 4326," in mysql_statement(sql, lambda *a: False)
