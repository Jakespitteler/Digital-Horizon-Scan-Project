from app.backend.utils.links import (
    find_added_links,
    find_link_difference,
    find_removed_links,
    is_document,
    separate_document_links,
)

PREVIOUS_LINKS_SAMPLE: list[str] = ["link1.com", "link2.com", "removed_link.com"]
CURRENT_LINKS_SAMPLE: list[str] = ["link1.com", "link2.com", "added_link.com"]


def test_find_added_links():
    assert set(find_added_links(PREVIOUS_LINKS_SAMPLE, CURRENT_LINKS_SAMPLE)) == {"added_link.com"}
    assert find_added_links(PREVIOUS_LINKS_SAMPLE, PREVIOUS_LINKS_SAMPLE) == []
    assert set(find_added_links([], PREVIOUS_LINKS_SAMPLE)) == set(PREVIOUS_LINKS_SAMPLE)


def test_find_removed_links():
    assert set(find_removed_links(PREVIOUS_LINKS_SAMPLE, CURRENT_LINKS_SAMPLE)) == {"removed_link.com"}
    assert find_removed_links(PREVIOUS_LINKS_SAMPLE, PREVIOUS_LINKS_SAMPLE) == []
    assert set(find_removed_links(PREVIOUS_LINKS_SAMPLE, [])) == set(PREVIOUS_LINKS_SAMPLE)


def test_find_link_difference():
    added, removed = find_link_difference(PREVIOUS_LINKS_SAMPLE, CURRENT_LINKS_SAMPLE)
    assert set(added) == {"added_link.com"}
    assert set(removed) == {"removed_link.com"}


def test_is_document():
    assert is_document("https://example.com/file.pdf") is True
    assert is_document("http://example.com/DOC.DOCX") is True
    assert is_document("https://example.com/archive.zip?download=true") is True
    assert is_document("https://example.com/path.to/file.TXT") is True
    assert is_document("https://example.com/page.html") is False
    assert is_document("https://example.com/no-extension") is False


def test_separate_document_links():
    links: list[str] = [
        "https://example.com/doc.pdf",
        "https://example.com/page",
        "http://example.com/sheet.XLSX",
        "https://example.com/image.png",
    ]
    docs, non_docs = separate_document_links(links)

    assert set(docs) == {
        "https://example.com/doc.pdf",
        "http://example.com/sheet.XLSX",
    }
    assert set(non_docs) == {
        "https://example.com/page",
        "https://example.com/image.png",  # TODO: Should we treat images differently
    }
