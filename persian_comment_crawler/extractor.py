from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .models import CommentRecord
from .persian import classify_persian


COMMENT_HINTS = ("comment", "comments", "comment-body", "comment-content", "review", "reply", "discussion", "نظر", "دیدگاه", "پاسخ")
TEXT_HINTS = ("comment-body", "comment-content", "comment-text", "review-text", "review-body", "ribbon-content", "appcomment__body", "app-comment-body", "entry-content", "text")
DATE_HINTS = ("date", "time", "published")
FORM_TEXT_HINTS = (
    "دیدگاهتان را بنویسید", "دیدگاه *", "نشانی ایمیل", "ذخیره نام و ایمیل",
    "اشتراک‌گذاری", "اشتراک گذاری", "بستن", "نام *", "ایمیل *",
    "leave a reply", "your email address", "required fields",
)
COMMENT_NOISE_EXACT = {
    "پاسخ", "reply", "reply to", "دیدگاه", "comment", "comments", "نظرات شما",
}
COMMENT_NOISE_PHRASES = (
    "اولین دیدگاه را شما ثبت کنید",
    "دیدگاه خود را",
    "نوشتن دیدگاه",
    "برای ثبت دیدگاه",
    "نظرات شما",
    "comments are closed",
    "write a comment",
    "leave a reply",
    "تولید شده با هوش مصنوعی",
    "خلاصه نظرات",
    "نظرات کاربران",
)
COMMENT_NOISE_PATTERNS = (
    re.compile(r"^[0-9۰-۹]+\s*(?:دیدگاه|comments?)$", re.IGNORECASE),
    re.compile(r"^[0-9۰-۹]+(?:[.,][0-9۰-۹]+)?\s*از\s*[0-9۰-۹]+$", re.IGNORECASE),
    re.compile(r"^[0-9۰-۹]+\s*(?:ستاره|star)s?$", re.IGNORECASE),
    re.compile(r"^[0-9۰-۹]+(?:\s+[0-9۰-۹]+)?\s*(?:پاسخ|reply)?\s*(?:[0-9۰-۹]+\s*)?(?:روز|هفته|ماه|سال)\s*(?:پیش|ago)?$", re.IGNORECASE),
    re.compile(r"^(?:[0-9۰-۹]+\s*)?(?:روز|هفته|ماه|سال)\s*(?:پیش|ago)$", re.IGNORECASE),
)
STRONG_TEXT_HINTS = (
    "comment-body", "comment-content", "comment-text", "review-text", "review-body", "ribbon-content", "appcomment__body", "app-comment-body", "commenttext",
)


@dataclass(slots=True)
class Node:
    tag: str
    attrs: dict[str, str]
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    def text(self) -> str:
        chunks: list[str] = []
        if self.text_parts:
            chunks.extend(self.text_parts)
        for child in self.children:
            chunks.append(child.text())
        return " ".join(chunk.strip() for chunk in chunks if chunk.strip())

    def marker(self) -> str:
        return " ".join((self.attrs.get("id", ""), self.attrs.get("class", ""), self.attrs.get("itemprop", ""))).lower()


class TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in {"meta", "link", "img", "input", "br", "hr", "source", "area", "base", "embed", "param", "track", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag.lower():
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].text_parts.append(data)


@dataclass(frozen=True, slots=True)
class ExtractedComment:
    text: str
    comment_url: str | None = None
    publication_date: str | None = None
    platform_id: str | None = None
    confidence: float = 0.0
    method: str = "html-heuristic"


@dataclass(frozen=True, slots=True)
class PageExtraction:
    comments: tuple[ExtractedComment, ...]
    links: tuple[str, ...]
    confidence: float
    method: str


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _node_has_hint(node: Node, hints: tuple[str, ...]) -> bool:
    marker = node.marker()
    return any(hint in marker for hint in hints)


def _has_ancestor_tag(node: Node, tags: set[str]) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.tag in tags:
            return True
        parent = parent.parent
    return False


def _find_attr(node: Node, names: tuple[str, ...]) -> str | None:
    for candidate in _walk(node):
        for name in names:
            if candidate.attrs.get(name):
                return candidate.attrs[name]
    return None


def _is_noise_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    if not normalized:
        return True
    if normalized in COMMENT_NOISE_EXACT:
        return True
    if any(phrase in normalized for phrase in COMMENT_NOISE_PHRASES):
        return True
    return any(pattern.fullmatch(normalized) for pattern in COMMENT_NOISE_PATTERNS)


def _has_strong_text_marker(node: Node | None) -> bool:
    if node is None:
        return False
    for candidate in _walk(node):
        marker = candidate.marker()
        if any(hint in marker for hint in STRONG_TEXT_HINTS):
            return True
        if candidate.attrs.get("itemprop", "").casefold() in {"commenttext", "reviewbody"}:
            return True
    return False


def _contains_structured_comment(text: str, comments: list[ExtractedComment]) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return any(len(existing.text) >= 20 and re.sub(r"\s+", " ", existing.text).strip().casefold() in normalized for existing in comments)


