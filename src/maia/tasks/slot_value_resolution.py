from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_TEXT_BOUNDARY_CHARS = frozenset(
    " \t\r\n,.;:/\\|()[]{}<>\"'`~!?"
    "\uff0c\u3002\uff1b\uff1a\uff01\uff1f\u3001"
    "\u7684\u548c\u53ca\u4e0e\u6216\u770b\u67e5\u89c2\u8981\u60f3\u5e2e\u6211\u628a\u5230"
)


@dataclass(frozen=True)
class SlotCandidate:
    value: Any
    label: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlotCandidateSet:
    slot: str
    candidates: tuple[SlotCandidate, ...]
    multi: bool = False
    all_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedSlotValues:
    matched: tuple[Any, ...] = ()
    invalid: tuple[str, ...] = ()
    ambiguous: tuple[Any, ...] = ()
    unmatched: tuple[str, ...] = ()

    @property
    def first(self) -> Any | None:
        return self.matched[0] if self.matched else None


@dataclass(frozen=True)
class _TextMatch:
    start: int
    end: int
    value: Any


class MessageSlotResolver:
    def resolve_message(self, message: str, candidate_set: SlotCandidateSet) -> ResolvedSlotValues:
        if self._matches_any(message, candidate_set.all_aliases):
            return ResolvedSlotValues(matched=tuple(candidate.value for candidate in candidate_set.candidates))

        selected: list[_TextMatch] = []
        matches = sorted(
            self._candidate_matches(message, candidate_set),
            key=lambda item: (item.start, -(item.end - item.start)),
        )
        for match in matches:
            if any(_overlaps(match, existing) for existing in selected):
                continue
            selected.append(match)
        values = _unique(match.value for match in sorted(selected, key=lambda item: item.start))
        if not candidate_set.multi and len(values) > 1:
            return ResolvedSlotValues(ambiguous=values)
        return ResolvedSlotValues(matched=values)

    def resolve_value(self, value: Any, candidate_set: SlotCandidateSet) -> ResolvedSlotValues:
        matched: list[Any] = []
        invalid: list[str] = []
        for item in _items(value):
            if self._is_all_value(item, candidate_set):
                matched.extend(candidate.value for candidate in candidate_set.candidates)
                continue
            candidate = self._candidate_for_value(item, candidate_set)
            if candidate is None:
                text = _text(item)
                if text is not None:
                    invalid.append(text)
                continue
            matched.append(candidate.value)
        values = _unique(matched)
        if not candidate_set.multi and len(values) > 1:
            return ResolvedSlotValues(ambiguous=values)
        return ResolvedSlotValues(matched=values, invalid=tuple(dict.fromkeys(invalid)))

    def _candidate_matches(self, message: str, candidate_set: SlotCandidateSet) -> tuple[_TextMatch, ...]:
        matches: list[_TextMatch] = []
        for candidate in candidate_set.candidates:
            for text in _candidate_texts(candidate):
                for start, end in self._text_spans(message, text):
                    matches.append(_TextMatch(start, end, candidate.value))
        return tuple(matches)

    def _candidate_for_value(self, value: Any, candidate_set: SlotCandidateSet) -> SlotCandidate | None:
        for candidate in candidate_set.candidates:
            if value == candidate.value:
                return candidate
        text = _text(value)
        if text is None:
            return None
        normalized = text.casefold()
        for candidate in candidate_set.candidates:
            if normalized in {item.casefold() for item in _candidate_texts(candidate)}:
                return candidate
        return None

    def _is_all_value(self, value: Any, candidate_set: SlotCandidateSet) -> bool:
        text = _text(value)
        return text is not None and text.casefold() in {alias.casefold() for alias in candidate_set.all_aliases}

    def _matches_any(self, message: str, aliases: tuple[str, ...]) -> bool:
        return any(self._text_spans(message, alias) for alias in aliases)

    def _text_spans(self, message: str, candidate: str) -> tuple[tuple[int, int], ...]:
        needle = candidate.strip()
        if not needle:
            return ()
        haystack = message.casefold()
        normalized = needle.casefold()
        spans: list[tuple[int, int]] = []
        start = haystack.find(normalized)
        while start >= 0:
            end = start + len(normalized)
            if _left_boundary(message, start, needle) and _right_boundary(message, end, needle):
                spans.append((start, end))
            start = haystack.find(normalized, start + 1)
        return tuple(spans)


def _candidate_texts(candidate: SlotCandidate) -> tuple[str, ...]:
    texts = [candidate.label, *candidate.aliases]
    if isinstance(candidate.value, str):
        texts.append(candidate.value)
    return tuple(dict.fromkeys(text.strip() for text in texts if text and text.strip()))


def _items(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(value)
    return (value,)


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, dict):
        return None
    text = str(value).strip()
    return text or None


def _unique(values) -> tuple[Any, ...]:
    result: list[Any] = []
    for value in values:
        if not any(value == existing for existing in result):
            result.append(value)
    return tuple(result)


def _overlaps(left: _TextMatch, right: _TextMatch) -> bool:
    return left.start < right.end and right.start < left.end


def _left_boundary(text: str, index: int, candidate: str) -> bool:
    return index == 0 or _boundary_char(text[index - 1], candidate)


def _right_boundary(text: str, index: int, candidate: str) -> bool:
    return index >= len(text) or _boundary_char(text[index], candidate)


def _boundary_char(char: str, candidate: str) -> bool:
    if _ascii_token(candidate):
        return not (char.isascii() and (char.isalnum() or char in "_-"))
    if char.isascii() and char.isalnum():
        return True
    return char in _TEXT_BOUNDARY_CHARS


def _ascii_token(value: str) -> bool:
    return all(char.isascii() and (char.isalnum() or char in "_-") for char in value)


__all__ = [
    "MessageSlotResolver",
    "ResolvedSlotValues",
    "SlotCandidate",
    "SlotCandidateSet",
]
