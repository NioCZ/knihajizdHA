"""Pandas Excel export for Kniha jízd."""

from __future__ import annotations

import json
from pathlib import Path
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


def export_excel(raw_path: Path, output_path: Path) -> dict[str, Any]:
    """Build a two-sheet xlsx file. This function must run in an executor."""
    import pandas as pd
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    with raw_path.open("r", encoding="utf-8") as file_handle:
        raw_document = json.load(file_handle)
    segments = raw_document.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("Raw data file does not contain a 'segments' list")

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
            summary_sheet.cell(row=row, column=6).number_format = "0.00"
            summary_sheet.cell(row=row, column=7).number_format = "0.00"

    return {
        "path": str(output_path),
        "days": len(summary_rows),
        "segments": len(segments),
    }


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
        route_nodes: list[str] = []
        if day_segments:
            route_nodes.append(str(day_segments[0].get("start_address") or ""))
            route_nodes.extend(
                str(segment.get("end_address") or "") for segment in day_segments
            )
        route_nodes = _deduplicate_adjacent(route_nodes)

        customers = _unique_nonempty(
            str(segment.get("purpose") or "")
            for segment in day_segments
            if segment.get("trip_type") != "private"
        )
        business_km = sum(
            _number(segment.get("distance_km"))
            for segment in day_segments
            if segment.get("trip_type") == "business"
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
                "Služební km": round(business_km, 3),
                "Soukromé km": round(private_km, 3),
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
        "start_odometer_km",
        "end_odometer_km",
        "distance_km",
        "start_address",
        "end_address",
        "start_latitude",
        "start_longitude",
        "end_latitude",
        "end_longitude",
        "purpose",
        "trip_type",
        "classification_source",
        "map_estimate",
        "map_address",
        "map_attribution",
        "map_candidates",
        "candidate_search_radius_m",
        "selected_map_candidate",
        "validation_error",
    ]
