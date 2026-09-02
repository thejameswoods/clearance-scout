"""common/db.py's explicit departments-to-watch selection -- a
watched_department row means "this department, and everything under it,
is watched" (see get_watched_department_names's docstring). Replaces the
old flat, substring-matched watched_departments text field."""

from __future__ import annotations

from common import db


def _seed_electrical_tree(conn, retailer_id):
    # Flattened breadcrumb names, same convention as adapters/home_depot's
    # own department naming (see common/departments.py).
    electrical = db.upsert_department(conn, retailer_id, "electrical", "Electrical", None)
    batteries = db.upsert_department(conn, retailer_id, "electrical-batteries", "Electrical Batteries", None)
    aa = db.upsert_department(conn, retailer_id, "electrical-batteries-aa", "Electrical Batteries AA", None)
    wire = db.upsert_department(conn, retailer_id, "electrical-wire", "Electrical Wire", None)
    plumbing = db.upsert_department(conn, retailer_id, "plumbing", "Plumbing", None)
    return {"electrical": electrical, "batteries": batteries, "aa": aa, "wire": wire, "plumbing": plumbing}


def test_no_watched_rows_means_watch_everything(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    _seed_electrical_tree(postgres_conn, retailer_id)

    assert db.get_watched_department_names(postgres_conn, retailer_id) is None


def test_watching_a_leaf_only_includes_itself(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    ids = _seed_electrical_tree(postgres_conn, retailer_id)
    db.set_watched_departments(postgres_conn, retailer_id, [ids["wire"]])

    assert db.get_watched_department_names(postgres_conn, retailer_id) == {"Electrical Wire"}


def test_watching_a_parent_includes_all_descendants(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    ids = _seed_electrical_tree(postgres_conn, retailer_id)
    db.set_watched_departments(postgres_conn, retailer_id, [ids["batteries"]])

    names = db.get_watched_department_names(postgres_conn, retailer_id)

    assert names == {"Electrical Batteries", "Electrical Batteries AA"}
    assert "Electrical" not in names  # the parent of the watched node isn't included
    assert "Electrical Wire" not in names  # a sibling isn't included
    assert "Plumbing" not in names


def test_set_watched_departments_replaces_the_full_selection(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    ids = _seed_electrical_tree(postgres_conn, retailer_id)
    db.set_watched_departments(postgres_conn, retailer_id, [ids["wire"]])

    db.set_watched_departments(postgres_conn, retailer_id, [ids["plumbing"]])  # replace, not add

    assert db.get_watched_department_names(postgres_conn, retailer_id) == {"Plumbing"}


def test_set_watched_departments_to_empty_list_goes_back_to_watch_everything(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    ids = _seed_electrical_tree(postgres_conn, retailer_id)
    db.set_watched_departments(postgres_conn, retailer_id, [ids["wire"]])

    db.set_watched_departments(postgres_conn, retailer_id, [])

    assert db.get_watched_department_names(postgres_conn, retailer_id) is None


def test_watched_departments_are_scoped_per_retailer(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    other_id = db.upsert_retailer(postgres_conn, "other_retailer", "Other Retailer", "https://example.invalid")
    ids = _seed_electrical_tree(postgres_conn, retailer_id)
    db.set_watched_departments(postgres_conn, retailer_id, [ids["wire"]])

    assert db.get_watched_department_names(postgres_conn, other_id) is None
