from __future__ import annotations

import argparse
import io
import json
import logging
import os
import tempfile
import time
import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely import make_valid
from shapely.geometry import box
from shapely.ops import unary_union

LOGGER = logging.getLogger("gtfs_subsetter")
LOG_DIR = Path(__file__).cwd() / "logs"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
LOG_DATEFMT = "%H:%M:%S"
REQUIRED_COLUMNS = {
    "stops.txt": {"stop_id", "stop_lat", "stop_lon"},
    "stop_times.txt": {"trip_id", "stop_id"},
    "trips.txt": {"route_id", "service_id", "trip_id"},
    "routes.txt": {"route_id"},
}
PROJECTED_CRS = "EPSG:27700"
CONSERVATIVE_OMISSIONS = {
    "areas.txt", "booking_rules.txt", "fare_attributes.txt",
    "fare_leg_join_rules.txt", "fare_leg_rules.txt", "fare_media.txt",
    "fare_products.txt", "fare_rules.txt", "location_group_stops.txt",
    "location_groups.txt", "networks.txt", "route_networks.txt",
    "stop_areas.txt", "timeframes.txt", "translations.txt",
}


class GTFSError(ValueError):
    """Raised when source data or the resulting subset is invalid."""


def _count(value: int) -> str:
    return f"{value:,}"


def _read_csv(source: Any) -> pd.DataFrame:
    return pd.read_csv(
        source,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        encoding="utf-8-sig",
    )


def read_gtfs(source: Path) -> dict[str, pd.DataFrame]:
    """Read every GTFS text table from a ZIP or directory as strings."""
    source = source.resolve()
    if not source.exists():
        raise GTFSError(f"GTFS source does not exist: {source}")

    LOGGER.info("Reading GTFS feed: %s", source)
    tables: dict[str, pd.DataFrame] = {}

    if source.is_dir():
        files = sorted(source.glob("*.txt"))
        if not files:
            raise GTFSError(f"No GTFS .txt files found in directory: {source}")
        LOGGER.info("Found %d GTFS tables", len(files))
        for number, path in enumerate(files, start=1):
            started = time.perf_counter()
            LOGGER.info("Reading table %d/%d: %s", number, len(files), path.name)
            tables[path.name] = _read_csv(path)
            LOGGER.info(
                "Read %s: %s rows in %.2f seconds",
                path.name, _count(len(tables[path.name])), time.perf_counter() - started,
            )
    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            text_members = [
                name for name in archive.namelist()
                if not name.endswith("/") and Path(name).suffix.lower() == ".txt"
            ]
            grouped: dict[str, list[str]] = {}
            for member in text_members:
                grouped.setdefault(Path(member).name, []).append(member)
            duplicates = sorted(name for name, members in grouped.items() if len(members) > 1)
            if duplicates:
                raise GTFSError(f"Duplicate GTFS table names in ZIP: {duplicates}")
            members = {name: paths[0] for name, paths in grouped.items()}
            LOGGER.info("Found %d GTFS tables", len(members))
            for number, (name, member) in enumerate(sorted(members.items()), start=1):
                started = time.perf_counter()
                LOGGER.info("Reading table %d/%d: %s", number, len(members), name)
                with archive.open(member) as handle:
                    tables[name] = _read_csv(handle)
                LOGGER.info(
                    "Read %s: %s rows in %.2f seconds",
                    name, _count(len(tables[name])), time.perf_counter() - started,
                )
    else:
        raise GTFSError(f"GTFS source must be a directory or ZIP file: {source}")

    missing_files = sorted(set(REQUIRED_COLUMNS) - tables.keys())
    if missing_files:
        raise GTFSError(f"Missing required GTFS file(s): {', '.join(missing_files)}")

    errors = []
    for name, required in REQUIRED_COLUMNS.items():
        missing = sorted(required - set(tables[name].columns))
        if missing:
            errors.append(f"{name}: missing column(s) {', '.join(missing)}")
    if errors:
        raise GTFSError("Invalid required GTFS columns:\n- " + "\n- ".join(errors))

    LOGGER.info(
        "Finished reading GTFS: %d tables and %s total rows",
        len(tables), _count(sum(len(frame) for frame in tables.values())),
    )
    return tables


