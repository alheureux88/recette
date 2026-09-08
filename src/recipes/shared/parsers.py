"""
parsers.py — Extract raw text and images from .docx, .doc, .odt, .pdf, and .txt files.
"""

import io
import zipfile
from pathlib import Path
from typing import Any


def parse_txt(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


def parse_docx(content: bytes) -> str:
    import mammoth

    result = mammoth.extract_raw_text(io.BytesIO(content))
    return str(result.value)


def _decode_with_fallback(raw_bytes: bytes) -> str:
    """Decode bytes with automatic encoding detection.

    Tries UTF-8 first, then cp1252, then chardet as fallback.
    Returns the decoded string with the best encoding.
    """
    # Try UTF-8 first (standard on Linux/modern systems)
    result_utf8 = raw_bytes.decode("utf-8", errors="replace")
    replacement_count = result_utf8.count("\ufffd")

    if replacement_count > 5:
        # Too many bad chars, try cp1252 (Windows standard for old .doc files)
        result_cp1252 = raw_bytes.decode("cp1252", errors="replace")
        replacement_count_cp1252 = result_cp1252.count("\ufffd")

        if replacement_count_cp1252 < replacement_count:
            return result_cp1252
        else:
            # Still bad, use chardet as fallback
            import chardet

            detected = chardet.detect(raw_bytes)
            encoding = detected["encoding"] or "utf-8"
            return raw_bytes.decode(encoding, errors="replace")
    else:
        return result_utf8


def parse_doc(content: bytes) -> str:
    """Extract text from .doc files using textract (requires antiword)."""
    import os
    import tempfile

    try:
        import textract

        env_backup: dict[str, str] = {}
        home_bin = Path.home() / ".local" / "bin"
        if home_bin.exists():
            current_path = os.environ.get("PATH", "")
            if str(home_bin) not in current_path:
                env_backup["PATH"] = current_path
                os.environ["PATH"] = f"{home_bin}{os.pathsep}{current_path}"

        if "HOME" not in os.environ:
            env_backup["HOME"] = ""
            os.environ["HOME"] = str(Path.home())

        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            raw_bytes = textract.process(tmp_path)
            result = _decode_with_fallback(raw_bytes)

            # Post-process to fix common antiword encoding issues
            # antiword often mangles Unicode fractions and special chars
            # U+FFFD is the Unicode replacement character
            replacements = {
                "\ufffd": "é",  # Common mangling for é
                "?": "¾",  # Fraction ¾
                "½": "½",
                "¼": "¼",
                "¾": "¾",
            }
            for old, new in replacements.items():
                result = result.replace(old, new)

            return str(result)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            for key, val in env_backup.items():
                if val:
                    os.environ[key] = val
                else:
                    os.environ.pop(key, None)
    except ImportError:
        raise ValueError(
            "Le support des fichiers .doc nécessite la bibliothèque 'textract'. "
            "Installez-la avec: pip install textract"
        ) from None
    except Exception as e:
        error_msg = str(e)
        if "antiword" in error_msg.lower():
            raise ValueError(
                "Le support des fichiers .doc nécessite l'outil système 'antiword'. "
                "Installez-le avec: apt-get install antiword (Linux) ou "
                "brew install antiword (macOS). "
                "Voir: https://textract.readthedocs.io/en/latest/installation.html"
            ) from e
        raise ValueError(f"Erreur lors de la lecture du fichier .doc: {e}") from e


def parse_odt(content: bytes) -> str:
    """Extract text from ODT files, including headings, paragraphs, and list items."""
    try:
        from odf.opendocument import load

        doc = load(io.BytesIO(content))
        text_parts = []

        def get_text_recursive(node: Any) -> str:
            """Recursively extract text from a node and its children."""
            text = ""
            if hasattr(node, "data") and node.data:
                text += node.data
            if hasattr(node, "childNodes"):
                for child in node.childNodes:
                    text += get_text_recursive(child)
            return text

        # Extract all text elements: headings (text:h), paragraphs (text:p), and list items
        for element in doc.text.childNodes:
            if hasattr(element, "tagName"):
                tag_name = element.tagName

                # Handle headings and paragraphs
                if tag_name in ("text:h", "text:p"):
                    text = get_text_recursive(element)
                    if text.strip():
                        text_parts.append(text)

                # Handle lists
                elif tag_name == "text:list":
                    for list_item in element.childNodes:
                        if hasattr(list_item, "tagName") and list_item.tagName == "text:list-item":
                            text = get_text_recursive(list_item)
                            if text.strip():
                                text_parts.append(text)

        return "\n".join(text_parts)

    except ImportError:
        raise ValueError(
            "Le support des fichiers .odt nécessite la bibliothèque 'odfpy'. "
            "Installez-la avec: pip install odfpy"
        ) from None
    except Exception as e:
        raise ValueError(f"Erreur lors de la lecture du fichier .odt: {e}") from e


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
    elif suffix == ".doc":
        return parse_doc(content)
    elif suffix == ".odt":
        return parse_odt(content)
    elif suffix == ".pdf":
        return parse_pdf(content)
    else:
        raise ValueError(f"Type de fichier non pris en charge : {suffix}")


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp", ".emf", ".wmf"}


def _is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in _IMAGE_EXTS


def extract_images_docx(content: bytes) -> list[tuple[str, bytes]]:
    images: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in sorted(zf.namelist()):
            if name.startswith("word/media/") and _is_image(name):
                images.append((Path(name).name, zf.read(name)))
    return images


def extract_images_odt(content: bytes) -> list[tuple[str, bytes]]:
    images: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in sorted(zf.namelist()):
            if name.startswith("Pictures/") and _is_image(name):
                images.append((Path(name).name, zf.read(name)))
    return images


def extract_images_pdf(content: bytes) -> list[tuple[str, bytes]]:
    import fitz

    images: list[tuple[str, bytes]] = []
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        seen: set[int] = set()
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            for img_idx, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.alpha:
                    pix = fitz.Pixmap(doc, xref, alpha=False)
                img_bytes = pix.tobytes("png")
                img_hash = hash(img_bytes)
                if img_hash in seen:
                    continue
                seen.add(img_hash)
                fname = f"page{page_idx + 1}_img{img_idx + 1}.png"
                images.append((fname, img_bytes))
    finally:
        doc.close()
    return images


def extract_images_doc(content: bytes) -> list[tuple[str, bytes]]:
    import olefile

    images: list[tuple[str, bytes]] = []
    if not olefile.isOleFile(io.BytesIO(content)):
        return images

    ole = olefile.OleFileIO(content)
    try:
        for stream_path in ole.listdir():
            name = "/".join(stream_path)
            data = ole.openstream(stream_path).read()
            _scan_stream_for_images(data, name, images)
    finally:
        ole.close()
    return images


def _scan_stream_for_images(data: bytes, stream_name: str, images: list[tuple[str, bytes]]) -> None:
    import re

    _JPEG_START = re.compile(b"\xff\xd8\xff[\xe0\xe1\xe2\xe3\xdb]")
    _PNG_START = b"\x89PNG\r\n\x1a\n"
    _PNG_END = b"IEND"
    _GIF_START = re.compile(b"GIF8[79]a")
    _GIF_END = b"\x00\x3b"

    idx = len(images)

    for m in _JPEG_START.finditer(data):
        start = m.start()
        end = data.find(b"\xff\xd9", start + 2)
        if end != -1:
            img = data[start : end + 2]
            if len(img) > 100:
                idx += 1
                images.append((f"{stream_name}_img{idx}.jpg", img))

    pos = 0
    while True:
        start = data.find(_PNG_START, pos)
        if start == -1:
            break
        end = data.find(_PNG_END, start)
        if end != -1:
            end += 4 + 4
            img = data[start:end]
            if len(img) > 100:
                idx += 1
                images.append((f"{stream_name}_img{idx}.png", img))
        pos = start + 1

    for m in _GIF_START.finditer(data):
        start = m.start()
        end = data.find(_GIF_END, start + 6)
        if end != -1:
            img = data[start : end + 2]
            if len(img) > 100:
                idx += 1
                images.append((f"{stream_name}_img{idx}.gif", img))


def extract_images(filename: str, content: bytes) -> list[tuple[str, bytes]]:
    """Extract embedded images from a document file.

    Returns a list of (filename, image_bytes) tuples.
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        return extract_images_docx(content)
    elif suffix == ".doc":
        return extract_images_doc(content)
    elif suffix == ".odt":
        return extract_images_odt(content)
    elif suffix == ".pdf":
        return extract_images_pdf(content)
    return []
