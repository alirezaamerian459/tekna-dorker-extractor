from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_TRANSLATION = str.maketrans({
    "ي": "ی",
    "ى": "ی",
    "ئ": "ی",
    "ك": "ک",
    "ۀ": "ه",
    "ة": "ه",
    "ـ": "",
    "\u200c": "\u200c",
    "\u200d": "",
    "\u200e": "",
    "\u200f": "",
})
_PERSIAN_SPECIFIC = set("پچژگک")
_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670]")
_SPACES = re.compile(r"\s+")
_WORD_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]+")
_PERSIAN_STOPWORDS = {
    "و", "در", "به", "از", "که", "برای", "این", "آن", "با", "را", "است",
    "من", "ما", "شما", "یک", "روی", "کرد", "می", "های", "نظر", "دیدگاه",
}


@dataclass(frozen=True, slots=True)
class LanguageResult:
    language: str
    confidence: float
    normalized_text: str
    persian_ratio: float
    arabic_ratio: float


def normalize_persian(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATION)
    text = _ARABIC_DIACRITICS.sub("", text)
    text = text.replace("\u00a0", " ")
    return _SPACES.sub(" ", text).strip()


def classify_persian(text: str, threshold: float = 0.45) -> LanguageResult:
    normalized = normalize_persian(text)
    letters = [char for char in normalized if char.isalpha()]
    if not letters:
        return LanguageResult("unknown", 0.0, normalized, 0.0, 0.0)
    arabic_letters = [char for char in letters if "\u0600" <= char <= "\u06ff"]
    persian_specific = sum(char in _PERSIAN_SPECIFIC for char in arabic_letters)
    words = _WORD_RE.findall(normalized)
    stopword_hits = sum(word in _PERSIAN_STOPWORDS for word in words)
    persian_ratio = len(arabic_letters) / len(letters)
    arabic_ratio = 1 - persian_ratio
    evidence = min(1.0, (persian_specific / max(1, len(arabic_letters))) * 1.8 + stopword_hits / max(1, len(words)) * 0.5)
    confidence = min(0.99, persian_ratio * 0.58 + evidence * 0.42)
    if persian_specific or stopword_hits:
        language = "fa" if confidence >= threshold else "uncertain"
    elif persian_ratio >= 0.82 and len(words) >= 3:
        language = "uncertain"
    else:
        language = "non-fa"
    return LanguageResult(language, round(confidence, 4), normalized, round(persian_ratio, 4), round(arabic_ratio, 4))
