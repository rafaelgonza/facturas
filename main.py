"""FastAPI application: routes for login, dashboard, invoice generation, history, admin."""
import os
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from auth import hash_password, verify_password, is_authenticated, require_auth
from crypto_utils import encrypt_bytes
from database import (
    init_db, get_db, get_settings, Settings, Contract, Invoice, Client, STORAGE_PATH,
)
from invoice_service import (
    make_preview, build_pdf_context, build_filename, save_invoice_record,
)
from pdf_generator import render_invoice_pdf
from signer import sign_pdf, SignatureError


BASE_DIR = Path(__file__).parent
PDF_DIR = STORAGE_PATH / "pdfs"
CERT_DIR = STORAGE_PATH / "certs"
ENCRYPTED_CERT_PATH = CERT_DIR / "cert.p12.enc"

# --- App ---
app = FastAPI(title="Invoice Generator")

# Session middleware (cookie-based)
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="invoice_session",
    max_age=14 * 24 * 60 * 60,  # 14 days
    same_site="lax",
    https_only=False,  # Railway terminates TLS upstream; cookies still work
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.on_event("startup")
def _startup():
    init_db()


# Convert HTTPException(303 + Location) into a real redirect
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    return await _default_http_exception_handler(request, exc)


async def _default_http_exception_handler(request, exc):
    from fastapi.exception_handlers import http_exception_handler as default
    return await default(request, exc)


# ---------- AUTH ----------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: Optional[str] = None):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login")
def login_submit(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    if verify_password(password, settings.password_hash):
        request.session["authenticated"] = True
        return RedirectResponse("/facturas", status_code=303)
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------- LANDING ----------

@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/facturas", status_code=303)
    return templates.TemplateResponse("landing.html", {"request": request})


# ---------- DASHBOARD ----------

@app.get("/facturas", response_class=HTMLResponse)
def dashboard(
    request: Request,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    recent = (
        db.query(Invoice)
        .order_by(Invoice.created_at.desc())
        .limit(5)
        .all()
    )
    cert_loaded = ENCRYPTED_CERT_PATH.exists()
    using_default_pw = verify_password("admin", settings.password_hash)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "recent": recent,
            "cert_loaded": cert_loaded,
            "using_default_pw": using_default_pw,
            "settings": settings,
        },
    )


# ---------- NEW INVOICE ----------

