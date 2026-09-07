"""Turn messy shopper text into something the agents and catalogue can use.

Customers type like they text: missing letters, swapped keys, Hindi/English
in the same sentence. This layer is deterministic (eval-safe) and is applied
before intent routing and product search. A live model then writes the reply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Longest phrases first. Hinglish / Hindi retail talk + common English slips.
_PHRASES: tuple[tuple[str, str], ...] = (
    ("cash on delivry", "cash on delivery"),
    ("cash on dlivery", "cash on delivery"),
    ("cash on delivery", "cash on delivery"),
    ("ki keemat kya hai", "what is the price of"),
    ("ki keemat batao", "what is the price of"),
    ("ki keemat", "price"),
    ("ki price kya hai", "what is the price of"),
    ("kitne ka hai", "what is the price"),
    ("kitne ki hai", "what is the price"),
    ("kitna hai", "what is the price"),
    ("kitna hain", "what is the price"),
    ("kya rate hai", "what is the price"),
    ("price batao", "tell me the prices"),
    ("price bata", "tell me the prices"),
    ("daam kya hai", "what is the price"),
    ("search karo", "search for"),
    ("search kar", "search for"),
    ("dhundho", "search for"),
    ("dhoondo", "search for"),
    ("dikha do", "show me"),
    ("dikhaao", "show me"),
    ("dikhao", "show me"),
    ("dikhhao", "show me"),
    ("i tot place", "i want to place"),
    ("i tot", "i want to"),
    ("plce order", "place order"),
    ("place oder", "place order"),
    ("order karo", "place order"),
    ("order karna", "place order"),
    ("mujhe chahiye", "i want"),
    ("lena hai", "i want to buy"),
    ("le na hai", "i want to buy"),
    ("kharidna hai", "i want to buy"),
    ("kharidna", "buy"),
    ("order kaha hai", "where is my order"),
    ("order kahaan hai", "where is my order"),
    ("mera order", "my order"),
    ("wapas karna", "return"),
    ("wapsi", "return"),
    ("vaapas", "return"),
    ("warranty kitne din", "warranty period"),
    ("i phone", "iphone"),
    ("i-phone", "iphone"),
    ("mac book", "macbook"),
    ("air pods", "airpods"),
    ("air pod", "airpods"),
    ("head phone", "headphone"),
    ("paymnt method", "payment method"),
    ("payment methd", "payment method"),
    # Devanagari
    ("की कीमत क्या है", "what is the price of"),
    ("कीमत", "price"),
    ("फोन", "phone"),
    ("आईफोन", "iphone"),
    ("लैपटॉप", "laptop"),
    ("ऑर्डर", "order"),
    ("वापसी", "return"),
    ("वारंटी", "warranty"),
    ("दिखाओ", "show me"),
    ("खोजो", "search for"),
    ("सस्ता", "cheap under"),
)

# Token replacements after phrase pass. Keys are lowercase.
_WORDS: dict[str, str] = {
    "oiphone": "iphone",
    "iphne": "iphone",
    "iphon": "iphone",
    "ifone": "iphone",
    "ipone": "iphone",
    "ipohne": "iphone",
    "iphpone": "iphone",
    "ipphne": "iphone",
    "eyephone": "iphone",
    "ifon": "iphone",
    "serch": "search",
    "seach": "search",
    "srch": "search",
    "saerch": "search",
    "serach": "search",
    "prce": "price",
    "prics": "price",
    "prise": "price",
    "priice": "price",
    "pric": "price",
    "pricese": "prices",
    "laptp": "laptop",
    "labtop": "laptop",
    "laptpo": "laptop",
    "lpatop": "laptop",
    "latop": "laptop",
    "leptop": "laptop",
    "macbok": "macbook",
    "macbokpro": "macbook",
    "hedphone": "headphone",
    "headfone": "headphone",
    "hedfone": "headphone",
    "airpod": "airpods",
    "airpods": "airpods",
    "galxy": "galaxy",
    "galxyy": "galaxy",
    "samsng": "samsung",
    "samsug": "samsung",
    "pixl": "pixel",
    "plce": "place",
    "oder": "order",
    "odrer": "order",
    "ordr": "order",
    "retrun": "return",
    "retrn": "return",
    "warrenty": "warranty",
    "waranty": "warranty",
    "paymnt": "payment",
    "paymet": "payment",
    "delivry": "delivery",
    "dlivery": "delivery",
    "chekout": "checkout",
    "checout": "checkout",
    "compar": "compare",
    "compair": "compare",
    "recomend": "recommend",
    "reccomend": "recommend",
    "under": "under",
    "sasta": "cheap",
    "mehnga": "expensive",
    "mehanga": "expensive",
    "phonee": "phone",
    "phne": "phone",
    "fone": "phone",
}

# Catalogue / intent vocabulary for fuzzy repair of leftover tokens.
_VOCAB: tuple[str, ...] = (
    "iphone", "macbook", "airpods", "galaxy", "pixel", "redmi", "samsung", "apple",
    "google", "sony", "boat", "dell", "lenovo", "asus", "acer", "xiaomi", "jbl",
    "laptop", "laptops", "phone", "phones", "headphone", "headphones", "monitor",
    "monitors", "speaker", "earbuds", "search", "price", "prices", "compare",
    "recommend", "order", "orders", "return", "refund", "warranty", "policy",
    "checkout", "payment", "delivery", "coupon", "discount", "budget", "under",
    "buy", "show", "find", "track", "cancel", "stock", "restock", "rating",
    "pending", "inventory",
)

_SKIP_FUZZY = {
    "a", "an", "the", "and", "or", "for", "me", "my", "i", "is", "it", "to",
    "of", "in", "on", "at", "ka", "ki", "ke", "hai", "hain", "ho", "kya",
    "ko", "se", "ye", "wo", "woh", "ek", "do", "with", "from", "this", "that",
    "give", "want", "need", "pls", "plz", "please", "bhai", "yaar",
}

_TOKEN = re.compile(r"[A-Za-z0-9\u0900-\u097F]+")
_ID = re.compile(r"\b([A-Z]{2,}-[A-Za-z0-9]{3,})\b")


@dataclass(frozen=True)
class Understood:
    original: str
    rewritten: str
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.rewritten.strip().lower() != (self.original or "").strip().lower()


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if abs(len(a) - len(b)) > 3:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (ca != cb)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def _fuzzy_word(token: str) -> str | None:
    if token in _SKIP_FUZZY or token.isdigit() or len(token) < 4:
        return None
    best = None
    best_d = 3
    for word in _VOCAB:
        d = _lev(token, word)
        if d < best_d and d <= (1 if len(token) <= 5 else 2):
            best, best_d = word, d
            if d == 0:
                break
    return best


def understand(text: str) -> Understood:
    original = text or ""
    work = original.strip()
    if not work:
        return Understood(original=original, rewritten="")
    notes: list[str] = []
    held: dict[str, str] = {}

    def _stash(match: re.Match) -> str:
        key = f"idkeep{len(held)}x"
        held[key] = match.group(1)
        return key

    protected = _ID.sub(_stash, work)
    low = protected.lower()
    for src, dst in _PHRASES:
        if src in low:
            low = low.replace(src, dst)
            notes.append(f"{src}→{dst}")

    parts: list[str] = []
    last = 0
    for m in _TOKEN.finditer(low):
        parts.append(low[last:m.start()])
        tok = m.group(0)
        if tok in held:
            parts.append(held[tok])
        elif tok in _WORDS:
            mapped = _WORDS[tok]
            if mapped != tok:
                notes.append(f"{tok}→{mapped}")
            parts.append(mapped)
        else:
            fuzzy = _fuzzy_word(tok)
            if fuzzy and fuzzy != tok:
                notes.append(f"{tok}→{fuzzy}")
                parts.append(fuzzy)
            else:
                parts.append(tok)
        last = m.end()
    parts.append(low[last:])
    rewritten = re.sub(r"\s+", " ", "".join(parts)).strip()
    for key, value in held.items():
        rewritten = rewritten.replace(key, value)
    return Understood(original=original, rewritten=rewritten or original, notes=notes)


def rewrite_query(text: str) -> str:
    return understand(text).rewritten
