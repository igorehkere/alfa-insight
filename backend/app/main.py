from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import BigInteger, and_, distinct, false, func, inspect, or_, select
from sqlalchemy.orm import Session

from app.connect import get_session
from app.models import Company, Payment, PersonCompanyLink, SourceCompanyRow

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
            name: as_flag(profile.get(column))
            for name, column in product_columns.items()
        },
        "has_customs_data": as_flag(profile.get("FTS_FLAG")),
        "customs_countries": [
            country.strip()
            for country in (profile.get("FTS_COUNTRY_NAME") or "").split(",")
            if country.strip()
        ],
    }


def known_company(session: Session, company_id: str) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(
            status_code=404,
            detail="Компания отсутствует в предоставленном справочнике",
        )
    return company


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/companies")
def list_companies(
    search: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    pattern = f"%{search.strip()}%"
    stmt = (
        select(Company)
        .where(
            or_(
                Company.company_id.ilike(pattern),
                Company.industry_name.ilike(pattern),
            )
        )
        .order_by(Company.company_id)
        .limit(limit)
        .offset(offset)
    )
    companies = session.scalars(stmt).all()
    return {
        "items": [
            {
                "company_id": company.company_id,
                "industry_name": company.industry_name,
                "region": company.region,
                "is_ambiguous": company.is_ambiguous,
            }
            for company in companies
        ],
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/companies/{company_id}")
def company_profile(company_id: str, session: Session = Depends(get_session)) -> dict:
    company = known_company(session, company_id)
    if company.is_ambiguous:
        raise HTTPException(
            status_code=409,
            detail=(
                f"У {company_id} противоречащие исходные профили "
                f"({company.source_row_count} строк). Требуется проверка."
            ),
        )

    incoming = func.count(distinct(Payment.payer_id)).filter(
        and_(
            Payment.beneficiary_id == company_id,
            Payment.payer_id != company_id,
        )
    )
    outgoing = func.count(distinct(Payment.beneficiary_id)).filter(
        and_(
            Payment.payer_id == company_id,
            Payment.beneficiary_id != company_id,
        )
    )
    own = func.count().filter(
        and_(
            Payment.payer_id == company_id,
            Payment.beneficiary_id == company_id,
        )
    )
    payment_summary = session.execute(
        select(
            incoming.label("incoming_counterparties"),
            outgoing.label("outgoing_counterparties"),
            own.label("self_payment_rows"),
        ).where(
            or_(
                Payment.payer_id == company_id,
                Payment.beneficiary_id == company_id,
            )
        )
    ).mappings().one()

    people_count = session.scalar(
        select(func.count(distinct(PersonCompanyLink.person_id))).where(
            PersonCompanyLink.company_id == company_id
        )
    )
    profile = company.profile or {}
    return {
        "company_id": company.company_id,
        "industry": company.industry_name,
        "industry_code": company.industry_code,
        "region": company.region,
        "group_id": company.group_id,
        "revenue_raw": (
            str(company.revenue_raw) if company.revenue_raw is not None else None
        ),
        "raw_profile": company.profile,
        "features": company_features(profile),
        "source_row_count": company.source_row_count,
        "linked_person_ids_count": people_count or 0,
        "payment_summary": dict(payment_summary),
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
    session: Session = Depends(get_session),
) -> dict:
    known_company(session, company_id)

    if direction == "incoming":
        other_column = Payment.payer_id
        self_column = Payment.beneficiary_id
    else:
        other_column = Payment.beneficiary_id
        self_column = Payment.payer_id

    total_amount = func.sum(Payment.amount).label("payment_amount")
    total_count = func.sum(Payment.payment_count).cast(BigInteger).label("payments_count")
    stmt = (
        select(
            other_column.label("company_id"),
            total_amount,
            total_count,
            Company.industry_name.label("industry"),
            Company.region.label("region"),
            func.coalesce(Company.is_ambiguous, false()).label("ambiguous_profile"),
        )
        .select_from(Payment)
        .outerjoin(Company, Company.company_id == other_column)
        .where(self_column == company_id, other_column != company_id)
        .group_by(
            other_column, Company.industry_name, Company.region, Company.is_ambiguous
        )
        .order_by(total_amount.desc(), other_column)
        .limit(limit)
        .offset(offset)
    )
    rows = session.execute(stmt).mappings().all()
    return {
        "company_id": company_id,
        "direction": direction,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                **dict(row),
                "payment_amount": str(row["payment_amount"]),
            }
            for row in rows
        ],
    }


@app.get("/api/companies/{company_id}/linked-people")
def linked_people(
    company_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    known_company(session, company_id)
    stmt = (
        select(
            PersonCompanyLink.person_id,
            PersonCompanyLink.city_name,
            PersonCompanyLink.region_name,
        )
        .where(PersonCompanyLink.company_id == company_id)
        .order_by(PersonCompanyLink.person_id)
        .limit(limit)
        .offset(offset)
    )
    rows = session.execute(stmt).mappings().all()
    return {
        "company_id": company_id,
        "items": [dict(row) for row in rows],
        "limit": limit,
        "offset": offset,
        "note": "Тип юридической связи человека с компанией в датасете не задан",
    }


@app.get("/api/companies/{company_id}/source-records")
def source_records(
    company_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    known_company(session, company_id)
    stmt = (
        select(SourceCompanyRow)
        .where(SourceCompanyRow.synthetic_u == company_id)
        .order_by(SourceCompanyRow.row_id)
        .limit(limit)
        .offset(offset)
    )
    source_rows = session.scalars(stmt).all()

    source_columns = [
        attr for attr in inspect(SourceCompanyRow).column_attrs
        if attr.key != "row_id"
    ]
    return {
        "company_id": company_id,
        "items": [
            {
                "row_id": row.row_id,
                "profile": {
                    attr.columns[0].name: getattr(row, attr.key)
                    for attr in source_columns
                },
            }
            for row in source_rows
        ],
        "limit": limit,
        "offset": offset,
    }
