from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    company_id: Mapped[str] = mapped_column(Text, primary_key=True)
    profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False)
    industry_code: Mapped[str | None] = mapped_column(Text)
    industry_name: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    group_id: Mapped[str | None] = mapped_column(Text)
    revenue_raw: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    beneficiary_id: Mapped[str] = mapped_column(Text, nullable=False)
    payer_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 2), nullable=False)
    payment_count: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PersonCompanyLink(Base):
    __tablename__ = "person_company_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    person_id: Mapped[str] = mapped_column(Text, nullable=False)
    city_name: Mapped[str | None] = mapped_column(Text)
    region_name: Mapped[str | None] = mapped_column(Text)
    company_id: Mapped[str] = mapped_column(Text, nullable=False)


class SourceCompanyRow(Base):
    __tablename__ = "source_company_rows"

    row_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    synthetic_u: Mapped[str] = mapped_column("SYNTHETIC_U", Text, nullable=False)
    org_type: Mapped[str | None] = mapped_column("ORG_TYPE", Text)
    okfs_name: Mapped[str | None] = mapped_column("OKFS_NAME", Text)
    company_age_years_gradation: Mapped[str | None] = mapped_column("COMPANY_AGE_YEARS_GRADATION", Text)
    eco_mark_label: Mapped[str | None] = mapped_column("ECO_MARK_LABEL", Text)
    mainokved_code: Mapped[str | None] = mapped_column("MAINOKVED_CODE", Text)
    mainokved_name: Mapped[str | None] = mapped_column("MAINOKVED_NAME", Text)
    negativemark: Mapped[str | None] = mapped_column("NEGATIVEMARK", Text)
    amount_workers_fns_gradation: Mapped[str | None] = mapped_column("AMOUNT_WORKERS_FNS_GRADATION", Text)
    amount_workers_spark_gradation: Mapped[str | None] = mapped_column("AMOUNT_WORKERS_SPARK_GRADATION", Text)
    cl_type: Mapped[str | None] = mapped_column("CL_TYPE", Text)
    cl_product_rko_mark: Mapped[str | None] = mapped_column("CL_PRODUCT_RKO_MARK", Text)
    cl_product_gar_mark: Mapped[str | None] = mapped_column("CL_PRODUCT_GAR_MARK", Text)
    cl_product_loa_mark: Mapped[str | None] = mapped_column("CL_PRODUCT_LOA_MARK", Text)
    cl_product_lea_mark: Mapped[str | None] = mapped_column("CL_PRODUCT_LEA_MARK", Text)
    cl_product_dep_mark: Mapped[str | None] = mapped_column("CL_PRODUCT_DEP_MARK", Text)
    cl_product_zpp_mark: Mapped[str | None] = mapped_column("CL_PRODUCT_ZPP_MARK", Text)
    fts_flag: Mapped[str | None] = mapped_column("FTS_FLAG", Text)
    fts_country_name: Mapped[str | None] = mapped_column("FTS_COUNTRY_NAME", Text)
    factoring_mark: Mapped[str | None] = mapped_column("FACTORING_MARK", Text)
    revenue_company: Mapped[str | None] = mapped_column("REVENUE_COMPANY", Text)
    group_id: Mapped[str | None] = mapped_column("GROUP_ID", Text)
    city: Mapped[str | None] = mapped_column("CITY", Text)
    region: Mapped[str | None] = mapped_column("REGION", Text)
