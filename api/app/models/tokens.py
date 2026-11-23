"""Estimates token counts when a provider does not report them. Uses the cl100k_base tokenizer
(tiktoken); every model splits text a little differently, so these numbers are always labelled
"estimated". If tiktoken is unavailable, falls back to a rough characters-per-token rule."""

_encoding = None


def _tokenizer():
    global _encoding
    if _encoding is None:
        try:
            import tiktoken
            _encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:  # not installed, or the tokenizer file cannot be downloaded
            _encoding = False
    return _encoding


def estimate(text: str) -> int:
    enc = _tokenizer()
    if enc:
        return len(enc.encode(text))
    # Rough rule: about 4 characters per token in English, far fewer for Indic scripts.
    indic = sum(0x0900 <= ord(ch) <= 0x0D7F for ch in text)
    return max(1, round((len(text) - indic) / 4 + indic / 1.5)) if text else 0
