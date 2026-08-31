"""department_prefix filtering (the dashboard's cascading department
select, "all departments under this root" case) -- see
web/backend/queries.py:list_deals and build_department_hierarchy."""

from __future__ import annotations

from adapters.base import Department, ProductRef
from scanner.orchestrator import run_scan
from web.backend import queries
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def _seed_electrical_and_plumbing(postgres_conn):
    electrical = Department(retailer_department_id="dept-electrical", name="Electrical")
    batteries = Department(retailer_department_id="dept-batteries", name="Electrical Batteries")
    aa = Department(retailer_department_id="dept-aa", name="Electrical Batteries AA Batteries")
    plumbing = Department(retailer_department_id="dept-plumbing", name="Plumbing")

    adapter = ConfigurableFakeAdapter(
        departments=[electrical, batteries, aa, plumbing],
        products_by_department={
            "dept-electrical": [ProductRef(retailer_product_id="sku-1", name="Wire", department=electrical)],
            "dept-batteries": [ProductRef(retailer_product_id="sku-2", name="9V Battery", department=batteries)],
            "dept-aa": [ProductRef(retailer_product_id="sku-3", name="AA 4-Pack", department=aa)],
            "dept-plumbing": [ProductRef(retailer_product_id="sku-4", name="PVC Pipe", department=plumbing)],
        },
    )
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")


def test_department_prefix_includes_root_and_all_descendants(postgres_conn):
    _seed_electrical_and_plumbing(postgres_conn)

    rows = queries.list_deals(postgres_conn, department_prefix="Electrical")

    names = {r["product_name"] for r in rows}
    assert names == {"Wire", "9V Battery", "AA 4-Pack"}


def test_department_prefix_does_not_match_unrelated_department(postgres_conn):
    _seed_electrical_and_plumbing(postgres_conn)

    rows = queries.list_deals(postgres_conn, department_prefix="Electrical")

    names = {r["product_name"] for r in rows}
    assert "PVC Pipe" not in names


def test_department_id_exact_match_still_works_alongside_prefix_support(postgres_conn):
    _seed_electrical_and_plumbing(postgres_conn)
    dept_row = postgres_conn.execute(
        "SELECT id FROM department WHERE name = 'Electrical Batteries AA Batteries'"
    ).fetchone()

    rows = queries.list_deals(postgres_conn, department_id=dept_row["id"])

    assert {r["product_name"] for r in rows} == {"AA 4-Pack"}
