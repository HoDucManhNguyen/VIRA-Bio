"""Local PDF ingestion with page anchors. No automatic downloading."""
import hashlib
import io
import re
from pathlib import Path

from pypdf import PdfReader


MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PAGES = 500


class ImportErrorPDF(ValueError):
    pass


def import_pdf(storage, payload: bytes, filename: str):
    if len(payload) > MAX_PDF_BYTES:
        raise ImportErrorPDF('PDF exceeds the 25 MB limit')
    if not payload.startswith(b'%PDF-'):
        raise ImportErrorPDF('File is not a PDF')
    digest = hashlib.sha256(payload).hexdigest()
    existing = storage.paper_by_hash(digest)
    if existing:
        return existing, False
    try:
        reader = PdfReader(io.BytesIO(payload), strict=True)
        if reader.is_encrypted:
            raise ImportErrorPDF('Encrypted PDFs are not supported')
        if len(reader.pages) > MAX_PAGES:
            raise ImportErrorPDF('PDF exceeds the 500-page limit')
        pages = [(number, page.extract_text() or '') for number, page in enumerate(reader.pages, 1)]
    except ImportErrorPDF:
        raise
    except Exception as exc:
        raise ImportErrorPDF('PDF could not be read') from exc
    if not any(text.strip() for _, text in pages):
        raise ImportErrorPDF('No selectable text found; scanned PDFs need OCR before import')
    safe_filename = Path(filename).name[:180]
    safe_filename = re.sub(r'[^\w .()\-]', '_', safe_filename, flags=re.UNICODE)
    title = Path(safe_filename).stem.strip() or 'Untitled paper'
    directory = Path(storage.path).parent / 'library'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f'{digest}.pdf'
    destination.write_bytes(payload)
    try:
        return storage.add_paper(title, safe_filename, digest, pages), True
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def pdf_path(storage, paper):
    return Path(storage.path).parent / 'library' / f"{paper['sha256']}.pdf"
