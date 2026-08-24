"""
parsers.py — Extract raw text from .docx, .pdf, and .txt files.
"""

import io
from pathlib import Path


def parse_txt(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


def parse_docx(content: bytes) -> str:
    import mammoth

    result = mammoth.extract_raw_text(io.BytesIO(content))
    return str(result.value)


def parse_pdf(content: bytes) -> str:
    import pdfplumber

    text_parts = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
    return "\n".join(text_parts)


def extract_text(filename: str, content: bytes) -> str:
    """Dispatch to the right parser based on file extension."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":
        return parse_txt(content)
    elif suffix == ".docx":
        return parse_docx(content)
    elif suffix == ".pdf":
        return parse_pdf(content)
    else:
        raise ValueError(f"Type de fichier non pris en charge : {suffix}")
