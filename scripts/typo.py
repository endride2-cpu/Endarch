"""Conservative typography normalization for Indonesian dialogue."""
import re

# Segmen yang TIDAK boleh disentuh: token gambar, URL, dan kode inline.
_PROTECT = re.compile(r"\[IMG:[^\]]+\]|\b(?:https?|ftp)://\S+|\bwww\.\S+|`[^`]*`")


def _normalize(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *— *", "—", text)
    text = re.sub(r"\.{3,}", "…", text)
    text = re.sub(r" +([,;.!?…])", r"\1", text)
    text = re.sub(r'''([!?,;:])(?=[^\s…—\d"”’'」』)\]!?.,;:])''', r"\1 ", text)
    return re.sub(r'''([!?.,…]["”’'」』])(?=[^\W\d_])''', r"\1 ", text)


def normalize_typo(text: str) -> str:
    """Normalisasi tipografi; token [IMG:...], URL, dan `kode` dibiarkan utuh."""
    out, pos = [], 0
    for m in _PROTECT.finditer(text):
        out.append(_normalize(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_normalize(text[pos:]))
    return "".join(out)