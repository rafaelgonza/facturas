"""Digital signature for PDFs using pyhanko (PAdES B-B).

Loads the encrypted .p12 from disk, decrypts it to a temp file with
restrictive permissions, signs the PDF, and immediately deletes the temp.
The certificate password is supplied per-call and never stored.
"""
import os
import tempfile
from pathlib import Path

from pyhanko.sign import signers, fields
from pyhanko.sign.fields import SigFieldSpec, SigSeedSubFilter
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

from crypto_utils import decrypt_bytes


class SignatureError(Exception):
    """Raised when signing fails (wrong password, invalid cert, etc.)."""


def _load_signer(encrypted_p12_path: Path, p12_password: str):
    """Decrypt cert to a 0600 temp file, load, then delete the temp file.

    pyhanko's load_pkcs12 only accepts a path, so we cannot keep it in memory only.
    We minimize exposure by using a 0600 file in /tmp and deleting it immediately.
    """
    try:
        encrypted_blob = encrypted_p12_path.read_bytes()
        p12_bytes = decrypt_bytes(encrypted_blob)
    except Exception as e:
        raise SignatureError(f"No se pudo descifrar el certificado: {e}")

    fd, tmp_path = tempfile.mkstemp(suffix=".p12", prefix="cert_")
    try:
        os.write(fd, p12_bytes)
        os.close(fd)
        os.chmod(tmp_path, 0o600)
        try:
            signer = signers.SimpleSigner.load_pkcs12(
                pfx_file=tmp_path,
                passphrase=(p12_password.encode("utf-8") if p12_password else None),
            )
        except Exception as e:
            raise SignatureError(
                "Contraseña del certificado incorrecta, o el certificado no es válido."
            ) from e
        if signer is None:
            raise SignatureError("Contraseña del certificado incorrecta.")
        return signer
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def sign_pdf(
    input_pdf_path: Path,
    output_pdf_path: Path,
    encrypted_p12_path: Path,
    p12_password: str,
) -> Path:
    """Sign a PDF, writing the signed PDF to output_pdf_path.

    Args:
        input_pdf_path: source unsigned PDF
        output_pdf_path: where to write the signed PDF
        encrypted_p12_path: path to the encrypted .p12 file on disk
        p12_password: certificate password (provided by user, never stored)

    Raises:
        SignatureError: on any signing problem
    """
    signer = _load_signer(encrypted_p12_path, p12_password)

    # Visible signature box in the lower-right corner of the page.
    # Letter page is 612 x 792 pts. Origin = bottom-left.
    sig_box = (340, 30, 575, 110)

    try:
        with open(input_pdf_path, "rb") as inf:
            writer = IncrementalPdfFileWriter(inf)
            fields.append_signature_field(
                writer,
                sig_field_spec=SigFieldSpec(
                    sig_field_name="Signature1",
                    on_page=0,
                    box=sig_box,
                ),
            )
            meta = signers.PdfSignatureMetadata(
                field_name="Signature1",
                subfilter=SigSeedSubFilter.PADES,
                reason="Firma de la factura",
                location="Espartinas, Sevilla, Spain",
            )
            with open(output_pdf_path, "wb") as outf:
                signers.sign_pdf(
                    writer,
                    signature_meta=meta,
                    signer=signer,
                    output=outf,
                )
    except SignatureError:
        raise
    except Exception as e:
        raise SignatureError(f"Error firmando el PDF: {e}") from e

    return output_pdf_path
