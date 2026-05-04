# Generador de Facturas

Aplicación web para generar mensualmente facturas en PDF con el mismo formato que tus plantillas Word, firmadas digitalmente con certificado PKCS#12.

## Características

- **Login con contraseña** (cookie de sesión).
- **Dashboard sencillo**: pides "nueva factura" y solo introduces **mes, año y días trabajados**.
- **Cálculo automático**: número de factura correlativo (`YY/MM/INICIALES/NN`), fechas, período de trabajo, importe total.
- **PDF idéntico al original** (Letter, mismo layout, mismas líneas rojas, mismo formato `5056,00 €`).
- **Firma digital PAdES** con tu certificado `.p12`/`.pfx`. El certificado se sube **una vez** y se guarda **cifrado** en disco; la contraseña del certificado **no se almacena**, la introduces en cada firma.
- **Histórico** con descarga.
- **Panel de administración** con todo configurable: emisor, cliente, banco, contratos (con tarifas distintas), nota de IVA, patrón de nombre de archivo, IVA, contraseña.

## Despliegue en Railway (mínimo coste)

La app es un **único servicio** (FastAPI + SQLite, sin Postgres aparte) con un **volumen persistente** para los PDFs y la base de datos.

### Pasos

1. Sube este repositorio a GitHub.
2. En Railway: **New Project → Deploy from GitHub repo** → selecciona el repositorio.
3. Railway detectará el `Dockerfile` automáticamente.
4. Crea un **Volume** y móntalo en `/data` (Railway → Service → Settings → Volumes).
5. Configura las variables de entorno en **Variables**:
   - `SECRET_KEY` → string largo aleatorio. Genera uno con:
     ```bash
     python -c "import secrets; print(secrets.token_urlsafe(48))"
     ```
     ⚠️ **Importante**: una vez generes facturas y subas el certificado, **no cambies esta variable** o no podrás descifrar el certificado.
   - `ADMIN_PASSWORD` → contraseña inicial de acceso (solo se usa en el primer arranque, luego se cambia desde el panel).
   - `STORAGE_PATH` → `/data` (debe coincidir con el path del volumen).
6. Despliega. Railway expone la URL pública automáticamente.

### Coste estimado

Railway cobra por uso de RAM/CPU. Esta app:
- Usa ~80–120 MB de RAM en reposo (1 worker uvicorn).
- CPU prácticamente 0 salvo cuando generas un PDF (1–2 segundos).
- SQLite + volumen → no hay coste de base de datos extra.

Con el plan Hobby ($5/mes) te sobra de largo.

## Primer uso

1. Accede a la URL de Railway.
2. Login con la contraseña que pusiste en `ADMIN_PASSWORD`.
3. Verás un aviso amarillo si sigues con la contraseña por defecto. Ve a **Administración → Seguridad** y cámbiala.
4. Ve a **Administración → Certificado** y sube tu `.p12` o `.pfx`.
5. Revisa el resto de secciones de Administración: ya vienen rellenas con tus datos de los ejemplos. El contrato activo es **SC 029679 a 320 €/día**.
6. ¡Listo! En **Inicio → Generar nueva factura** introduces mes y días, y la app hace el resto.

## Estructura del proyecto

```
invoice-app/
├── main.py                  # FastAPI app: rutas
├── database.py              # SQLAlchemy models + init
├── auth.py                  # Bcrypt + sesión
├── crypto_utils.py          # Cifrado del certificado en disco
├── pdf_generator.py         # WeasyPrint
├── signer.py                # pyhanko (firma PAdES)
├── invoice_service.py       # Lógica de negocio (numeración, fechas, etc.)
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── new_invoice.html
│   ├── invoice_done.html
│   ├── history.html
│   ├── admin.html
│   └── invoice_pdf.html     # Template del PDF
├── Dockerfile
├── railway.json
├── requirements.txt
└── .env.example
```

## Desarrollo local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copia .env.example a .env y edítalo
cp .env.example .env

# Crea carpeta de almacenamiento local
mkdir -p storage

# Ejecuta
STORAGE_PATH=./storage uvicorn main:app --reload
```

Abre http://localhost:8000

## Notas técnicas

### Numeración de facturas

Formato `YY/MM/INICIALES/NN` (ej. `26/04/RGM/01`). El secuencial `NN` se reinicia cada mes. Si en abril ya generaste una, la siguiente del mismo mes será `26/04/RGM/02` y el archivo usará el patrón `_extra`.

### Firma digital

Se usa `pyhanko` para firma PAdES B-B (estándar europeo). La firma se inserta como **firma visible** en la esquina inferior derecha de la página, igual que en tus PDFs originales. El certificado se carga desde el `.p12` cifrado en disco solo durante la firma; la contraseña pasa por memoria y se descarta tras firmar.

### Privacidad

- La contraseña de login se guarda como hash bcrypt.
- El certificado `.p12` se guarda cifrado con Fernet (AES-128-CBC + HMAC) usando una clave derivada de `SECRET_KEY`.
- La contraseña del certificado **nunca se persiste**.
- Las facturas son archivos PDF en disco (volumen Railway). Si las quieres más privadas, considera cifrarlas también o usar un bucket privado.

### Backup

Todo lo importante está en `/data` (DB SQLite + PDFs + certificado cifrado). Haz backup periódico del volumen Railway o descarga las facturas desde el histórico.