def prepare_boundary(
    boundary_path: Path | None,
    bbox: tuple[float, float, float, float] | None,
    bbox_crs: str | None,
    buffer_m: float,
) -> tuple[Any, dict[str, Any]]:
    """Load, validate, project and buffer a boundary, returning EPSG:4326 geometry."""
    LOGGER.info("Preparing spatial selection boundary")
    if (boundary_path is None) == (bbox is None):
        raise GTFSError("Supply exactly one of --boundary or --bbox.")
    if buffer_m < 0:
        raise GTFSError("--buffer-m cannot be negative.")

    if boundary_path is not None:
        boundary = gpd.read_file(boundary_path)
        source_label = str(boundary_path.resolve())
        if boundary.crs is None:
            raise GTFSError(f"Boundary CRS is undefined: {boundary_path}")
        LOGGER.info("Read boundary: %s features; CRS=%s", _count(len(boundary)), boundary.crs)
    else:
        if not bbox_crs:
            raise GTFSError("--bbox-crs is required when --bbox is used; CRS is never guessed.")
        min_x, min_y, max_x, max_y = bbox or (0, 0, 0, 0)
        if min_x >= max_x or min_y >= max_y:
            raise GTFSError("--bbox must be MIN_X MIN_Y MAX_X MAX_Y with increasing coordinates.")
        boundary = gpd.GeoDataFrame(
            geometry=[box(min_x, min_y, max_x, max_y)], crs=bbox_crs
        )
        source_label = f"bbox:{min_x},{min_y},{max_x},{max_y}"
        LOGGER.info("Created bounding-box boundary; CRS=%s", boundary.crs)

    original_crs = boundary.crs.to_string()
    if boundary.empty or boundary.geometry.isna().all() or boundary.geometry.is_empty.all():
        raise GTFSError("Boundary contains no usable geometry.")
    if not set(boundary.geom_type.dropna()).issubset({"Polygon", "MultiPolygon"}):
        raise GTFSError("Boundary must contain only Polygon or MultiPolygon geometry.")

    warnings: list[str] = []
    invalid_count = int((~boundary.geometry.is_valid).sum())
    if invalid_count:
        LOGGER.warning("Repairing %d invalid boundary geometries with make_valid", invalid_count)
        boundary = boundary.copy()
        boundary.geometry = boundary.geometry.map(make_valid)
        warnings.append(f"Repaired {invalid_count} invalid boundary geometries with make_valid.")
    if boundary.geometry.is_empty.any() or boundary.geometry.isna().any():
        raise GTFSError("Boundary contains empty geometry after validity repair.")

    LOGGER.info("Transforming boundary from %s to %s", original_crs, PROJECTED_CRS)
    projected = boundary.to_crs(PROJECTED_CRS)
    selection = unary_union(projected.geometry.array)
    if selection.is_empty:
        raise GTFSError("Unified boundary geometry is empty.")
    if buffer_m:
        LOGGER.info("Applying %.1f metre boundary buffer", buffer_m)
        selection = selection.buffer(buffer_m)
    else:
        LOGGER.info("No boundary buffer requested")

    selection_4326 = gpd.GeoSeries([selection], crs=PROJECTED_CRS).to_crs("EPSG:4326").iloc[0]
    LOGGER.info("Spatial selection boundary prepared")
    return selection_4326, {
        "source": source_label,
        "original_crs": original_crs,
        "projected_crs": PROJECTED_CRS,
        "buffer_m": float(buffer_m),
        "invalid_geometries_repaired": invalid_count,
        "warnings": warnings,
    }


def _nonempty(series: pd.Series) -> set[str]:
    return set(series[series.ne("")])


def _filter_optional(
    name: str,
    frame: pd.DataFrame,
    retained: dict[str, set[str]],
) -> tuple[pd.DataFrame | None, str]:
    filters = {
        "calendar.txt": ("service_id", retained["service_id"]),
        "calendar_dates.txt": ("service_id", retained["service_id"]),
        "frequencies.txt": ("trip_id", retained["trip_id"]),
        "shapes.txt": ("shape_id", retained["shape_id"]),
    }
    if name in filters:
        column, values = filters[name]
        if column not in frame:
            raise GTFSError(f"{name}: missing required reference column {column}")
        filtered = frame[frame[column].isin(values)].copy()
        LOGGER.info(
            "Filtered %s: %s -> %s rows", name, _count(len(frame)), _count(len(filtered))
        )
        return filtered, "filtered"
    if name == "feed_info.txt":
        LOGGER.info("Copied %s unchanged: %s rows", name, _count(len(frame)))
        return frame.copy(), "copied"
    if name in CONSERVATIVE_OMISSIONS:
        LOGGER.info("Omitting optional table %s: relationship not safely handled", name)
        return None, "omitted: relationship not handled conservatively"
    LOGGER.info("Omitting unhandled optional table %s", name)
    return None, "deferred"


