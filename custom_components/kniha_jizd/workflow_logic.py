"""Pure decision helpers shared by the phone workflow and administration panel."""

from __future__ import annotations

from typing import Any


PHONE_NOTIFICATION_GRACE_MINUTES = 10.0


def map_candidates(segment: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """Return compact, valid destination suggestions in their ranked order."""
    raw_candidates = segment.get("map_candidates")
    if not isinstance(raw_candidates, list):
        return []
    candidates: list[dict[str, Any]] = []
    for raw in raw_candidates:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            continue
        candidates.append(
            {
                "index": len(candidates) + 1,
                "name": str(raw["name"]).strip(),
                "distance_m": _number(raw.get("distance_m")),
                "score": _number(raw.get("score")),
                "contains_parking_point": bool(raw.get("contains_parking_point")),
                "keyword_matches": [
                    str(value)
                    for value in raw.get("keyword_matches", [])
                    if isinstance(value, str) and value.strip()
                ],
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def mobile_notification_policy(
    segment: dict[str, Any],
) -> tuple[bool, str]:
    """Decide whether a pending question is useful enough to interrupt the phone."""
    stop = segment.get("transient_stop")
    if isinstance(stop, dict) and stop.get("expired"):
        return False, "short_stop_panel_only"

    if segment.get("known_place_exception"):
        return True, "known_mixed_place"

    return_context = segment.get("return_context")
    if (
        isinstance(return_context, dict)
        and return_context.get("suggested")
        and (
            return_context.get("previous_segment_id")
            or return_context.get("previous_purpose")
        )
    ):
        return True, "business_return_context"

    if segment.get("configured_place") == "home":
        return True, "configured_home_choice"

    candidates = map_candidates(segment, limit=1)
    if not candidates:
        return False, "no_actionable_map_candidate"
    candidate = candidates[0]
    distance = _number(candidate.get("distance_m"))
    score = _number(candidate.get("score")) or 0.0
    confident = bool(
        candidate.get("contains_parking_point")
        or candidate.get("keyword_matches")
        or score >= 25.0
    )
    if confident and (distance is None or distance <= 1_000.0):
        return True, "confident_map_candidate"
    return False, "low_confidence_map_candidate"


def panel_question(segment: dict[str, Any], status: str) -> dict[str, Any] | None:
    """Build one safe action model for an unresolved trip in the admin panel."""
    if status != "waiting_classification" or segment.get("classification_ready"):
        return None

    estimate = str(
        segment.get("map_estimate")
        or segment.get("end_address")
        or "Neznámý cíl"
    ).strip()
    candidates = map_candidates(segment)
    return_context = segment.get("return_context")
    stop = segment.get("transient_stop")
    known_exception = bool(segment.get("known_place_exception"))

    if known_exception:
        kind = "known_place_exception"
        title = "Potvrďte typ známého místa"
        prompt = f"Místo {estimate} používáte služebně i soukromě."
    elif isinstance(stop, dict) and stop.get("expired"):
        kind = "short_stop"
        title = "Byla krátká zastávka samostatným cílem?"
        prompt = (
            f"Zastavení {estimate} vypadalo jako mezizastávka, ale cesta "
            "zatím nepokračovala."
        )
    elif isinstance(return_context, dict) and return_context.get("suggested"):
        kind = "return"
        title = "Jak zařadit navazující jízdu?"
        previous = str(return_context.get("previous_purpose") or "").strip()
        prompt = (
            f"Jízda může být návratem po návštěvě {previous}."
            if previous
            else f"Cíl {estimate} může být služební návrat nebo jiná cesta."
        )
    else:
        kind = "destination"
        title = "Kam tato jízda patří?"
        prompt = f"Rozpoznaný cíl: {estimate}."

    actions: list[dict[str, Any]] = []
    if kind == "return" and isinstance(return_context, dict) and (
        return_context.get("previous_segment_id")
        or return_context.get("previous_purpose")
    ):
        actions.append({"id": "return", "label": "Služební návrat"})

    if known_exception:
        actions.append({"id": "confirm", "label": "Služební"})
    else:
        actions.extend(
            {
                "id": "confirm",
                "label": f"Použít {candidate['name']}",
                "candidate_index": candidate["index"],
            }
            for candidate in candidates
        )
        actions.append({"id": "business", "label": "Služební bez klienta"})
        actions.append(
            {"id": "new", "label": "Jiný klient / účel", "requires_value": True}
        )
    actions.append({"id": "private", "label": "Soukromá"})

    if segment.get("notification_sent_at"):
        phone_state = "sent"
    elif segment.get("notification_suppressed_reason"):
        phone_state = "panel_only"
    elif segment.get("notification_due_at"):
        phone_state = "waiting"
    else:
        phone_state = "not_scheduled"

    return {
        "kind": kind,
        "title": title,
        "prompt": prompt,
        "estimate": estimate,
        "previous_purpose": (
            return_context.get("previous_purpose")
            if isinstance(return_context, dict)
            else None
        ),
        "candidates": candidates,
        "actions": actions,
        "phone_state": phone_state,
        "phone_reason": segment.get("notification_reason")
        or segment.get("notification_suppressed_reason"),
    }


def _number(value: Any) -> float | None:
    """Return one finite-enough JSON number without raising."""
    try:
        parsed = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if parsed is None or parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed
