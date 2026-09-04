"""Pure decision helpers shared by the phone workflow and administration panel."""

from __future__ import annotations

import re
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
    """Send exactly one classification question for every unresolved trip."""
    del segment
    return True, "classification_question"


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
    known_exception = bool(segment.get("known_place_exception"))

    if known_exception:
        kind = "known_place_exception"
        title = "Potvrďte typ známého místa"
        prompt = f"Místo {estimate} používáte služebně i soukromě."
    elif (
        isinstance(return_context, dict)
        and return_context.get("reason") == "configured_home_destination"
        and not return_context.get("previous_segment_id")
        and not return_context.get("previous_purpose")
    ):
        kind = "home"
        title = "Jak zařadit cestu domů?"
        prompt = "Dojeli jste domů. Byla tato jízda služební, nebo soukromá?"
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
        title = "Jaký typ měla tato jízda?"
        prompt = (
            f"Rozpoznaný cíl: {estimate}. Nejprve vyberte typ jízdy; "
            "uložení místa nabídneme samostatně."
        )

    actions: list[dict[str, Any]] = []
    if kind == "return" and isinstance(return_context, dict) and (
        return_context.get("previous_segment_id")
        or return_context.get("previous_purpose")
    ):
        actions.append({"id": "return", "label": "Služební návrat"})

    actions.append({"id": "business", "label": "Služební"})
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
        "purpose_input": True,
        "purpose_optional": True,
        "actions": actions,
        "phone_state": phone_state,
        "phone_reason": segment.get("notification_reason")
        or segment.get("notification_suppressed_reason"),
    }


def should_offer_place_save(
    segment: dict[str, Any],
    source: str,
    trip_type: str,
    place_radius_m: float | None = None,
) -> bool:
    """Offer a separate save-place choice only for an explicit real destination."""
    latitude = _number(segment.get("end_latitude"))
    longitude = _number(segment.get("end_longitude"))
    coordinates_available = bool(
        latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )
    coordinate_reliable = bool(
        coordinates_available
        and gps_accuracy_suitable(segment, place_radius_m)
    )
    address = str(segment.get("end_address") or "").strip()
    stable_address = bool(address and not _coordinate_address(address))
    return bool(
        trip_type in {"business", "private"}
        and source.startswith(("manual_panel", "notification"))
        and segment.get("journey_role") != "return"
        and segment.get("visit_role") != "waypoint"
        and segment.get("journey_role") != "transient_stop"
        and not segment.get("configured_place")
        and not segment.get("matched_place_id")
        and not segment.get("needs_review")
        and (coordinate_reliable or stable_address)
    )


def gps_accuracy_suitable(
    segment: dict[str, Any], radius_m: float | None
) -> bool:
    """Treat absent legacy accuracy as usable, but reject a known wider fix."""
    accuracy = _number(segment.get("end_accuracy_m"))
    if accuracy is None or accuracy < 0 or radius_m is None:
        return True
    return accuracy <= radius_m


def place_label_suggestion(
    segment: dict[str, Any], purpose: str, trip_type: str
) -> str:
    """Return a predictable label for the independent save-place question."""
    business_purpose = str(purpose or "").strip() if trip_type == "business" else ""
    return str(
        business_purpose
        or segment.get("map_estimate")
        or segment.get("end_address")
        or ("Soukromé místo" if trip_type == "private" else "Klient")
    ).strip()


def place_name_input_allowed(trip_type: str) -> bool:
    """Ask for a custom place/customer name only for business trips."""
    return trip_type == "business"


def _coordinate_address(value: str) -> bool:
    """Recognize the synthetic address used when only GPS is available."""
    return bool(re.fullmatch(r"-?\d+\.\d{6},\s*-?\d+\.\d{6}", value.strip()))


def _number(value: Any) -> float | None:
    """Return one finite-enough JSON number without raising."""
    try:
        parsed = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if parsed is None or parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed
