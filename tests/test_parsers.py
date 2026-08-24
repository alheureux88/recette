"""Tests for parsers.py — text extraction from txt, docx, pdf."""

import pytest

from recipes.parsers import extract_text, parse_txt


def test_parse_txt_utf8():
    content = "Ingrédients: farine, beurre, sucre".encode()
    assert "farine" in parse_txt(content)


def test_parse_txt_with_bad_bytes():
    # Should not crash — falls back to replacement chars
    content = b"hello \x80 world"
    result = parse_txt(content)
    assert "hello" in result
    assert "world" in result


def test_extract_text_txt():
    content = b"Simple recipe\nIngredients: eggs, milk"
    result = extract_text("recipe.txt", content)
    assert "eggs" in result


def test_extract_text_unsupported_extension():
    with pytest.raises(ValueError, match="Type de fichier non pris en charge"):
        extract_text("recipe.xyz", b"some content")


def test_extract_text_case_insensitive_extension():
    content = b"Title: Cake\nIngredients: flour"
    # Should not raise even with uppercase extension
    result = extract_text("RECIPE.TXT", content)
    assert "flour" in result


def test_extract_text_docx(tmp_path):
    """Integration test — build a minimal real docx and parse it."""
    pytest.importorskip("mammoth")
    import textwrap
    import zipfile

    docx_path = tmp_path / "test.docx"
    body_xml = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:r><w:t>Chocolate Cake recipe</w:t></w:r></w:p>
          </w:body>
        </w:document>
    """)
    rels_xml = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
    """)
    content_types_xml = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Override PartName="/word/document.xml"
            ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
        </Types>
    """)
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("word/_rels/document.xml.rels", rels_xml)
        zf.writestr("word/document.xml", body_xml)

    result = extract_text("test.docx", docx_path.read_bytes())
    assert "Chocolate Cake" in result


def test_extract_text_odt():
    """Test .odt parsing — requires odfpy."""
    pytest.importorskip("odf")
    from odf.opendocument import OpenDocumentText
    from odf.text import P

    doc = OpenDocumentText()
    p = P(text="Tarte au sucre recipe")
    doc.text.addElement(p)

    import io

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)

    result = extract_text("test.odt", buffer.read())
    assert "Tarte au sucre" in result


def test_extract_text_doc():
    """Test .doc parsing — requires textract and antiword."""
    pytest.importorskip("textract")
    # .doc files are complex binary format, so we just test that the function
    # can be called without crashing on invalid data
    from recipes.parsers import parse_doc

    # This will raise a ValueError because either antiword is not installed
    # or the content is not a valid .doc file
    with pytest.raises(ValueError, match="(antiword|Erreur lors de la lecture du fichier .doc)"):
        parse_doc(b"not a valid doc file")
