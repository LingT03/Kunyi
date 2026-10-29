"""Structural and test-wiseness lint for kunyi JSON decks.

Each rule is a pure function over one raw JSON card entry (or over a whole
deck) returning ``Finding`` objects. Rules target the defects that make a
card measure something other than recall: answer leakage, position and
length cues, multi-target clozes, and feedback that is only a citation.
They are deterministic heuristics: they flag candidates for review rather
than prove a card is bad, and thresholds are module constants so they can
be recalibrated against hand-labelled samples.

Usage::

    python -m kunyi.lint jsons/*.json [--format text|json] [--fail-on error|warn]

Exit code is 1 when any finding at or above ``--fail-on`` is reported.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import statistics
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kunyi.card_types import CARD_KINDS

#: Maximum distinct cloze numbers per note before CLZ-010 warns.
MAX_CLOZES_PER_NOTE: int = 2
#: Maximum TeX-stripped deletion length (characters) before CLZ-011 warns.
MAX_DELETION_CHARS: int = 40
#: Maximum TeX-stripped cloze text length before CLZ-012 warns.
MAX_CLOZE_TEXT_CHARS: int = 250
#: Key length / median distractor length ratio above which MCQ-010 warns.
MAX_KEY_LENGTH_RATIO: float = 1.3
#: Content-word Jaccard similarity at or above which DECK-002 warns.
DUPLICATE_JACCARD: float = 0.6
#: Minimum explanation length (after removing bracketed citations).
MIN_EXPLANATION_CHARS: int = 15

Entry = dict[str, Any]

_CLOZE = re.compile(r"\{\{c(\d+)::(.*?)(?:::([^{}]*?))?\}\}", re.DOTALL)
_TEX_CMD = re.compile(r"\\[a-zA-Z]+")
_ABSOLUTE = re.compile(r"\b(only|always|never|all|none|every|must)\b", re.IGNORECASE)
_JUSTIFIED_KEY = re.compile(
    r"\s--\s|\s\u2014\s|\bbecause\b|\bsince\b|^(yes|no)\b|\bby (rank-nullity|definition|the [a-z-]+ theorem)",
    re.IGNORECASE,
)
_ABOVE = re.compile(r"\b(all|none) of the above\b", re.IGNORECASE)
_TAIL_RESTATEMENT = re.compile(r"\s(--|\u2014)\s|\bi\.e\.|\bthat is\b|\bequivalently\b", re.IGNORECASE)
_YES_NO_START = re.compile(
    r"^\s*(is|are|does|do|can|must|will|should|was|has|have)\b", re.IGNORECASE
)
_ASKS_FOR_REASON = re.compile(
    r"\b(why|justify|explain|give|show|witness|example|counterexample|prove|which|what)\b",
    re.IGNORECASE,
)
_STOP = frozenset(
    "the a an of to in is are and or for on with by as be that this it its if then "
    "from at which what when where there their not no any all each".split()
)


@dataclass(frozen=True)
class Finding:
    """One lint result.

    Attributes
    ----------
    rule:
        Rule id, e.g. ``"CLZ-001"``.
    severity:
        ``"error"`` (blocks a clean build) or ``"warn"`` (review).
    card:
        Card id, or ``"#<index>"`` when the card has no id.
    message:
        Human-readable explanation.
    file:
        Source file name (filled in by ``lint_file``).
    """

    rule: str
    severity: str
    card: str
    message: str
    file: str = ""


def strip_tex(text: str) -> str:
    """Normalise text for comparison: drop TeX commands, delimiters and HTML.

    Parameters
    ----------
    text:
        Raw card text.

    Returns
    -------
    str
        Lower-cased text with TeX commands, braces, ``\\(``/``\\[`` markers
        and HTML tags removed and whitespace collapsed.
    """
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\\[()\[\]]", " ", text)
    text = _TEX_CMD.sub(" ", text)
    text = re.sub(r"[{}_^$\\]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _content_words(text: str) -> set[str]:
    """Return the set of content words (length > 2, not stop words)."""
    return {w for w in re.findall(r"[a-z0-9]+", strip_tex(text)) if len(w) > 2 and w not in _STOP}


def _card_label(entry: Entry, index: int) -> str:
    """Return the id used to report a card."""
    return str(entry.get("id") or f"#{index}")


def _mcq_parts(entry: Entry) -> tuple[str, list[str]]:
    """Return (key, distractors) for a v1 or v2 MCQ entry."""
    if "answer" in entry:
        dis = [d["text"] if isinstance(d, dict) else d for d in entry.get("distractors", [])]
        return entry["answer"], dis
    key = entry.get("correct_answer", "")
    return key, [c for c in entry.get("choices", []) if c != key]


def _text_of(entry: Entry) -> str:
    """Return the card's prompt-side text for duplicate detection."""
    if entry.get("type") == "cloze":
        return _CLOZE.sub(lambda m: m.group(2), entry.get("text", ""))
    prompt = entry.get("prompt", entry.get("question", ""))
    return f"{prompt} {entry.get('answer', entry.get('correct_answer', ''))}"


# ---------------------------------------------------------------------------
# Card-level rules
# ---------------------------------------------------------------------------


def rule_metadata(entry: Entry, label: str) -> list[Finding]:
    """ALL-001 missing id; ALL-002 citation-only explanation; ALL-004 bad kind."""
    out: list[Finding] = []
    if not entry.get("id"):
        out.append(Finding("ALL-001", "error", label, "card has no stable id"))
    explanation = entry.get("explanation", entry.get("extra", "")) or ""
    body = re.sub(r"\[[^\]]*\]", "", explanation).strip()
    if len(strip_tex(body)) < MIN_EXPLANATION_CHARS:
        out.append(Finding("ALL-002", "error", label, "explanation is empty or only a citation"))
    kind = entry.get("kind")
    if kind is not None and kind not in CARD_KINDS:
        out.append(Finding("ALL-004", "error", label, f"unknown kind {kind!r}"))
    return out


def rule_mcq(entry: Entry, label: str) -> list[Finding]:
    """MCQ-001 structure; MCQ-010 length cue; MCQ-011 absolutes; MCQ-012 justified key; MCQ-013 'of the above'."""
    if entry.get("type", "mcq") != "mcq":
        return []
    out: list[Finding] = []
    key, dis = _mcq_parts(entry)
    if len(dis) < 2 or key in dis or len(set(dis)) != len(dis):
        out.append(Finding("MCQ-001", "error", label, "needs a unique key and >= 2 distinct distractors"))
        return out
    key_len = len(strip_tex(key))
    med = statistics.median(len(strip_tex(d)) for d in dis)
    if med > 0 and key_len / med > MAX_KEY_LENGTH_RATIO:
        out.append(Finding("MCQ-010", "warn", label, f"key is {key_len / med:.2f}x the median distractor length"))
    key_abs = bool(_ABSOLUTE.search(strip_tex(key)))
    dis_abs = sum(bool(_ABSOLUTE.search(strip_tex(d))) for d in dis)
    if dis_abs and not key_abs and dis_abs == len(dis) or (key_abs and dis_abs == 0):
        out.append(Finding("MCQ-011", "warn", label, "absolute words appear asymmetrically between key and distractors"))
    if _JUSTIFIED_KEY.search(key):
        out.append(Finding("MCQ-012", "warn", label, "key contains its own justification or a yes/no lead"))
    if any(_ABOVE.search(c) for c in [key, *dis]):
        out.append(Finding("MCQ-013", "warn", label, "uses 'all/none of the above'"))
    return out


def rule_cloze(entry: Entry, label: str) -> list[Finding]:
    """CLZ-001 leak; CLZ-010 too many clozes; CLZ-011 long deletion; CLZ-012 long text; CLZ-013 restatement; CLZ-014 brace collision."""
    if entry.get("type") != "cloze":
        return []
    out: list[Finding] = []
    text = entry.get("text", "")
    matches = list(_CLOZE.finditer(text))
    for m in matches:
        body = m.group(2)
        if body.count("{") != body.count("}"):
            out.append(Finding("CLZ-014", "error", label, f"unbalanced braces in deletion c{m.group(1)} (TeX '}}}}' collision?)"))
    numbers = {m.group(1) for m in matches}
    if len(numbers) > MAX_CLOZES_PER_NOTE:
        out.append(Finding("CLZ-010", "warn", label, f"{len(numbers)} cloze numbers in one note"))
    outside = strip_tex(_CLOZE.sub(" ", text))
    for m in matches:
        ans = strip_tex(m.group(2))
        if len(ans) > MAX_DELETION_CHARS:
            out.append(Finding("CLZ-011", "warn", label, f"deletion c{m.group(1)} is {len(ans)} chars"))
        if len(re.sub(r"[^a-z0-9]", "", ans)) >= 4 and re.search(rf"(?<![a-z0-9]){re.escape(ans)}(?![a-z0-9])", outside):
            out.append(Finding("CLZ-001", "error", label, f"answer of c{m.group(1)} also appears outside the deletion"))
    if len(strip_tex(_CLOZE.sub(lambda m: m.group(2), text))) > MAX_CLOZE_TEXT_CHARS:
        out.append(Finding("CLZ-012", "warn", label, "cloze text is long"))
    if matches and _TAIL_RESTATEMENT.search(text[matches[-1].end():]):
        out.append(Finding("CLZ-013", "warn", label, "text after the last deletion restates it"))
    return out


def rule_qa(entry: Entry, label: str) -> list[Finding]:
    """QA-010 bare yes/no prompt; QA-011 several questions in one prompt."""
    if entry.get("type") not in {"qa", "basic"}:
        return []
    out: list[Finding] = []
    prompt = entry.get("prompt", entry.get("front", ""))
    if _YES_NO_START.search(prompt) and not _ASKS_FOR_REASON.search(prompt):
        out.append(Finding("QA-010", "warn", label, "yes/no prompt without asking for a reason or witness"))
    if prompt.count("?") > 1:
        out.append(Finding("QA-011", "warn", label, "prompt asks more than one question"))
    if not str(entry.get("answer", entry.get("back", ""))).strip():
        out.append(Finding("QA-001", "error", label, "answer is empty"))
    return out


CARD_RULES: tuple[Callable[[Entry, str], list[Finding]], ...] = (
    rule_metadata,
    rule_mcq,
    rule_cloze,
    rule_qa,
)


# ---------------------------------------------------------------------------
# Deck-level rules
# ---------------------------------------------------------------------------


def rule_duplicate_ids(entries: list[tuple[str, Entry]]) -> list[Finding]:
    """ALL-003: the same id used twice across the linted files."""
    seen: dict[str, str] = {}
    out: list[Finding] = []
    for fname, e in entries:
        cid = e.get("id")
        if not cid:
            continue
        if cid in seen:
            out.append(Finding("ALL-003", "error", cid, f"duplicate id (also in {seen[cid]})", fname))
        seen[cid] = fname
    return out


def rule_near_duplicates(entries: list[tuple[str, Entry]]) -> list[Finding]:
    """DECK-002: near-duplicate cards by content-word Jaccard similarity.

    O(n^2) over cards; fine for decks of a few thousand cards.
    """
    words = [(f, e, _content_words(_text_of(e))) for f, e in entries]
    out: list[Finding] = []
    for (fa, a, wa), (fb, b, wb) in itertools.combinations(words, 2):
        if len(wa) < 4 or len(wb) < 4:
            continue
        j = len(wa & wb) / len(wa | wb)
        if j >= DUPLICATE_JACCARD:
            out.append(
                Finding("DECK-002", "warn", str(a.get("id")), f"near-duplicate of {b.get('id')} ({fb}), J={j:.2f}", fa)
            )
    return out


def lint_entries(entries: Iterable[tuple[str, Entry]]) -> list[Finding]:
    """Run every card- and deck-level rule.

    Parameters
    ----------
    entries:
        (file name, raw card entry) pairs. Retired notes should be excluded.

    Returns
    -------
    list[Finding]
        All findings, errors first.
    """
    entries = list(entries)
    out: list[Finding] = []
    for i, (fname, e) in enumerate(entries):
        label = _card_label(e, i)
        for rule in CARD_RULES:
            out.extend(Finding(f.rule, f.severity, f.card, f.message, fname) for f in rule(e, label))
    out.extend(rule_duplicate_ids(entries))
    out.extend(rule_near_duplicates(entries))
    return sorted(out, key=lambda f: (f.severity != "error", f.file, f.rule, f.card))


def lint_files(paths: Iterable[Path]) -> list[Finding]:
    """Lint the ``cards`` of one or more JSON deck files together.

    Parameters
    ----------
    paths:
        JSON deck files.

    Returns
    -------
    list[Finding]
        Findings across all files (ids and duplicates are checked jointly).
    """
    entries: list[tuple[str, Entry]] = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        entries.extend((Path(p).name, e) for e in data["cards"])
    return lint_entries(entries)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    ap = argparse.ArgumentParser(prog="kunyi-lint", description="Lint kunyi JSON decks.")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--fail-on", choices=["error", "warn"], default="error")
    args = ap.parse_args(argv)
    findings = lint_files(args.files)
    if args.format == "json":
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        for f in findings:
            print(f"{f.file}:{f.card}: {f.severity} {f.rule} {f.message}")
        n_err = sum(f.severity == "error" for f in findings)
        print(f"{n_err} errors, {len(findings) - n_err} warnings", file=sys.stderr)
    failing = {"error"} if args.fail_on == "error" else {"error", "warn"}
    return int(any(f.severity in failing for f in findings))


if __name__ == "__main__":
    sys.exit(main())
