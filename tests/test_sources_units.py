"""Phase 5 building blocks with no services: claim checking, number parsing, web page text, GitHub zip
filtering, and the private-address guard for links."""

import io
import socket
import zipfile

import pytest

from app.rag import claims, fetch, repo, webpage
from app.rag.store import Chunk

CHUNKS = [
    Chunk(11, "s1", "refund-policy.pdf", 0, 1, "Lab tests cancelled before sample collection: 80 percent is refunded.", 0.7),
    Chunk(12, "s1", "refund-policy.pdf", 1, 2, "Approved refunds are paid within 5 working days.", 0.7),
]


@pytest.fixture
def fake_vectors(monkeypatch):
    """Sentences mentioning 'refund' or 'lab' point the same way as the chunks; anything else is orthogonal."""
    def vec(text):
        t = text.lower()
        return [1.0, 0.0] if ("refund" in t or "lab" in t or "test" in t) else [0.0, 1.0]
    monkeypatch.setattr(claims.embeddings, "embed", lambda texts: [vec(t) for t in texts])
    monkeypatch.setattr(claims.store, "chunk_vectors", lambda ids: {i: [1.0, 0.0] for i in ids})


# ---------- claims ----------
@pytest.mark.real_models
def test_supported_sentence_is_kept(fake_vectors):
    out = claims.verify("80 percent of a lab test is refunded [1].", CHUNKS)
    assert out["answer"] == "80 percent of a lab test is refunded [1]." and out["removed"] == 0
    assert out["claims"][0]["verdict"] == "supported" and out["claims"][0]["similarity"] == 1.0


@pytest.mark.real_models
def test_wrong_number_is_removed(fake_vectors):
    out = claims.verify("60 percent of a lab test is refunded [1]. Refunds take 5 working days [2].", CHUNKS)
    assert out["answer"] == "Refunds take 5 working days [2]."
    bad = out["claims"][0]
    assert bad["verdict"] == "unsupported" and "60" in bad["reason"]


@pytest.mark.real_models
def test_right_number_but_wrong_chunk_is_removed(fake_vectors):
    out = claims.verify("Refunds take 5 working days [1].", CHUNKS)  # the 5 days are in chunk [2]
    assert out["answer"] == "" and out["claims"][0]["verdict"] == "unsupported"


@pytest.mark.real_models
def test_off_topic_sentence_is_removed(fake_vectors):
    out = claims.verify("The hostel gate closes at night [1]. Lab refunds are 80 percent [1].", CHUNKS)
    assert out["answer"] == "Lab refunds are 80 percent [1]."
    assert "meaning" in out["claims"][0]["reason"]


@pytest.mark.real_models
def test_uncited_sentence_is_removed(fake_vectors):
    out = claims.verify("Lab refunds are 80 percent [1]. They are very generous.", CHUNKS)
    assert out["answer"] == "Lab refunds are 80 percent [1]." and out["claims"][1]["verdict"] == "uncited"


@pytest.mark.real_models
def test_telugu_and_hindi_digits_are_checked(fake_vectors):
    hindi_eighty = "८०"  # Devanagari 8 and 0
    out = claims.verify(f"lab test refund {hindi_eighty} percent [1].", CHUNKS)
    assert out["claims"][0]["verdict"] == "supported"
    out = claims.verify("lab test refund ౯౦ percent [1].", CHUNKS)  # Telugu 9 and 0
    assert out["claims"][0]["verdict"] == "unsupported"


@pytest.mark.parametrize("text,expected", [
    ("Rs. 3,500 per month", {"3500"}),
    ("closes at 9:30 PM", {"9", "30"}),
    ("within 05 days", {"5"}),
    ("1.5 percent", {"1.5"}),
    ("no numbers here", set()),
])
def test_numbers(text, expected):
    assert claims.numbers(text) == expected


# ---------- web pages ----------
def test_html_to_text_keeps_content_and_drops_scripts():
    html = """<html><head><title>Clinic FAQ</title><style>p{}</style></head><body>
      <nav>Home | About</nav><h1>Refunds</h1><p>Lab tests: 80 percent.</p>
      <script>var x = 1;</script><ul><li>Cash at the desk</li></ul><footer>(c) clinic</footer></body></html>"""
    title, text = webpage.html_to_text(html)
    assert title == "Clinic FAQ"
    assert text == "Refunds\n\nLab tests: 80 percent.\n\nCash at the desk"


# ---------- GitHub zip ----------
def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in files.items():
            zf.writestr(f"octo-demo-abc1234/{path}", data)
    return buf.getvalue()


def test_parse_github_url():
    assert repo.parse_url("https://github.com/octocat/Spoon-Knife") == ("octocat", "Spoon-Knife", None)
    assert repo.parse_url("https://github.com/a/b.git") == ("a", "b", None)
    assert repo.parse_url("https://github.com/a/b/tree/dev") == ("a", "b", "dev")
    with pytest.raises(repo.RepoError):
        repo.parse_url("https://gitlab.com/a/b")


