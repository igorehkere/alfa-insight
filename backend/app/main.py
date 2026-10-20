from fastapi import Depends, FastAPI, HTTPException, Query
from psycopg import Connection

from app.connect import connect

app = FastAPI(title="Company Data API", version="0.1.0")


def as_flag(value: str | None) -> bool | None:
    if value in ("1", "1.0"):
        return True
    if value in ("0", "0.0"):
        return False
    return None


def company_features(profile: dict) -> dict:
    """Исходные данные для ИИ-агента."""
    product_columns = {
        "settlement": "CL_PRODUCT_RKO_MARK",
        "guarantee": "CL_PRODUCT_GAR_MARK",
        "loan": "CL_PRODUCT_LOA_MARK",
        "leasing": "CL_PRODUCT_LEA_MARK",
        "deposit": "CL_PRODUCT_DEP_MARK",
        "payroll": "CL_PRODUCT_ZPP_MARK",
        "factoring": "FACTORING_MARK",
    }
    return {
        "industry_code": profile.get("MAINOKVED_CODE"),
        "industry_name": profile.get("MAINOKVED_NAME"),
        "company_age_range": profile.get("COMPANY_AGE_YEARS_GRADATION"),
        "employees_fns_range": profile.get("AMOUNT_WORKERS_FNS_GRADATION"),
        "region": profile.get("REGION"),
        "city": profile.get("CITY"),
        "bank_products": {
            name: as_flag(profile.get(source))
            for name, source in product_columns.items()
        },
        "has_customs_data": as_flag(profile.get("FTS_FLAG")),
        "customs_countries": [
            country.strip() for country in (profile.get("FTS_COUNTRY_NAME") or "").split(",")
            if country.strip()
        ],
    }


def known_company(conn: Connection, company_id: str) -> dict:
    result = conn.execute(
        "SELECT company_id, profile, source_row_count, is_ambiguous, "
        "industry_code, industry_name, region, group_id, revenue_raw "
        "FROM companies WHERE company_id = %s",
        (company_id,),
    ).fetchone()
    if result is None:
        raise HTTPException(404, "Компания отсутствует в предоставленном справочнике")
    return result


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/companies")
def list_companies(
    search: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conn: Connection = Depends(connect),
) -> dict:
    query = "%" + search.strip() + "%"
    rows = conn.execute(
        "SELECT company_id, industry_name, region, is_ambiguous "
        "FROM companies WHERE company_id ILIKE %s OR industry_name ILIKE %s "
        "ORDER BY company_id LIMIT %s OFFSET %s",
        (query, query, limit, offset),
    ).fetchall()
    return {"items": rows, "limit": limit, "offset": offset}


@app.get("/api/companies/{company_id}")
def company_profile(company_id: str, conn: Connection = Depends(connect)) -> dict:
    company = known_company(conn, company_id)
    if company["is_ambiguous"]:
        raise HTTPException(
            status_code=409,
            detail=(f"У {company_id} противоречащие исходные профили ({company['source_row_count']} строк). Требуется проверка."),
        )

    summary = conn.execute(
        """
        SELECT
          COUNT(DISTINCT payer_id) FILTER (
            WHERE beneficiary_id = %(id)s AND payer_id <> %(id)s
          ) AS incoming_counterparties,
          COUNT(DISTINCT beneficiary_id) FILTER (
            WHERE payer_id = %(id)s AND beneficiary_id <> %(id)s
          ) AS outgoing_counterparties,
          COUNT(*) FILTER (
            WHERE payer_id = %(id)s AND beneficiary_id = %(id)s
          ) AS self_payment_rows
        FROM payments
        WHERE payer_id = %(id)s OR beneficiary_id = %(id)s
        """,
        {"id": company_id},
    ).fetchone()
    people = conn.execute(
        "SELECT COUNT(DISTINCT person_id) AS count "
        "FROM person_company_links WHERE company_id = %s",
        (company_id,),
    ).fetchone()["count"]
    return {
        "company_id": company["company_id"],
        "industry": company["industry_name"],
        "industry_code": company["industry_code"],
        "region": company["region"],
        "group_id": company["group_id"],
        "revenue_raw": (str(company["revenue_raw"])
                        if company["revenue_raw"] is not None else None),
        "raw_profile": company["profile"],  # All 24 CSV fields, not just five.
        "features": company_features(company["profile"]),
        "source_row_count": company["source_row_count"],
        "linked_person_ids_count": people,
        "payment_summary": summary,
        "data_notes": {
            "revenue_currency_and_units_verified": False,
            "payment_period_known": False,
            "payment_link_does_not_prove_supplier_relationship": True,
            "linked_person_legal_role_known": False,
        },
    }


@app.get("/api/companies/{company_id}/counterparties")
def counterparties(
    company_id: str,
    direction: str = Query(default="outgoing", pattern="^(incoming|outgoing)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conn: Connection = Depends(connect),
) -> dict:
    known_company(conn, company_id)
    if direction == "incoming":
        other_column, self_column = "payer_id", "beneficiary_id"
    else:
        other_column, self_column = "beneficiary_id", "payer_id"

    # Названия столбцов выбираются только из двух заданных случаев.
    query = f"""
        SELECT
            p.{other_column} AS company_id,
            SUM(p.amount) AS payment_amount,
            SUM(p.payment_count)::BIGINT AS payments_count,
            c.industry_name AS industry,
            c.region AS region,
            COALESCE(c.is_ambiguous, FALSE) AS ambiguous_profile
        FROM payments p
        LEFT JOIN companies c ON c.company_id = p.{other_column}
        WHERE p.{self_column} = %s AND p.{other_column} <> %s
        GROUP BY p.{other_column}, c.industry_name, c.region, c.is_ambiguous
        ORDER BY payment_amount DESC, company_id
        LIMIT %s OFFSET %s
    """
    rows = conn.execute(query, (company_id, company_id, limit, offset)).fetchall()
    return {
        "company_id": company_id,
        "direction": direction,
        "limit": limit,
        "offset": offset,
        "items": [
            {**row, "payment_amount": str(row["payment_amount"])} for row in rows
        ],
    }


@app.get("/api/companies/{company_id}/linked-people")
def linked_people(
    company_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conn: Connection = Depends(connect),
) -> dict:
    known_company(conn, company_id)
    rows = conn.execute(
        "SELECT person_id, city_name, region_name "
        "FROM person_company_links WHERE company_id = %s "
        "ORDER BY person_id LIMIT %s OFFSET %s",
        (company_id, limit, offset),
    ).fetchall()
    return {"company_id": company_id, "items": rows,
            "limit": limit, "offset": offset,
            "note": "Тип юридической связи человека с компанией в датасете не задан"}


@app.get("/api/companies/{company_id}/source-records")
def source_records(
    company_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conn: Connection = Depends(connect),
) -> dict:
    known_company(conn, company_id)
    rows = conn.execute(
        "SELECT row_id, to_jsonb(s) - 'row_id' AS profile "
        'FROM source_company_rows s WHERE "SYNTHETIC_U" = %s '
        'ORDER BY row_id LIMIT %s OFFSET %s',
        (company_id, limit, offset),
    ).fetchall()
    return {"company_id": company_id, "items": rows, "limit": limit, "offset": offset}
