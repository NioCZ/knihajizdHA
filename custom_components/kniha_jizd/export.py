"""Pandas Excel export for Kniha jízd."""

from __future__ import annotations

import json
from math import floor
from pathlib import Path
import re
from typing import Any


SUMMARY_COLUMNS = [
    "Datum",
    "Start/Odkud",
    "Přes",
    "Cíl/Kam",
    "Zákazník",
    "Služební km",
    "Soukromé km",
]


def export_excel(
    raw_path: Path, output_path: Path, month: str | None = None
) -> dict[str, Any]:
    """Build a two-sheet xlsx file, optionally restricted to one month."""
    import pandas as pd
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    with raw_path.open("r", encoding="utf-8") as file_handle:
        raw_document = json.load(file_handle)
    segments = raw_document.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("Raw data file does not contain a 'segments' list")
    if month is not None:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise ValueError("Month must use the YYYY-MM format")
        segments = [
            segment
            for segment in segments
            if isinstance(segment, dict) and _segment_belongs_to_month(segment, month)
        ]

    summary_rows = _build_summary_rows(segments)
    summary_frame = pd.DataFrame(summary_rows, columns=SUMMARY_COLUMNS)
    if not summary_frame.empty:
        summary_frame["Datum"] = pd.to_datetime(
            summary_frame["Datum"], errors="coerce"
        ).dt.date
    raw_frame = pd.DataFrame.from_records(segments)
    if raw_frame.empty:
        raw_frame = pd.DataFrame(columns=_raw_columns())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_frame.to_excel(writer, sheet_name="Kniha jízd", index=False)
        raw_frame.to_excel(writer, sheet_name="Raw data", index=False)

        for worksheet in writer.book.worksheets:
            worksheet.sheet_view.showGridLines = False
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            worksheet.row_dimensions[1].height = 24
            for cell in worksheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F4E78")
                cell.alignment = Alignment(horizontal="center", vertical="center")

            for column_cells in worksheet.columns:
                max_length = max(
                    (len(str(cell.value)) for cell in column_cells if cell.value is not None),
                    default=0,
                )
                column_letter = get_column_letter(column_cells[0].column)
                worksheet.column_dimensions[column_letter].width = min(
                    max(max_length + 2, 11), 65
                )
                for cell in column_cells[1:]:
                    cell.alignment = Alignment(vertical="top", wrap_text=True)

        summary_sheet = writer.book["Kniha jízd"]
        for row in range(2, summary_sheet.max_row + 1):
            summary_sheet.cell(row=row, column=1).number_format = "yyyy-mm-dd"
            summary_sheet.cell(row=row, column=6).number_format = "0"
            summary_sheet.cell(row=row, column=7).number_format = "0"

    return {
        "path": str(output_path),
        "month": month,
        "days": len(summary_rows),
        "segments": len(segments),
    }


def _segment_belongs_to_month(segment: dict[str, Any], month: str) -> bool:
    """Return whether the segment's local date belongs to the selected month."""
    date = str(segment.get("date") or _date_from_timestamp(segment.get("started_at")))
    return date.startswith(f"{month}-")