@app.get("/new", response_class=HTMLResponse)
def new_invoice_form(
    request: Request,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    contracts = (
        db.query(Contract)
        .filter(Contract.is_archived == False)  # noqa: E712
        .order_by(Contract.created_at.desc())
        .all()
    )
    today = date.today()
    cert_loaded = ENCRYPTED_CERT_PATH.exists()
    clients = db.query(Client).filter(Client.is_archived == False).order_by(Client.name).all()  # noqa: E712
    return templates.TemplateResponse(
        "new_invoice.html",
        {
            "request": request,
            "contracts": contracts,
            "active_contract_id": settings.active_contract_id,
            "clients": clients,
            "active_client_id": settings.active_client_id,
            "default_year": today.year,
            "default_month": today.month,
            "cert_loaded": cert_loaded,
        },
    )


@app.post("/new", response_class=HTMLResponse)
def new_invoice_submit(
    request: Request,
    year: int = Form(...),
    month: int = Form(...),
    days: int = Form(...),
    contract_id: int = Form(...),
    client_id: Optional[int] = Form(None),
    invoice_date: Optional[str] = Form(None),  # ISO YYYY-MM-DD
    period_start: Optional[str] = Form(None),
    period_end: Optional[str] = Form(None),
    cert_password: Optional[str] = Form(None),
    sign: Optional[str] = Form(None),  # "1" if user wants to sign
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    contracts = db.query(Contract).filter(Contract.is_archived == False).all()  # noqa: E712
    clients = db.query(Client).filter(Client.is_archived == False).order_by(Client.name).all()  # noqa: E712

    def _err(msg: str):
        return templates.TemplateResponse(
            "new_invoice.html",
            {
                "request": request,
                "contracts": contracts,
                "active_contract_id": settings.active_contract_id,
                "clients": clients,
                "active_client_id": client_id or settings.active_client_id,
                "default_year": year,
                "default_month": month,
                "default_days": days,
                "default_contract_id": contract_id,
                "cert_loaded": ENCRYPTED_CERT_PATH.exists(),
                "error": msg,
            },
            status_code=400,
        )

    if days <= 0:
        return _err("El número de días debe ser mayor que 0.")
    if not (1 <= month <= 12):
        return _err("Mes inválido.")

    def _parse(s: Optional[str]):
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    # Resolve client: use selected or fall back to active
    resolved_client_id = client_id or settings.active_client_id
    client = db.query(Client).get(resolved_client_id) if resolved_client_id else None
    if not client:
        return _err("Selecciona un cliente antes de generar la factura.")

    # Update active client for next time
    if settings.active_client_id != resolved_client_id:
        settings.active_client_id = resolved_client_id
        db.commit()

    try:
        preview = make_preview(
            db, settings,
            year=year, month=month, days=days,
            contract_id=contract_id,
            invoice_date=_parse(invoice_date),
            period_start=_parse(period_start),
            period_end=_parse(period_end),
        )
    except ValueError as e:
        return _err(str(e))

    contract = db.query(Contract).get(contract_id)
    pdf_filename = build_filename(settings, year, month, preview.seq_in_month)

    # Avoid filename collision (paranoia)
    target_path = PDF_DIR / pdf_filename
    if target_path.exists():
        stem = target_path.stem
        target_path = PDF_DIR / f"{stem}-{int(datetime.utcnow().timestamp())}.pdf"
        pdf_filename = target_path.name

    # Render PDF
    ctx = build_pdf_context(settings, contract, preview, client=client)
    unsigned_path = PDF_DIR / f".unsigned_{pdf_filename}"
    render_invoice_pdf(ctx, unsigned_path)

    will_sign = bool(sign and ENCRYPTED_CERT_PATH.exists() and cert_password)
    final_path = PDF_DIR / pdf_filename

    if will_sign:
        try:
            sign_pdf(unsigned_path, final_path, ENCRYPTED_CERT_PATH, cert_password)
        except SignatureError as e:
            unsigned_path.unlink(missing_ok=True)
            return _err(f"No se pudo firmar la factura: {e}")
        unsigned_path.unlink(missing_ok=True)
        signed = True
    else:
        # Move unsigned to final
        unsigned_path.rename(final_path)
        signed = False

    inv = save_invoice_record(db, settings, contract, preview, pdf_filename, signed, client_id=resolved_client_id)

    return templates.TemplateResponse(
        "invoice_done.html",
        {
            "request": request,
            "invoice": inv,
            "signed": signed,
        },
    )


# ---------- HISTORY / DOWNLOADS ----------

@app.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).all()
    return templates.TemplateResponse(
        "history.html",
        {"request": request, "invoices": invoices},
    )


@app.get("/download/{invoice_id}")
def download(
    invoice_id: int,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    inv = db.query(Invoice).get(invoice_id)
    if not inv:
        raise HTTPException(404, "Factura no encontrada")
    pdf_path = PDF_DIR / inv.pdf_filename
    if not pdf_path.exists():
        raise HTTPException(404, "PDF no encontrado en disco")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=inv.pdf_filename,
    )


@app.post("/delete/{invoice_id}")
def delete_invoice(
    invoice_id: int,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    inv = db.query(Invoice).get(invoice_id)
    if not inv:
        raise HTTPException(404, "Factura no encontrada")
    pdf_path = PDF_DIR / inv.pdf_filename
    pdf_path.unlink(missing_ok=True)
    db.delete(inv)
    db.commit()
    return RedirectResponse("/history", status_code=303)


# ---------- ADMIN ----------

@app.get("/admin", response_class=HTMLResponse)
def admin(
    request: Request,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
    section: str = "issuer",
    msg: Optional[str] = None,
    err: Optional[str] = None,
):
    settings = get_settings(db)
    contracts = db.query(Contract).order_by(Contract.created_at.desc()).all()
    clients = db.query(Client).order_by(Client.name).all()
    cert_loaded = ENCRYPTED_CERT_PATH.exists()
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "settings": settings,
            "contracts": contracts,
            "clients": clients,
            "cert_loaded": cert_loaded,
            "section": section,
            "msg": msg,
            "err": err,
        },
    )


