"""Helpers for structured knowledge references and citations."""

from typing import Any, Iterable


KNOWLEDGE_RESULT_TYPE = "knowledge_result"
KNOWLEDGE_REFERENCES_KEY = "knowledge_references"
KNOWLEDGE_CITATIONS_KEY = "knowledge_citations"


def normalize_knowledge_references(value: Any) -> list[dict]:
    """Return valid, de-duplicated knowledge reference dictionaries."""
    if not isinstance(value, list):
        return []

    references: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        reference_id = item.get("reference_id")
        marker = item.get("citation_marker")
        if not isinstance(reference_id, str) or not reference_id:
            continue
        if not isinstance(marker, str) or not marker:
            continue
        if not isinstance(item.get("document_id"), int):
            continue
        if not isinstance(item.get("segment_id"), int):
            continue
        if reference_id in seen:
            continue
        seen.add(reference_id)
        references.append(dict(item))
    return references


def build_knowledge_result(content: str, references: list[dict]) -> dict:
    """Build the standard result envelope returned by knowledge tools."""
    return {
        "type": KNOWLEDGE_RESULT_TYPE,
        "content": content,
        KNOWLEDGE_REFERENCES_KEY: normalize_knowledge_references(references),
    }


def unpack_knowledge_result(value: Any) -> tuple[str, list[dict]] | None:
    """Parse a standard knowledge tool result envelope."""
    if not isinstance(value, dict) or value.get("type") != KNOWLEDGE_RESULT_TYPE:
        return None
    content = value.get("content")
    if not isinstance(content, str):
        return None
    references = normalize_knowledge_references(value.get(KNOWLEDGE_REFERENCES_KEY))
    return content, references


def filter_references_in_content(content: str, references: Any) -> list[dict]:
    """Keep only references whose markers are visible in the supplied text."""
    return [
        reference
        for reference in normalize_knowledge_references(references)
        if reference["citation_marker"] in content
    ]


def merge_knowledge_references(*groups: Iterable[dict]) -> list[dict]:
    """Merge reference groups while preserving first-seen order."""
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for reference in normalize_knowledge_references(list(group)):
            reference_id = reference["reference_id"]
            if reference_id in seen:
                continue
            seen.add(reference_id)
            merged.append(reference)
    return merged


def extract_message_knowledge_references(message: Any) -> list[dict]:
    """Extract candidate references stored on a LangChain message."""
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict):
        references = normalize_knowledge_references(
            artifact.get(KNOWLEDGE_REFERENCES_KEY)
        )
        if references:
            return references

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        return normalize_knowledge_references(
            additional_kwargs.get(KNOWLEDGE_REFERENCES_KEY)
        )
    return []


def extract_message_knowledge_citations(message: Any) -> list[dict]:
    """Extract validated citations stored on an AI message."""
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(additional_kwargs, dict):
        return []
    return normalize_knowledge_references(
        additional_kwargs.get(KNOWLEDGE_CITATIONS_KEY)
    )


def collect_message_knowledge_references(messages: Iterable[Any]) -> list[dict]:
    """Collect candidate references and prior citations from message history."""
    groups: list[list[dict]] = []
    for message in messages:
        groups.append(extract_message_knowledge_references(message))
        groups.append(extract_message_knowledge_citations(message))
    return merge_knowledge_references(*groups)


def validate_knowledge_citations(content: str, references: Any) -> list[dict]:
    """Return references cited in content, ordered by first marker occurrence."""
    matches: list[tuple[int, dict]] = []
    for reference in normalize_knowledge_references(references):
        position = content.find(reference["citation_marker"])
        if position >= 0:
            matches.append((position, reference))
    matches.sort(key=lambda item: item[0])
    return [reference for _, reference in matches]
