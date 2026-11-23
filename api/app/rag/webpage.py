"""Turns an HTML page into plain text paragraphs, with the standard-library HTML parser.
Scripts, styles, navigation and footers are skipped; block tags become paragraph breaks."""

from html.parser import HTMLParser

SKIP = {"script", "style", "noscript", "svg", "template", "nav", "footer", "iframe", "form"}
BLOCK = {"p", "div", "section", "article", "main", "li", "ul", "ol", "table", "tr", "br", "hr", "pre",
         "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "dd", "dt", "header", "aside"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, text) with paragraphs separated by blank lines."""
    parser = _Extractor()
    parser.feed(html)
    raw = "".join(parser.parts)
    paragraphs = [" ".join(p.split()) for p in raw.split("\n\n")]
    return " ".join(parser.title.split()), "\n\n".join(p for p in paragraphs if p)
