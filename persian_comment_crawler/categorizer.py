from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from .persian import classify_persian


ARTICLE_CATEGORIES = (
    "politics", "economy", "technology", "science", "health", "sports",
    "culture", "society", "education", "travel", "lifestyle", "business",
    "religion", "opinion", "other",
)

# Short, high-signal vocabulary keeps this classifier fast and explainable. The
# title receives a larger weight than the body; URL sections are a small tie-breaker.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "politics": ("دولت", "مجلس", "انتخابات", "وزیر", "رئیس جمهور", "دیپلماسی", "سیاست", "پارلمان", "government", "election", "minister", "politics"),
    "economy": ("اقتصاد", "تورم", "بورس", "بازار", "قیمت", "ارز", "بانک", "دلار", "سهام", "economy", "inflation", "market", "currency"),
    "technology": ("فناوری", "تکنولوژی", "نرم افزار", "هوش مصنوعی", "اینترنت", "موبایل", "گوشی", "اپلیکیشن", "کامپیوتر", "technology", "software", "artificial intelligence", "internet"),
    "science": ("علم", "دانشگاه", "پژوهش", "تحقیقات", "آزمایش", "نجوم", "فضا", "science", "research", "astronomy"),
    "health": ("سلامت", "پزشکی", "درمان", "بیمارستان", "بیماری", "دارو", "پزشک", "health", "medical", "hospital", "treatment"),
    "sports": ("فوتبال", "ورزش", "لیگ", "مسابقه", "بازیکن", "مربی", "قهرمانی", "sports", "football", "league", "match"),
    "culture": ("فرهنگ", "سینما", "فیلم", "موسیقی", "کتاب", "هنر", "بازیگر", "فرهنگی", "culture", "cinema", "music", "film"),
    "society": ("جامعه", "اجتماعی", "مردم", "خانواده", "شهروند", "آسیب اجتماعی", "society", "social", "community"),
    "education": ("آموزش", "مدرسه", "دانش آموز", "دانشجو", "کنکور", "معلم", "education", "school", "student", "university"),
    "travel": ("سفر", "گردشگری", "گردشگر", "هتل", "تور", "مقصد", "travel", "tourism", "hotel", "destination"),
    "lifestyle": ("سبک زندگی", "غذا", "آشپزی", "مد", "خانه", "خودرو", "lifestyle", "fashion", "recipe", "food", "home"),
    "business": ("کسب و کار", "استارتاپ", "شرکت", "مدیرعامل", "سرمایه گذاری", "فروش", "business", "startup", "company", "investment"),
    "religion": ("دین", "مذهبی", "قرآن", "نماز", "رمضان", "religion", "islam", "quran"),
    "opinion": ("یادداشت", "تحلیل", "دیدگاه", "سرمقاله", "نظر", "opinion", "analysis", "editorial", "commentary"),
}

PATH_HINTS = {
    "politics": ("politic", "politics", "سیاست"),
    "economy": ("economy", "economic", "اقتصاد", "business"),
    "technology": ("tech", "technology", "فناوری"),
    "sports": ("sport", "sports", "ورزش"),
    "health": ("health", "سلامت", "پزشکی"),
    "culture": ("culture", "فرهنگ", "هنر"),
    "travel": ("travel", "tourism", "گردشگری"),
}


@dataclass(frozen=True, slots=True)
class ArticleClassification:
    primary_category: str
    categories: tuple[str, ...]
    confidence: float
    quality_score: float


def _keyword_present(text: str, keyword: str) -> bool:
    if re.search(r"[a-zA-Z]", keyword):
        return re.search(rf"(?<![a-zA-Z]){re.escape(keyword.casefold())}(?![a-zA-Z])", text.casefold()) is not None
    return keyword in text


def classify_article(title: str, body: str, url: str = "") -> ArticleClassification:
    """Classify an article locally with explainable multi-label scoring."""
    clean_title = re.sub(r"\s+", " ", title).strip()
    clean_body = re.sub(r"\s+", " ", body).strip()
    title_text = clean_title.casefold()
    body_text = clean_body.casefold()
    path_text = unquote(urlsplit(url).path.casefold())
    scores: dict[str, float] = {category: 0.0 for category in ARTICLE_CATEGORIES if category != "other"}
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if _keyword_present(title_text, keyword):
                scores[category] += 3.0
            elif _keyword_present(body_text, keyword):
                scores[category] += 1.0
    for category, hints in PATH_HINTS.items():
        if any(hint in path_text for hint in hints):
            scores[category] += 1.5
    ranked = sorted(((score, category) for category, score in scores.items() if score > 0), reverse=True)
    if not ranked:
        categories = ("other",)
        confidence = 0.25
    else:
        top_score = ranked[0][0]
        categories = tuple(category for score, category in ranked if score >= max(2.0, top_score * 0.35))[:3]
        confidence = min(0.98, 0.42 + min(0.5, top_score / 20.0) + (0.06 if len(ranked) > 1 else 0.0))
    language = classify_persian(f"{clean_title} {clean_body}")
    quality = 0.0
    if 8 <= len(clean_title) <= 180:
        quality += 0.25
    if len(clean_body) >= 240:
        quality += 0.25
    if len(clean_body) >= 700:
        quality += 0.25
    if language.language == "fa":
        quality += 0.25
    elif language.language == "uncertain":
        quality += 0.12
    return ArticleClassification(categories[0], categories, round(confidence, 3), round(min(1.0, quality), 3))