def _build_summary_rows(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate raw segments by local calendar date."""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for segment in segments:
        date = str(segment.get("date") or _date_from_timestamp(segment.get("started_at")))
        by_date.setdefault(date, []).append(segment)

    rows: list[dict[str, Any]] = []
    for date in sorted(by_date):
        day_segments = sorted(
            by_date[date], key=lambda item: str(item.get("started_at", ""))
        )
        business_segments = [
            segment
            for segment in day_segments
            if segment.get("trip_type") == "business"
        ]
        route_nodes: list[str] = []
        if business_segments:
            route_nodes.append(
                str(business_segments[0].get("start_address") or "")
            )
            route_nodes.extend(
                str(segment.get("end_address") or "")
                for segment in business_segments
            )
        route_nodes = _deduplicate_adjacent(route_nodes)

        customers = _unique_nonempty(
            str(segment.get("purpose") or "")
            for segment in business_segments
        )
        business_km = sum(
            _number(segment.get("distance_km"))
            for segment in business_segments
        )
        private_km = sum(
            _number(segment.get("distance_km"))
            for segment in day_segments
            if segment.get("trip_type") == "private"
        )

        rows.append(
            {
                "Datum": date,
                "Start/Odkud": route_nodes[0] if route_nodes else "",
                "Přes": " → ".join(route_nodes[1:-1]),
                "Cíl/Kam": route_nodes[-1] if route_nodes else "",
                "Zákazník": ", ".join(customers),
                "Služební km": _whole_km(business_km),
                "Soukromé km": _whole_km(private_km),
            }
        )
    return rows


def _date_from_timestamp(value: Any) -> str:
    """Use the ISO date prefix only as compatibility fallback."""
    text = str(value or "")
    return text[:10] if len(text) >= 10 else "Neznámé datum"


def _number(value: Any) -> float:
    """Coerce a JSON value to a summable float."""
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _whole_km(value: float) -> int:
    """Round exported kilometre totals to a whole number."""
    return int(floor(max(0.0, value) + 0.5))


def _deduplicate_adjacent(values: list[str]) -> list[str]:
    """Remove empty and immediately repeated route nodes."""
    result: list[str] = []
    for value in values:
        stripped = value.strip()
        if stripped and (not result or result[-1] != stripped):
            result.append(stripped)
    return result


def _unique_nonempty(values: Any) -> list[str]:
    """Return nonempty values in first-seen order."""
    result: list[str] = []
    for value in values:
        stripped = value.strip()
        if stripped and stripped not in result:
            result.append(stripped)
    return result


def _raw_columns() -> list[str]:
    """Keep Raw data useful even before the first recorded segment."""
    return [
        "id",
        "date",
        "started_at",
        "ended_at",
        "odometer_updated_at",
        "odometer_wait_timed_out",
        "odometer_ready",
        "odometer_shared_update",
        "odometer_completion_source",
        "start_odometer_km",
        "end_odometer_km",
        "distance_km",
        "distance_km_raw",
        "distance_hint_km",
        "distance_reconciliation_source",
        "distance_rounding_method",
        "distance_anchor_start_km",
        "distance_anchor_end_km",
        "odometer_reconciliation_boundary_km",
        "odometer_reconciliation_boundary_source",
        "odometer_anchor_ignored_due_to_daily_conflict",
        "daily_odometer_override_reason",
        "daily_odometer_authoritative_total_km",
        "manual_distance_override",
        "start_address",
        "end_address",
        "start_address_raw",
        "end_address_raw",
        "start_address_manual",
        "end_address_manual",
        "start_latitude",
        "start_longitude",
        "end_latitude",
        "end_longitude",
        "purpose",
        "trip_type",
        "classification_source",
        "classification_prepared",
        "classification_ready",
        "classification_options",
        "persisted",
        "manually_edited_at",
        "notification_sent_at",
        "notification_tag",
        "journey_id",
        "journey_segment_count",
        "journey_distance_km",
        "journey_distance_complete",
        "journey_role",
        "journey_inherited_from_segment_id",
        "transient_stop",
        "transient_continuation",
        "continued_by_segment_id",
        "continuation",
        "return_of_segment_id",
        "return_context",
        "matched_place_id",
        "return_destination_label",
        "map_estimate",
        "map_address",
        "map_attribution",
        "map_candidates",
        "candidate_search_radius_m",
        "selected_map_candidate",
        "configured_place",
        "configured_place_match",
        "end_location_ready",
        "end_location_source",
        "end_location_captured_at",
        "end_location_initial_address",
        "end_location_initial_address_raw",
        "end_location_initial_latitude",
        "end_location_initial_longitude",
        "location_update_requested_at",
        "validation_error",
    ]