def test_repo_keeps_text_files_and_skips_the_rest(monkeypatch):
    data = make_zip({
        "README.md": b"# Demo\nHello",
        "src/app.py": b"print('hi')",
        "node_modules/x/index.js": b"junk",
        ".git/config": b"junk",
        "logo.png": b"\x89PNG\x00\x00",
        "fake.txt": b"\x00\x01binary",
        "big.md": b"x" * (repo.MAX_FILE_BYTES + 1),
    })
    monkeypatch.setattr(repo, "fetch", lambda url, max_bytes, headers=None: fetch.Fetched(url, "application/zip", data))
    name, files = repo.download("https://github.com/octocat/demo")
    assert name == "octocat/demo@abc1234"
    assert sorted(f.path for f in files) == ["README.md", "src/app.py"]
    assert files[0].url.startswith("https://github.com/octocat/demo/blob/abc1234/")


# ---------- private-address guard ----------
@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "172.18.0.3", "192.168.1.9", "169.254.169.254", "::1"])
def test_private_addresses_are_blocked(monkeypatch, ip):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, (ip, 80))])
    with pytest.raises(fetch.BlockedURL):
        fetch.check_url("http://ollama:11434/api/tags")


def test_public_address_is_allowed(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.215.14", 443))])
    fetch.check_url("https://example.com/")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x", "https:///nohost"])
def test_only_http_links(url):
    with pytest.raises(fetch.BlockedURL):
        fetch.check_url(url)


@pytest.mark.real_models
def test_citation_after_the_full_stop_counts(fake_vectors):
    # "fact. [1]" is a common style: the marker belongs to the sentence before it.
    out = claims.verify("80 percent of a lab test is refunded. [1] Refunds take 5 working days. [2]", CHUNKS)
    assert [c["verdict"] for c in out["claims"]] == ["supported", "supported"]
    assert out["answer"] == "80 percent of a lab test is refunded. [1] Refunds take 5 working days. [2]"


def test_split_sentences_keeps_markers_with_their_sentence():
    assert claims.split_sentences("A is true. [1] B is true [2]. [3]") == ["A is true. [1]", "B is true [2]. [3]"]


# Regressions found by the language evaluation (eval/RESULTS.md, 2026-11-23)
def test_currency_and_title_abbreviations_do_not_end_a_sentence():
    assert claims.split_sentences("The consultation fee is Rs. 300 [1].") == ["The consultation fee is Rs. 300 [1]."]
    assert claims.split_sentences("Ask Dr. Rao at the desk [1]. It opens at 8 AM [2].") == \
        ["Ask Dr. Rao at the desk [1].", "It opens at 8 AM [2]."]
    assert claims.split_sentences("The mess fee is Rs. 3,500 per month [1].") == ["The mess fee is Rs. 3,500 per month [1]."]


@pytest.mark.real_models
def test_rs_amount_sentence_is_supported(fake_vectors):
    chunks = [Chunk(11, "s1", "refund-policy.pdf", 0, 1, "The consultation fee is Rs. 300. Refund in full.", 0.7)]
    out = claims.verify("The consultation refund fee is Rs. 300 [1].", chunks)
    assert [c["verdict"] for c in out["claims"]] == ["supported"]


def test_full_width_citation_brackets_are_read():
    from app.rag import citations
    chunks = [Chunk(1, "s1", "hostel-rules.pdf", 0, 1, "The gate closes at 9:30 PM.", 0.7)]
    out = citations.check("The hostel gate closes at 9:30 PM every day【1】.", chunks)
    assert out["answer"] == "The hostel gate closes at 9:30 PM every day[1]." and [c["n"] for c in out["citations"]] == [1]
    assert citations.check("Gate closes at 9:30 ［1］.", chunks)["citations"][0]["n"] == 1


# ---------- redirects are checked hop by hop ----------
def fake_dns(monkeypatch, table):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(None, None, None, None, (table[host], 80))])


def test_public_link_redirecting_to_an_internal_address_is_refused(monkeypatch):
    import httpx
    fake_dns(monkeypatch, {"docs.example.org": "93.184.215.14", "169.254.169.254": "169.254.169.254"})
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    with pytest.raises(fetch.BlockedURL):
        fetch.fetch("https://docs.example.org/page", 1000, transport=httpx.MockTransport(handler))
    assert seen == ["https://docs.example.org/page"]  # the internal address was never requested


def test_redirect_to_a_docker_service_name_is_refused(monkeypatch):
    import httpx
    fake_dns(monkeypatch, {"docs.example.org": "93.184.215.14", "ollama": "172.18.0.2"})
    transport = httpx.MockTransport(lambda r: httpx.Response(301, headers={"location": "http://ollama:11434/api/tags"}))
    with pytest.raises(fetch.BlockedURL):
        fetch.fetch("https://docs.example.org/page", 1000, transport=transport)


def test_public_redirect_chain_is_followed(monkeypatch):
    import httpx
    fake_dns(monkeypatch, {"docs.example.org": "93.184.215.14"})

    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>hello</p>")
    page = fetch.fetch("https://docs.example.org/old", 1000, transport=httpx.MockTransport(handler))
    assert page.url == "https://docs.example.org/new" and page.body == b"<p>hello</p>"


def test_redirect_loop_stops(monkeypatch):
    import httpx
    fake_dns(monkeypatch, {"docs.example.org": "93.184.215.14"})
    transport = httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "/again"}))
    with pytest.raises(fetch.FetchError, match="redirected more than"):
        fetch.fetch("https://docs.example.org/x", 1000, transport=transport)


def test_oversized_page_is_refused(monkeypatch):
    import httpx
    fake_dns(monkeypatch, {"docs.example.org": "93.184.215.14"})
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(fetch.FetchError, match="larger than"):
        fetch.fetch("https://docs.example.org/x", 1000, transport=transport)