def subset_gtfs(
    tables: dict[str, pd.DataFrame], selection_geometry: Any
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Select trips through qualifying stops and build a consistent dependency closure."""
    stops = tables["stops.txt"]
    stop_times = tables["stop_times.txt"]
    trips = tables["trips.txt"]
    routes = tables["routes.txt"]

    LOGGER.info(
        "Starting spatial subset: %s stops, %s stop times, %s trips and %s routes",
        _count(len(stops)), _count(len(stop_times)), _count(len(trips)), _count(len(routes)),
    )
    lon = pd.to_numeric(stops["stop_lon"], errors="coerce")
    lat = pd.to_numeric(stops["stop_lat"], errors="coerce")
    valid_coordinates = lon.between(-180, 180) & lat.between(-90, 90)
    malformed_coordinates = int((~valid_coordinates).sum())
    LOGGER.info(
        "Valid stop coordinates: %s; malformed or missing: %s",
        _count(int(valid_coordinates.sum())), _count(malformed_coordinates),
    )

    stop_points = gpd.GeoSeries(
        gpd.points_from_xy(lon[valid_coordinates], lat[valid_coordinates]), crs="EPSG:4326"
    )
    spatial_mask = pd.Series(False, index=stops.index)
    spatial_mask.loc[valid_coordinates] = stop_points.intersects(selection_geometry).to_numpy()
    qualifying_stop_ids = _nonempty(stops.loc[spatial_mask, "stop_id"])
    LOGGER.info(
        "Stops intersecting buffered boundary: %s of %s",
        _count(len(qualifying_stop_ids)), _count(len(stops)),
    )
    if not qualifying_stop_ids:
        raise GTFSError("No stops intersect the buffered boundary; no output was written.")

    selected_trip_ids = _nonempty(
        stop_times.loc[stop_times["stop_id"].isin(qualifying_stop_ids), "trip_id"]
    )
    LOGGER.info(
        "Complete trips selected: %s of %s",
        _count(len(selected_trip_ids)), _count(len(trips)),
    )
    if not selected_trip_ids:
        raise GTFSError("Qualifying stops are not used by any trip; no output was written.")

    selected_trips = trips[trips["trip_id"].isin(selected_trip_ids)].copy()
    missing_selected_trips = selected_trip_ids - set(selected_trips["trip_id"])
    if missing_selected_trips:
        raise GTFSError(
            f"stop_times.txt references missing selected trips: {sorted(missing_selected_trips)[:10]}"
        )
    selected_stop_times = stop_times[stop_times["trip_id"].isin(selected_trip_ids)].copy()
    used_stop_ids = _nonempty(selected_stop_times["stop_id"])
    LOGGER.info(
        "Retained %s of %s stop-time records; %s stops used by complete trips",
        _count(len(selected_stop_times)), _count(len(stop_times)), _count(len(used_stop_ids)),
    )

    stops_by_id = stops.drop_duplicates("stop_id", keep="first").set_index("stop_id", drop=False)
    retained_stop_ids = set(used_stop_ids)
    parent_added: set[str] = set()
    missing_parents: set[str] = set()
    circular_parents: set[str] = set()
    for start_id in list(used_stop_ids):
        chain: set[str] = set()
        current = start_id
        while current in stops_by_id.index:
            if current in chain:
                circular_parents.add(current)
                break
            chain.add(current)
            parent = (
                str(stops_by_id.at[current, "parent_station"])
                if "parent_station" in stops.columns else ""
            )
            if not parent:
                break
            if parent not in stops_by_id.index:
                missing_parents.add(parent)
                break
            if parent not in retained_stop_ids:
                retained_stop_ids.add(parent)
                parent_added.add(parent)
            current = parent
    LOGGER.info("Station hierarchy expansion added %s parent stops", _count(len(parent_added)))
    if missing_parents or circular_parents:
        details = []
        if missing_parents:
            details.append(f"missing parent stations {sorted(missing_parents)[:10]}")
        if circular_parents:
            details.append(f"circular parent relationships at {sorted(circular_parents)[:10]}")
        raise GTFSError("Invalid stop hierarchy: " + "; ".join(details))

    pathway_added: set[str] = set()
    selected_pathways: pd.DataFrame | None = None
    if "pathways.txt" in tables:
        pathways = tables["pathways.txt"]
        missing = {"from_stop_id", "to_stop_id"} - set(pathways.columns)
        if missing:
            raise GTFSError(f"pathways.txt: missing column(s) {', '.join(sorted(missing))}")
        keep = pd.Series(False, index=pathways.index)
        changed = True
        while changed:
            new_keep = (
                pathways["from_stop_id"].isin(retained_stop_ids)
                | pathways["to_stop_id"].isin(retained_stop_ids)
            )
            new_keep &= (
                pathways["from_stop_id"].isin(stops_by_id.index)
                & pathways["to_stop_id"].isin(stops_by_id.index)
            )
            new_ids = _nonempty(pathways.loc[new_keep, "from_stop_id"]) | _nonempty(
                pathways.loc[new_keep, "to_stop_id"]
            )
            additions = new_ids - retained_stop_ids
            keep |= new_keep
            retained_stop_ids |= additions
            pathway_added |= additions
            changed = bool(additions)
        selected_pathways = pathways[keep].copy()
        LOGGER.info(
            "Retained %s pathway records and added %s pathway stops",
            _count(len(selected_pathways)), _count(len(pathway_added)),
        )

    selected_stops = stops[stops["stop_id"].isin(retained_stop_ids)].copy()
    route_ids = _nonempty(selected_trips["route_id"])
    service_ids = _nonempty(selected_trips["service_id"])
    shape_ids = _nonempty(selected_trips["shape_id"]) if "shape_id" in selected_trips else set()
    selected_routes = routes[routes["route_id"].isin(route_ids)].copy()
    LOGGER.info(
        "Dependency closure references %s routes, %s services and %s shapes",
        _count(len(route_ids)), _count(len(service_ids)), _count(len(shape_ids)),
    )

    output = {
        "stops.txt": selected_stops,
        "stop_times.txt": selected_stop_times,
        "trips.txt": selected_trips,
        "routes.txt": selected_routes,
    }
    retained = {
        "stop_id": retained_stop_ids,
        "trip_id": selected_trip_ids,
        "route_id": route_ids,
        "service_id": service_ids,
        "shape_id": shape_ids,
    }
    optional_actions: dict[str, str] = {}
    special = {"agency.txt", "transfers.txt", "pathways.txt", "levels.txt", "attributions.txt"}
    for name, frame in tables.items():
        if name in output or name in special:
            continue
        filtered, action = _filter_optional(name, frame, retained)
        optional_actions[name] = action
        if filtered is not None:
            output[name] = filtered

    agency_ids: set[str] = set()
    if "agency_id" in selected_routes:
        agency_ids = _nonempty(selected_routes["agency_id"])
    if "agency.txt" in tables:
        agency = tables["agency.txt"]
        if agency_ids:
            if "agency_id" not in agency:
                raise GTFSError("agency.txt lacks agency_id but routes.txt uses agency_id.")
            output["agency.txt"] = agency[agency["agency_id"].isin(agency_ids)].copy()
            optional_actions["agency.txt"] = "filtered"
        else:
            output["agency.txt"] = agency.copy()
            optional_actions["agency.txt"] = "copied: routes do not identify agencies"
        LOGGER.info(
            "Agency rows retained: %s of %s",
            _count(len(output["agency.txt"])), _count(len(agency)),
        )

    transfer_dropped = 0
    if "transfers.txt" in tables:
        transfer = tables["transfers.txt"]
        mask = pd.Series(True, index=transfer.index)
        references = {
            "from_stop_id": retained_stop_ids, "to_stop_id": retained_stop_ids,
            "from_route_id": route_ids, "to_route_id": route_ids,
            "from_trip_id": selected_trip_ids, "to_trip_id": selected_trip_ids,
        }
        for column, values in references.items():
            if column in transfer:
                mask &= transfer[column].eq("") | transfer[column].isin(values)
        output["transfers.txt"] = transfer[mask].copy()
        transfer_dropped = int((~mask).sum())
        optional_actions["transfers.txt"] = "filtered"
        LOGGER.info(
            "Transfers retained: %s of %s; dropped: %s",
            _count(len(output["transfers.txt"])), _count(len(transfer)),
            _count(transfer_dropped),
        )

    if selected_pathways is not None:
        output["pathways.txt"] = selected_pathways
        optional_actions["pathways.txt"] = "filtered and endpoint-expanded"

    level_ids: set[str] = set()
    for frame in (selected_stops, selected_pathways):
        if frame is not None and "level_id" in frame:
            level_ids |= _nonempty(frame["level_id"])
    if "levels.txt" in tables:
        levels = tables["levels.txt"]
        if "level_id" not in levels:
            raise GTFSError("levels.txt: missing column level_id")
        output["levels.txt"] = levels[levels["level_id"].isin(level_ids)].copy()
        optional_actions["levels.txt"] = "filtered"

    if "attributions.txt" in tables:
        attrs = tables["attributions.txt"]
        mask = pd.Series(True, index=attrs.index)
        for column, values in (
            ("agency_id", agency_ids), ("route_id", route_ids), ("trip_id", selected_trip_ids)
        ):
            if column in attrs:
                mask &= attrs[column].eq("") | attrs[column].isin(values)
        output["attributions.txt"] = attrs[mask].copy()
        optional_actions["attributions.txt"] = "filtered"

    LOGGER.info(
        "Subset complete: %s trips, %s routes, %s stops and %s stop times",
        _count(len(selected_trips)), _count(len(selected_routes)),
        _count(len(selected_stops)), _count(len(selected_stop_times)),
    )
    return output, {
        "stops_intersecting_boundary": len(qualifying_stop_ids),
        "complete_trips_retained": len(selected_trip_ids),
        "routes_retained": len(route_ids),
        "stops_outside_boundary_retained_for_complete_trips": len(
            used_stop_ids - qualifying_stop_ids
        ),
        "parent_stops_added": len(parent_added),
        "pathway_stops_added": len(pathway_added),
        "stops_with_malformed_or_missing_coordinates": malformed_coordinates,
        "transfer_records_dropped": transfer_dropped,
        "optional_files": optional_actions,
        "warnings": [],
    }


def _missing(values: Iterable[str], available: Iterable[str]) -> set[str]:
    available_values = set(available)
    return {value for value in values if value and value not in available_values}


def validate_gtfs(tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Validate key references in a filtered feed and raise one combined error."""
    LOGGER.info("Starting internal GTFS reference validation")
    errors: list[str] = []
    checks_completed = 0

    def check(source: str, column: str, target: str, target_column: str) -> None:
        nonlocal checks_completed
        if source not in tables or column not in tables[source]:
            return
        if target not in tables or target_column not in tables[target]:
            if _nonempty(tables[source][column]):
                errors.append(f"{source}.{column} references absent/invalid {target}.{target_column}")
            return
        LOGGER.info("Validating %s.%s -> %s.%s", source, column, target, target_column)
        missing = _missing(tables[source][column], tables[target][target_column])
        checks_completed += 1
        if missing:
            errors.append(
                f"{source}.{column} -> {target}.{target_column}: missing {sorted(missing)[:10]}"
            )

    check("stop_times.txt", "trip_id", "trips.txt", "trip_id")
    check("stop_times.txt", "stop_id", "stops.txt", "stop_id")
    check("trips.txt", "route_id", "routes.txt", "route_id")
    check("frequencies.txt", "trip_id", "trips.txt", "trip_id")
    check("routes.txt", "agency_id", "agency.txt", "agency_id")
    check("stops.txt", "parent_station", "stops.txt", "stop_id")
    check("pathways.txt", "from_stop_id", "stops.txt", "stop_id")
    check("pathways.txt", "to_stop_id", "stops.txt", "stop_id")
    check("stops.txt", "level_id", "levels.txt", "level_id")
    check("pathways.txt", "from_level_id", "levels.txt", "level_id")
    check("pathways.txt", "to_level_id", "levels.txt", "level_id")

    LOGGER.info("Validating retained service calendars")
    service_ids: set[str] = set()
    for name in ("calendar.txt", "calendar_dates.txt"):
        if name in tables and "service_id" in tables[name]:
            service_ids |= _nonempty(tables[name]["service_id"])
    missing_services = _missing(tables["trips.txt"]["service_id"], service_ids)
    checks_completed += 1
    if missing_services:
        errors.append(
            f"trips.txt.service_id absent from retained calendars: {sorted(missing_services)[:10]}"
        )

    if "shape_id" in tables["trips.txt"]:
        referenced = _nonempty(tables["trips.txt"]["shape_id"])
        if referenced:
            LOGGER.info("Validating %s referenced shapes", _count(len(referenced)))
            checks_completed += 1
            if "shapes.txt" not in tables or "shape_id" not in tables["shapes.txt"]:
                errors.append("trips.txt references shapes but retained shapes.txt is absent or invalid")
            else:
                missing_shapes = referenced - _nonempty(tables["shapes.txt"]["shape_id"])
                if missing_shapes:
                    errors.append(
                        f"trips.txt.shape_id missing from shapes.txt: {sorted(missing_shapes)[:10]}"
                    )

    if "transfers.txt" in tables:
        refs = {
            "from_stop_id": ("stops.txt", "stop_id"),
            "to_stop_id": ("stops.txt", "stop_id"),
            "from_route_id": ("routes.txt", "route_id"),
            "to_route_id": ("routes.txt", "route_id"),
            "from_trip_id": ("trips.txt", "trip_id"),
            "to_trip_id": ("trips.txt", "trip_id"),
        }
        for column, (target, target_column) in refs.items():
            check("transfers.txt", column, target, target_column)

    if errors:
        raise GTFSError("GTFS integrity validation failed:\n- " + "\n- ".join(errors))
    LOGGER.info("Internal validation passed: %d checks completed", checks_completed)
    return {"passed": True, "checks": checks_completed, "errors": []}


def write_gtfs(tables: dict[str, pd.DataFrame], output: Path) -> None:
    """Atomically write GTFS text files at the root of a new ZIP archive."""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Writing output GTFS ZIP: %s", output)
    fd, temporary_name = tempfile.mkstemp(
        suffix=".zip", prefix=f".{output.stem}-", dir=output.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for number, (name, frame) in enumerate(tables.items(), start=1):
                started = time.perf_counter()
                LOGGER.info(
                    "Writing table %d/%d: %s with %s rows",
                    number, len(tables), name, _count(len(frame)),
                )
                with archive.open(name, mode="w") as binary_handle:
                    with io.TextIOWrapper(
                        binary_handle, encoding="utf-8", newline=""
                    ) as text_handle:
                        frame.to_csv(
                            text_handle,
                            index=False,
                            lineterminator="\n",
                            chunksize=100_000,
                        )
                LOGGER.info("Wrote %s in %.2f seconds", name, time.perf_counter() - started)
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    LOGGER.info("Output GTFS written successfully: %s", output)


def _source_signature(stat: os.stat_result) -> tuple[int, int, int, int]:
    """Identify source changes without treating a read's access time as a change."""
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def run(
    input_path: Path,
    output_path: Path,
    boundary_path: Path | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    bbox_crs: str | None = None,
    buffer_m: float = 250.0,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Run the complete GTFS subsetting pipeline and return its audit report."""
    started = time.perf_counter()
    input_resolved = input_path.resolve()
    output_resolved = output_path.resolve()
    if input_resolved == output_resolved:
        raise GTFSError("Output cannot overwrite the source GTFS.")
    source_stat = input_resolved.stat() if input_resolved.is_file() else None

    LOGGER.info("Stage 1/5: Reading source GTFS")
    tables = read_gtfs(input_path)
    LOGGER.info("Stage 2/5: Preparing spatial boundary")
    selection, boundary_report = prepare_boundary(boundary_path, bbox, bbox_crs, buffer_m)
    LOGGER.info("Stage 3/5: Selecting complete trips and dependencies")
    filtered, subset_report = subset_gtfs(tables, selection)

    source_trip_count = len(tables["trips.txt"])
    output_trip_count = len(filtered["trips.txt"])
    source_route_count = len(tables["routes.txt"])
    output_route_count = len(filtered["routes.txt"])
    LOGGER.info(
        "Trip result: %s retained of %s (%.1f%%); %s removed",
        _count(output_trip_count),
        _count(source_trip_count),
        100.0 * output_trip_count / source_trip_count if source_trip_count else 0.0,
        _count(source_trip_count - output_trip_count),
    )
    LOGGER.info(
        "Route result: %s retained of %s (%.1f%%); %s removed",
        _count(output_route_count),
        _count(source_route_count),
        100.0 * output_route_count / source_route_count if source_route_count else 0.0,
        _count(source_route_count - output_route_count),
    )

    LOGGER.info("Stage 4/5: Validating internal references")
    validation_report = validate_gtfs(filtered)
    LOGGER.info("Stage 5/5: Writing output GTFS")
    write_gtfs(filtered, output_path)

    if source_stat and (
        _source_signature(input_resolved.stat()) != _source_signature(source_stat)
    ):
        output_resolved.unlink(missing_ok=True)
        raise GTFSError("Source GTFS changed during processing; output was removed.")

    report = {
        "input_path": str(input_resolved),
        "output_path": str(output_resolved),
        "boundary": boundary_report,
        "source_row_counts": {name: len(frame) for name, frame in tables.items()},
        "output_row_counts": {name: len(frame) for name, frame in filtered.items()},
        **subset_report,
        "integrity_checks": validation_report,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    report["warnings"] = boundary_report["warnings"] + report["warnings"]
    if report_path:
        report_path = report_path.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = report_path.with_name(f".{report_path.name}.tmp")
        LOGGER.info("Writing JSON processing report: %s", report_path)
        temporary_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary_report.replace(report_path)

    LOGGER.info("Completed successfully in %.2f seconds", report["elapsed_seconds"])
    LOGGER.info(
        "Final result: %s trips, %s routes, %s stops and %s stop times",
        _count(report["output_row_counts"]["trips.txt"]),
        _count(report["output_row_counts"]["routes.txt"]),
        _count(report["output_row_counts"]["stops.txt"]),
        _count(report["output_row_counts"]["stop_times.txt"]),
    )
    return report


def configure_logging(verbose: bool, log_dir: Path = LOG_DIR) -> Path:
    """Log to the console and to a new timestamped file in log_dir, creating it if needed."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"subset_gtfs_{datetime.now():%Y%m%d_%H%M%S}.log"
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)
    console_handler = logging.StreamHandler()
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    for handler in (console_handler, file_handler):
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    return log_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Subset GTFS by stops while retaining complete trips."
    )
    parser.add_argument("--input", type=Path, required=True, help="Input GTFS ZIP or directory")
    parser.add_argument("--output", type=Path, required=True, help="New output GTFS ZIP")
    area = parser.add_mutually_exclusive_group(required=True)
    area.add_argument("--boundary", type=Path, help="Polygon dataset readable by GeoPandas")
    area.add_argument(
        "--bbox", nargs=4, type=float, metavar=("MIN_X", "MIN_Y", "MAX_X", "MAX_Y")
    )
    parser.add_argument("--bbox-crs", help="Required CRS for --bbox, for example EPSG:4326")
    parser.add_argument(
        "--buffer-m", type=float, default=250.0, help="Buffer in metres; use 0 for none"
    )
    parser.add_argument("--no-buffer", action="store_true", help="Equivalent to --buffer-m 0")
    parser.add_argument("--report", type=Path, help="Optional JSON audit report")
    parser.add_argument("--verbose", action="store_true", help="Show third-party debug logging")
    return parser


def main() -> int:
    """Parse CLI arguments and execute the subsetting pipeline."""
    args = build_parser().parse_args()
    log_path = configure_logging(args.verbose)
    LOGGER.info("Logging to file: %s", log_path)
    try:
        run(
            input_path=args.input,
            output_path=args.output,
            boundary_path=args.boundary,
            bbox=tuple(args.bbox) if args.bbox else None,
            bbox_crs=args.bbox_crs,
            buffer_m=0.0 if args.no_buffer else args.buffer_m,
            report_path=args.report,
        )
    except (GTFSError, OSError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
