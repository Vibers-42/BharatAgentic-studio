"""Phase 3 building blocks with no services: PDF text per page, chunking, and the citation checker."""

import pytest

from app.rag import chunker, citations
from app.rag.pdf import extract_pages
from app.rag.prompt import DONT_KNOW
from app.rag.store import Chunk
from pdfgen import HOSTEL_RULES, REFUND_POLICY, make_pdf


# ---------- PDF ----------
def test_extract_pages_keeps_page_numbers(tmp_path):
    path = tmp_path / "policy.pdf"
    path.write_bytes(make_pdf(REFUND_POLICY))
    pages = extract_pages(str(path))
    assert [p for p, _ in pages] == [1, 2]
    assert "Rs. 300" in pages[0][1]
    assert "5 working days" in pages[1][1]


def test_pdf_without_text_gives_no_pages(tmp_path):
    path = tmp_path / "blank.pdf"
    path.write_bytes(make_pdf([[]]))
    assert extract_pages(str(path)) == []


# ---------- chunker ----------
def test_chunks_respect_size_and_keep_pages():
    pages = [(1, " ".join(REFUND_POLICY[0])), (2, " ".join(REFUND_POLICY[1]))]
    chunks = chunker.chunk_pages(pages)
    assert {p for p, _ in chunks} == {1, 2}
    limit = chunker.CHUNK_CHARS + chunker.OVERLAP_CHARS + 1
    assert all(0 < len(text) <= limit for _, text in chunks)


def test_chunks_overlap():
    text = " ".join(f"Sentence number {i} is here." for i in range(60))
    chunks = chunker.chunk_text(text)
    assert len(chunks) > 2
    for a, b in zip(chunks, chunks[1:]):
        assert b[:30].split(" ")[1] in a  # the next chunk starts with words from the end of this one


def test_very_long_sentence_is_split():
    text = "word " * 400
    assert all(len(c) <= chunker.CHUNK_CHARS + chunker.OVERLAP_CHARS + 1 for c in chunker.chunk_text(text))


def test_short_text_is_one_chunk():
    assert chunker.chunk_text(" ".join(HOSTEL_RULES[0][:3])) == [" ".join(" ".join(HOSTEL_RULES[0][:3]).split())]


def test_hindi_sentence_end_is_respected():
    text = ("यह पहला वाक्य है। " * 60).strip()
    assert all(c.endswith("।") or c.endswith("है") for c in chunker.chunk_text(text))


# ---------- citations ----------
def make_chunks(n: int) -> list[Chunk]:
    return [Chunk(i, "s1", "policy.pdf", i, 1, f"text {i}", 0.8) for i in range(n)]


def test_valid_citations_are_kept_in_order_of_use():
    out = citations.check("Fee is Rs. 300 [2]. Paid in 5 days [1][2].", make_chunks(3))
    assert [c["n"] for c in out["citations"]] == [2, 1]
    assert out["citations"][0]["file"] == "policy.pdf" and out["citations"][0]["chunk"] == 1
    assert out["invalid_markers"] == []


def test_invented_citation_numbers_are_removed():
    out = citations.check("Fee is Rs. 300 [7]. Paid in 5 days [1].", make_chunks(2))
    assert out["answer"] == "Fee is Rs. 300 . Paid in 5 days [1]."
    assert [c["n"] for c in out["citations"]] == [1]
    assert out["invalid_markers"] == [7]


def test_comma_list_citations():
    out = citations.check("Both say so [1, 3, 8].", make_chunks(3))
    assert out["answer"] == "Both say so [1][3]."
    assert out["invalid_markers"] == [8]


def test_answer_with_only_invented_citations_has_none():
    assert citations.check("Made up [5].", make_chunks(2))["citations"] == []


@pytest.mark.parametrize("lang", ["en", "te", "hi"])
def test_dont_know_detected_in_every_language(lang):
    assert citations.is_dont_know(DONT_KNOW[lang])
    assert citations.is_dont_know("  " + DONT_KNOW[lang].rstrip(".।") + "  ")


def test_normal_answer_is_not_dont_know():
    assert not citations.is_dont_know("The consultation fee is Rs. 300 [1].")


def test_answer_of_only_markers_has_no_citations():
    # A small model once replied just "[2]": that states nothing, so it must not count as supported.
    assert citations.check("[2]", make_chunks(3))["citations"] == []
    assert citations.check(" [1][2]. ", make_chunks(3))["citations"] == []


def test_overlap_is_whole_sentences_so_boundary_facts_stay_complete():
    # Regression (2026-11-23): a fixed 80-character overlap started chunk 1 with "test amount is refunded",
    # half of the 80-percent sentence, and a model cited that half.
    chunks = chunker.chunk_text(" ".join(REFUND_POLICY[0]))
    fact = "If a lab test is cancelled before the sample is collected, 80 percent of the test amount is refunded."
    for c in chunks[1:]:
        assert c[0].isupper(), f"chunk starts mid-sentence: {c[:40]!r}"
    assert all(fact in c for c in chunks if "test amount is refunded" in c)


def test_rs_amount_does_not_split_a_chunk_sentence():
    from app.rag import sentences
    assert sentences.split("The fee is Rs. 300. It is refunded in full.") == ["The fee is Rs. 300.", "It is refunded in full."]
