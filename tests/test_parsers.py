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
        extract_text("recipe.odt", b"some content")


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
