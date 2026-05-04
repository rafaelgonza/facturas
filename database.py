"""SQLAlchemy models and database setup."""
import os
import json
from datetime import datetime
from pathlib import Path
from sqlalchemy import (
    Column, Integer, String, DateTime, Float, Text, Boolean, ForeignKey, create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Storage path: configurable for Railway volume mount
STORAGE_PATH = Path(os.getenv("STORAGE_PATH", str(Path(__file__).parent / "storage")))
STORAGE_PATH.mkdir(parents=True, exist_ok=True)
(STORAGE_PATH / "pdfs").mkdir(exist_ok=True)
(STORAGE_PATH / "certs").mkdir(exist_ok=True)

DB_PATH = STORAGE_PATH / "invoices.db"
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Settings(Base):
    """Single-row table holding all configurable application settings."""
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, default=1)

    # Issuer (you)
    issuer_name = Column(String, default="Rafael González Manzano")
    issuer_address_line1 = Column(String, default="Calle Sierra de Gador, 39")
    issuer_address_line2 = Column(String, default="41807 Espartinas, Seville, Spain")
    issuer_phone = Column(String, default="+34 645 77 63 10")
    issuer_email = Column(String, default="rafaelgonza@gmail.com")
    issuer_vat = Column(String, default="ES49027243Y")
    # Initials used in invoice number, e.g. RGM in 26/04/RGM/01
    issuer_initials = Column(String, default="RGM")

    # Client
    client_name = Column(String, default="SEIDOR BELGIUM S.P.R.L.,")
    client_address_line1 = Column(String, default="Robert Schuman roundabout 6, Box 7, 1040 Brussels,")
    client_address_line2 = Column(String, default="Belgium")
    client_vat = Column(String, default="TVA BE0673.493.269")

    # Bank
    bank_name = Column(String, default="kutxabank")
    bank_iban = Column(String, default="ES21 2095 8302 1091 7258 5956")
    bank_swift = Column(String, default="BASKES2BXXX")
    bank_holder = Column(String, default="Rafael González Manzano")

    # VAT note
    vat_note = Column(
        Text,
        default=(
            "Spanish VAT not applicable by application of article 25 "
            "Spanish VAT Code – VAT due by the recipient of the service "
            '("reverse charge mechanism")'
        ),
    )
    vat_percentage = Column(Float, default=0.0)

    # Filename patterns. Available placeholders:
    #   {month:02d}, {year:04d}, {year_short:02d}, {seq}, {initials}
    filename_pattern_first = Column(
        String, default="{month:02d}{year_short:02d}-Invoice-JRC"
    )
    filename_pattern_extra = Column(
        String, default="{month:02d}{year_short:02d}-Invoice-JRC-{seq}"
    )

    # Auth (bcrypt hash)
    password_hash = Column(String, default="")

    # Certificate
    cert_filename = Column(String, default="")  # original filename (for reference only)
    cert_uploaded_at = Column(DateTime, nullable=True)

    # Active contract
    active_contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=True)


class Contract(Base):
    """A billing contract: defines daily rate and bullet-list services description."""
    __tablename__ = "contracts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # e.g. "Contract DI/07941 - SC 029679"
    daily_rate = Column(Float, nullable=False)
    services_description = Column(Text, nullable=False)  # one bullet per line
    is_archived = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Invoice(Base):
    """A generated invoice record (with snapshot of all fields used)."""
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_number = Column(String, unique=True, nullable=False)
    invoice_date = Column(String, nullable=False)  # DD/MM/YYYY
    period_start = Column(String, nullable=False)  # D/MM/YYYY
    period_end = Column(String, nullable=False)
    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    seq_in_month = Column(Integer, nullable=False)  # 1, 2, 3...
    days = Column(Integer, nullable=False)
    daily_rate = Column(Float, nullable=False)
    total = Column(Float, nullable=False)
    vat_percentage = Column(Float, default=0.0)
    pdf_filename = Column(String, nullable=False)
    signed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # JSON snapshots so historical invoices remain reproducible even
    # if you change settings later
    snapshot = Column(Text, nullable=False)  # JSON of all data used


def init_db():
    """Create tables and seed default data on first run."""
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        settings = db.query(Settings).first()
        if not settings:
            settings = Settings(id=1)
            db.add(settings)
            db.flush()

            # Default contract: the active one (320 €/day, SC 029679)
            default_services = """Architecture and design of information systems
Programming and maintenance of Object Oriented applications
Programming and maintenance of web applications
Prototyping of applications
Elaboration of test programs
Production of application technical documentation, following the JRC adopted methodology (i.e.: RUP@EC).
Assistance with installation and configuration of the systems
Elicitation and implementation of user requirements.
Participation in meetings with the project teams.
Advise scientific staff on the development of new information systems according to their requirements.
Collaborate with the database administrator (DBA) and/or the interface designer in complex information systems.
Coming to the office to meetings with clients. Address: Expo Building. Inca Garcilaso Street 3, 41092. Seville. Spain"""

            default_contract = Contract(
                name="Contract DI/07941 - SC 029679",
                daily_rate=320.0,
                services_description=default_services,
            )
            db.add(default_contract)
            db.flush()
            settings.active_contract_id = default_contract.id

            # Bootstrap password from env var (or 'admin' as fallback)
            from auth import hash_password
            initial_pw = os.getenv("ADMIN_PASSWORD", "admin")
            settings.password_hash = hash_password(initial_pw)

            db.commit()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_settings(db: Session) -> Settings:
    s = db.query(Settings).first()
    if not s:
        init_db()
        s = db.query(Settings).first()
    return s
