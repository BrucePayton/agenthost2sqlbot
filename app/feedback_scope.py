"""Resolve the question boundary from persisted user inputs, including tool continuations."""

import json


def question_roots(messages) -> dict[str, str]:
    """Group chronological session messages; empty tool inputs continue the last question."""
    roots = {}
    current = None
    for message in messages:
        payload = json.loads(message.payload_json)
        # Files can be a real user question even when its text is empty.
        if message.event_type == "message.user" and (
            (payload.get("text") or "").strip() or payload.get("attachments")
            or payload.get("file_references") or current is None
        ):
            current = message.turn_id
        if current is not None:
            roots[message.turn_id] = current
    return roots
