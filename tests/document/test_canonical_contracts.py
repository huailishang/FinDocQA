from document import CanonicalBlock, CanonicalBlockType, CanonicalDocument, CanonicalPage


def test_document_helpers() -> None:
    block = CanonicalBlock(
        block_id="d::p1::b0",
        page_number=1,
        block_type=CanonicalBlockType.TEXT,
        text="hello",
    )
    page = CanonicalPage(page_number=1, text="hello", blocks=(block,))
    doc = CanonicalDocument(
        document_id="d",
        domain="test",
        title="doc",
        source_type="markdown",
        source_uri="doc.md",
        parser_name="test",
        parser_version="1",
        pages=(page,),
    )
    assert doc.page_count == 1
    assert doc.page(1) == page
    assert tuple(doc.iter_blocks()) == (block,)