@app.post("/admin/issuer")
def admin_save_issuer(
    issuer_name: str = Form(...),
    issuer_address_line1: str = Form(...),
    issuer_address_line2: str = Form(...),
    issuer_phone: str = Form(...),
    issuer_email: str = Form(...),
    issuer_vat: str = Form(...),
    issuer_initials: str = Form(...),
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    s = get_settings(db)
    s.issuer_name = issuer_name
    s.issuer_address_line1 = issuer_address_line1
    s.issuer_address_line2 = issuer_address_line2
    s.issuer_phone = issuer_phone
    s.issuer_email = issuer_email
    s.issuer_vat = issuer_vat
    s.issuer_initials = issuer_initials
    db.commit()
    return RedirectResponse("/admin?section=issuer&msg=Datos+del+emisor+guardados", status_code=303)


@app.post("/admin/clients/new")
def admin_new_client(
    name: str = Form(...),
    address_line1: str = Form(""),
    address_line2: str = Form(""),
    vat: str = Form(""),
    set_active: Optional[str] = Form(None),
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = Client(name=name, address_line1=address_line1, address_line2=address_line2, vat=vat)
    db.add(c)
    db.flush()
    if set_active:
        s = get_settings(db)
        s.active_client_id = c.id
    db.commit()
    return RedirectResponse("/admin?section=clients&msg=Cliente+creado", status_code=303)


@app.post("/admin/clients/{cid}/update")
def admin_update_client(
    cid: int,
    name: str = Form(...),
    address_line1: str = Form(""),
    address_line2: str = Form(""),
    vat: str = Form(""),
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = db.query(Client).get(cid)
    if not c:
        raise HTTPException(404)
    c.name = name
    c.address_line1 = address_line1
    c.address_line2 = address_line2
    c.vat = vat
    db.commit()
    return RedirectResponse("/admin?section=clients&msg=Cliente+actualizado", status_code=303)


@app.post("/admin/clients/{cid}/activate")
def admin_activate_client(
    cid: int,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = db.query(Client).get(cid)
    if not c:
        raise HTTPException(404)
    s = get_settings(db)
    s.active_client_id = cid
    db.commit()
    return RedirectResponse("/admin?section=clients&msg=Cliente+activado", status_code=303)


@app.post("/admin/clients/{cid}/archive")
def admin_archive_client(
    cid: int,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = db.query(Client).get(cid)
    if not c:
        raise HTTPException(404)
    c.is_archived = True
    s = get_settings(db)
    if s.active_client_id == cid:
        s.active_client_id = None
    db.commit()
    return RedirectResponse("/admin?section=clients&msg=Cliente+archivado", status_code=303)


@app.post("/admin/clients/{cid}/unarchive")
def admin_unarchive_client(
    cid: int,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = db.query(Client).get(cid)
    if not c:
        raise HTTPException(404)
    c.is_archived = False
    db.commit()
    return RedirectResponse("/admin?section=clients&msg=Cliente+restaurado", status_code=303)


@app.post("/admin/clients/{cid}/delete")
def admin_delete_client(
    cid: int,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = db.query(Client).get(cid)
    if not c:
        raise HTTPException(404)
    # Check no invoices linked
    linked = db.query(Invoice).filter(Invoice.client_id == cid).count()
    if linked:
        return RedirectResponse(f"/admin?section=clients&err=No+se+puede+borrar:+tiene+{linked}+facturas+asociadas", status_code=303)
    s = get_settings(db)
    if s.active_client_id == cid:
        s.active_client_id = None
    db.delete(c)
    db.commit()
    return RedirectResponse("/admin?section=clients&msg=Cliente+eliminado", status_code=303)


@app.post("/admin/bank")
def admin_save_bank(
    bank_name: str = Form(...),
    bank_iban: str = Form(...),
    bank_swift: str = Form(...),
    bank_holder: str = Form(...),
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    s = get_settings(db)
    s.bank_name = bank_name
    s.bank_iban = bank_iban
    s.bank_swift = bank_swift
    s.bank_holder = bank_holder
    db.commit()
    return RedirectResponse("/admin?section=bank&msg=Datos+bancarios+guardados", status_code=303)


@app.post("/admin/general")
def admin_save_general(
    vat_percentage: float = Form(...),
    vat_note: str = Form(...),
    filename_pattern_first: str = Form(...),
    filename_pattern_extra: str = Form(...),
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    s = get_settings(db)
    s.vat_percentage = vat_percentage
    s.vat_note = vat_note
    s.filename_pattern_first = filename_pattern_first
    s.filename_pattern_extra = filename_pattern_extra
    db.commit()
    return RedirectResponse("/admin?section=general&msg=Configuración+general+guardada", status_code=303)


@app.post("/admin/contracts/new")
def admin_new_contract(
    name: str = Form(...),
    daily_rate: float = Form(...),
    services_description: str = Form(...),
    set_active: Optional[str] = Form(None),
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = Contract(name=name, daily_rate=daily_rate, services_description=services_description)
    db.add(c)
    db.flush()
    if set_active:
        s = get_settings(db)
        s.active_contract_id = c.id
    db.commit()
    return RedirectResponse("/admin?section=contracts&msg=Contrato+creado", status_code=303)


@app.post("/admin/contracts/{cid}/update")
def admin_update_contract(
    cid: int,
    name: str = Form(...),
    daily_rate: float = Form(...),
    services_description: str = Form(...),
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = db.query(Contract).get(cid)
    if not c:
        raise HTTPException(404)
    c.name = name
    c.daily_rate = daily_rate
    c.services_description = services_description
    db.commit()
    return RedirectResponse("/admin?section=contracts&msg=Contrato+actualizado", status_code=303)


@app.post("/admin/contracts/{cid}/activate")
def admin_activate_contract(
    cid: int,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = db.query(Contract).get(cid)
    if not c:
        raise HTTPException(404)
    s = get_settings(db)
    s.active_contract_id = cid
    db.commit()
    return RedirectResponse("/admin?section=contracts&msg=Contrato+activado", status_code=303)


@app.post("/admin/contracts/{cid}/archive")
def admin_archive_contract(
    cid: int,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = db.query(Contract).get(cid)
    if not c:
        raise HTTPException(404)
    c.is_archived = True
    s = get_settings(db)
    if s.active_contract_id == cid:
        s.active_contract_id = None
    db.commit()
    return RedirectResponse("/admin?section=contracts&msg=Contrato+archivado", status_code=303)


@app.post("/admin/contracts/{cid}/unarchive")
def admin_unarchive_contract(
    cid: int,
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = db.query(Contract).get(cid)
    if not c:
        raise HTTPException(404)
    c.is_archived = False
    db.commit()
    return RedirectResponse("/admin?section=contracts&msg=Contrato+restaurado", status_code=303)


@app.post("/admin/cert")
async def admin_upload_cert(
    cert_file: UploadFile = File(...),
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if not cert_file.filename:
        return RedirectResponse("/admin?section=cert&err=Selecciona+un+archivo", status_code=303)
    raw = await cert_file.read()
    if not raw:
        return RedirectResponse("/admin?section=cert&err=Archivo+vacío", status_code=303)
    if len(raw) > 5 * 1024 * 1024:  # 5 MB sanity limit
        return RedirectResponse("/admin?section=cert&err=Archivo+demasiado+grande", status_code=303)

    encrypted = encrypt_bytes(raw)
    ENCRYPTED_CERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENCRYPTED_CERT_PATH.write_bytes(encrypted)

    s = get_settings(db)
    s.cert_filename = cert_file.filename
    s.cert_uploaded_at = datetime.utcnow()
    db.commit()
    return RedirectResponse("/admin?section=cert&msg=Certificado+subido+y+cifrado", status_code=303)


@app.post("/admin/cert/delete")
def admin_delete_cert(
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if ENCRYPTED_CERT_PATH.exists():
        ENCRYPTED_CERT_PATH.unlink()
    s = get_settings(db)
    s.cert_filename = ""
    s.cert_uploaded_at = None
    db.commit()
    return RedirectResponse("/admin?section=cert&msg=Certificado+eliminado", status_code=303)


@app.post("/admin/password")
def admin_change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_repeat: str = Form(...),
    _: bool = Depends(require_auth),
    db: Session = Depends(get_db),
):
    s = get_settings(db)
    if not verify_password(current_password, s.password_hash):
        return RedirectResponse("/admin?section=security&err=Contraseña+actual+incorrecta", status_code=303)
    if new_password != new_password_repeat:
        return RedirectResponse("/admin?section=security&err=Las+nuevas+contraseñas+no+coinciden", status_code=303)
    if len(new_password) < 8:
        return RedirectResponse("/admin?section=security&err=Mínimo+8+caracteres", status_code=303)
    s.password_hash = hash_password(new_password)
    db.commit()
    return RedirectResponse("/admin?section=security&msg=Contraseña+actualizada", status_code=303)


# Health check (Railway uses this)
@app.get("/health")
def health():
    return {"status": "ok"}
