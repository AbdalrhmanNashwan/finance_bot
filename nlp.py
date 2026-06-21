"""
Lightweight regex-based parser for messages like:
  "spent 25k on lunch"
  "salary 1,200,000"
  "got 50000 from freelance"
  "paid 15k for taxi"

No AI call needed -- this covers the common phrasing patterns. If nothing
matches, the bot will fall back to asking the user to use /expense or /income.
"""

import re
from dataclasses import dataclass


@dataclass
class ParsedEntry:
    type_: str          # "expense" or "income"
    amount: int          # always positive, whole units
    description: str | None


EXPENSE_VERBS = r"(?:spent|paid|bought|buy)"
INCOME_VERBS = r"(?:salary|earned|received|got|income)"

# matches amounts like 25k, 1,200,000, 25000, 1.5k
AMOUNT_RE = r"(?P<amount>\d[\d,]*\.?\d*\s*k?)"

PATTERNS = [
    # "spent 25k on lunch" / "paid 15000 for taxi"
    (re.compile(rf"^{EXPENSE_VERBS}\s+{AMOUNT_RE}\s+(?:on|for)\s+(?P<desc>.+)$", re.I), "expense"),
    # "spent 25k lunch" (no preposition)
    (re.compile(rf"^{EXPENSE_VERBS}\s+{AMOUNT_RE}\s+(?P<desc>.+)$", re.I), "expense"),
    # "spent 25k"
    (re.compile(rf"^{EXPENSE_VERBS}\s+{AMOUNT_RE}$", re.I), "expense"),
    # "salary 1,200,000 IQD" / "got 50000 from freelance"
    (re.compile(rf"^{INCOME_VERBS}\s+{AMOUNT_RE}\s*(?:iqd|usd)?\s*(?:from\s+(?P<desc>.+))?$", re.I), "income"),
]


def _parse_amount(raw: str) -> int:
    raw = raw.strip().lower().replace(",", "").replace(" ", "")
    if raw.endswith("k"):
        return int(float(raw[:-1]) * 1000)
    return int(float(raw))


def parse_message(text: str) -> ParsedEntry | None:
    text = text.strip()
    for pattern, type_ in PATTERNS:
        m = pattern.match(text)
        if m:
            amount = _parse_amount(m.group("amount"))
            desc = None
            if "desc" in m.groupdict():
                desc = m.group("desc")
                if desc:
                    desc = desc.strip()
            return ParsedEntry(type_=type_, amount=amount, description=desc)
    return None


if __name__ == "__main__":
    tests = [
        "spent 25k on lunch",
        "salary 1,200,000 IQD",
        "paid 15000 for taxi",
        "got 50000 from freelance",
        "spent 5k coffee",
        "bought 30000 groceries",
        "earned 200k",
        "random message that matches nothing",
    ]
    for t in tests:
        print(t, "->", parse_message(t))