def _json_ld_comments(parser: TreeParser) -> list[ExtractedComment]:
    results: list[ExtractedComment] = []
    for node in _walk(parser.root):
        if node.tag != "script" or node.attrs.get("type", "").lower() not in {"application/ld+json", "application/json"}:
            continue
        try:
            data = json.loads(node.text())
        except (ValueError, TypeError):
            continue
        if isinstance(data, list):
            objects = data
        elif isinstance(data, dict) and isinstance(data.get("@graph"), list):
            objects = [data, *data["@graph"]]
        else:
            objects = [data]
        for item in objects:
            if not isinstance(item, dict):
                continue
            types = item.get("@type", "")
            type_names = {str(value).lower() for value in types} if isinstance(types, list) else {str(types).lower()}
            candidates: list[tuple[dict, str, str]] = []
            if "comment" in type_names:
                candidates.append((item, str(item.get("text") or item.get("commentText") or ""), "schema.org-jsonld"))
            if "review" in type_names:
                candidates.append((item, str(item.get("reviewBody") or item.get("reviewText") or item.get("text") or ""), "schema.org-review"))
            nested_reviews = item.get("review") or item.get("reviews") or []
            if isinstance(nested_reviews, dict):
                nested_reviews = [nested_reviews]
            if isinstance(nested_reviews, list):
                for review in nested_reviews:
                    if isinstance(review, dict):
                        candidates.append((review, str(review.get("reviewBody") or review.get("reviewText") or review.get("text") or ""), "schema.org-review"))
            for candidate, raw_text, method in candidates:
                text = raw_text.strip()
                if text and not _is_noise_text(text):
                    results.append(ExtractedComment(text, candidate.get("url"), candidate.get("datePublished") or candidate.get("dateCreated"), candidate.get("identifier") or candidate.get("@id"), .98, method))
    return results


def _extract_from_candidate(node: Node, base_url: str, method: str) -> ExtractedComment | None:
    text_node = next((child for child in _walk(node) if child is not node and _node_has_hint(child, TEXT_HINTS)), None)
    text = text_node.text() if text_node else node.text()
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 2 or len(text) > 10_000:
        return None
    if _is_noise_text(text) or any(marker in text.casefold() for marker in FORM_TEXT_HINTS):
        return None
    strong_marker = _has_strong_text_marker(text_node) or _has_strong_text_marker(node)
    # Generic comment wrappers often contain usernames, dates, reply links, or
    # counts. Keep short text only when it comes from an explicit body field.
    if not strong_marker and len(text) < 8:
        return None
    result = classify_persian(text)
    if result.language not in {"fa", "uncertain"}:
        return None
    permalink = _find_attr(node, ("data-url", "data-permalink", "href"))
    if permalink:
        permalink = urljoin(base_url, permalink)
    publication_date = _find_attr(node, ("datetime", "data-date", "title"))
    platform_id = _find_attr(node, ("data-comment-id", "data-id", "id"))
    ancestor = node.parent
    while not platform_id and ancestor is not None:
        platform_id = _find_attr(ancestor, ("data-comment-id", "data-id", "id"))
        ancestor = ancestor.parent
    return ExtractedComment(text, permalink, publication_date, platform_id, result.confidence, method)


def extract_page(html: str, page_url: str) -> PageExtraction:
    parser = TreeParser()
    try:
        parser.feed(html)
    except Exception:
        return PageExtraction((), (), 0.0, "invalid-html")

    comments = _json_ld_comments(parser)
    candidates: list[Node] = []
    for node in _walk(parser.root):
        if node.tag in {"script", "style", "nav", "header", "footer", "form"} or _has_ancestor_tag(node, {"form"}):
            continue
        marker = node.marker()
        if any(noise in marker for noise in ("review-summary", "aggregate-rating", "app-rating", "rating-summary")):
            continue
        if any(hint in marker for hint in COMMENT_HINTS):
            candidates.append(node)

    # Prefer the smallest marked nodes. This prevents a whole comment list from
    # being stored as one record when individual comment bodies are present.
    candidates.sort(key=lambda item: len(item.text()))
    seen: set[str] = {re.sub(r"\s+", " ", item.text).strip().casefold() for item in comments}
    for candidate in candidates:
        extracted = _extract_from_candidate(candidate, page_url, "html-heuristic")
        key = re.sub(r"\s+", " ", extracted.text).strip().casefold() if extracted else ""
        if extracted and key not in seen and not _contains_structured_comment(extracted.text, comments):
            comments.append(extracted)
            seen.add(key)

    links: list[str] = []
    for node in _walk(parser.root):
        if node.tag != "a" or not node.attrs.get("href"):
            continue
        href = urljoin(page_url, node.attrs["href"])
        if urlsplit(href).scheme in {"http", "https"}:
            links.append(href)
    confidence = min(1.0, max((comment.confidence for comment in comments), default=0.0) + (0.1 if candidates else 0.0))
    return PageExtraction(tuple(comments), tuple(dict.fromkeys(links)), confidence, "schema.org-jsonld+html-heuristic" if comments else "html-heuristic")
