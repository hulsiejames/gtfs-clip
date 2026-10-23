from __future__ import annotations

import hashlib
import logging
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from shapely.geometry import box

from subset_gtfs import (
    LOGGER,
    GTFSError,
    configure_logging,
    read_gtfs,
    run,
    subset_gtfs,
    validate_gtfs,
)


def write_table(folder: Path, name: str, rows: list[dict[str, str]]) -> None:
    pd.DataFrame(rows).to_csv(folder / name, index=False)


def make_feed(folder: Path, calendar: str = "calendar", shapes: bool = True) -> Path:
    folder.mkdir()
    write_table(
        folder,
        "stops.txt",
        [
            {
                "stop_id": "001",
                "stop_name": "Inside",
                "stop_lat": "53.5",
                "stop_lon": "-2.2",
                "parent_station": "",
            },
            {
                "stop_id": "002",
                "stop_name": "Outside",
                "stop_lat": "53.8",
                "stop_lon": "-2.2",
                "parent_station": "P01",
            },
            {
                "stop_id": "P01",
                "stop_name": "Parent",
                "stop_lat": "53.8",
                "stop_lon": "-2.2",
                "parent_station": "",
            },
            {
                "stop_id": "003",
                "stop_name": "Other",
                "stop_lat": "54.0",
                "stop_lon": "-2.2",
                "parent_station": "",
            },
            {
                "stop_id": "BAD",
                "stop_name": "Bad",
                "stop_lat": "",
                "stop_lon": "x",
                "parent_station": "",
            },
        ],
    )
    write_table(
        folder,
        "stop_times.txt",
        [
            {
                "trip_id": "T001",
                "arrival_time": "25:15:00",
                "departure_time": "25:15:00",
                "stop_id": "001",
                "stop_sequence": "1",
            },
            {
                "trip_id": "T001",
                "arrival_time": "26:00:00",
                "departure_time": "26:00:00",
                "stop_id": "002",
                "stop_sequence": "2",
            },
            {
                "trip_id": "T002",
                "arrival_time": "10:00:00",
                "departure_time": "10:00:00",
                "stop_id": "003",
                "stop_sequence": "1",
            },
        ],
    )
    write_table(
        folder,
        "trips.txt",
        [
            {
                "route_id": "R001",
                "service_id": "S001",
                "trip_id": "T001",
                "shape_id": "SH001" if shapes else "",
            },
            {
                "route_id": "R002",
                "service_id": "S002",
                "trip_id": "T002",
                "shape_id": "SH002" if shapes else "",
            },
        ],
    )
    write_table(
        folder,
        "routes.txt",
        [
            {"route_id": "R001", "route_short_name": "1", "route_type": "3"},
            {"route_id": "R002", "route_short_name": "2", "route_type": "3"},
        ],
    )
    if calendar == "calendar":
        write_table(
            folder,
            "calendar.txt",
            [{"service_id": "S001", "monday": "1"}, {"service_id": "S002", "monday": "1"}],
        )
    else:
        write_table(
            folder,
            "calendar_dates.txt",
            [{"service_id": "S001", "date": "20261022", "exception_type": "1"}],
        )
    if shapes:
        write_table(
            folder,
            "shapes.txt",
            [
                {
                    "shape_id": "SH001",
                    "shape_pt_lat": "53.5",
                    "shape_pt_lon": "-2.2",
                    "shape_pt_sequence": "1",
                },
                {
                    "shape_id": "SH001",
                    "shape_pt_lat": "53.8",
                    "shape_pt_lon": "-2.2",
                    "shape_pt_sequence": "2",
                },
                {
                    "shape_id": "SH002",
                    "shape_pt_lat": "54",
                    "shape_pt_lon": "-2.2",
                    "shape_pt_sequence": "1",
                },
            ],
        )
    write_table(
        folder,
        "transfers.txt",
        [
            {"from_stop_id": "001", "to_stop_id": "002", "transfer_type": "0"},
            {"from_stop_id": "001", "to_stop_id": "003", "transfer_type": "0"},
        ],
    )
    return folder


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_complete_trip_shape_parent_ids_times_and_transfer(tmp_path: Path) -> None:
    source = make_feed(tmp_path / "feed")
    tables = read_gtfs(source)
    output, report = subset_gtfs(tables, box(-2.3, 53.4, -2.1, 53.6))
    validate_gtfs(output)
    assert output["trips.txt"]["trip_id"].tolist() == ["T001"]
    assert output["stop_times.txt"]["stop_id"].tolist() == ["001", "002"]
    assert output["stop_times.txt"]["arrival_time"].tolist()[0] == "25:15:00"
    assert output["stops.txt"]["stop_id"].tolist() == ["001", "002", "P01"]
    assert output["shapes.txt"]["shape_id"].tolist() == ["SH001", "SH001"]
    assert output["transfers.txt"].shape[0] == 1
    assert report["parent_stops_added"] == 1
    assert report["stops_with_malformed_or_missing_coordinates"] == 1
    assert output["stops.txt"].iloc[0]["stop_id"] == "001"


def test_stop_on_boundary_is_selected(tmp_path: Path) -> None:
    tables = read_gtfs(make_feed(tmp_path / "feed"))
    output, _ = subset_gtfs(tables, box(-2.2, 53.5, -2.1, 53.6))
    assert output["trips.txt"]["trip_id"].tolist() == ["T001"]


def test_buffer_can_include_stop(tmp_path: Path) -> None:
    source = make_feed(tmp_path / "feed")
    output = tmp_path / "out.zip"
    report = run(
        source, output, bbox=(-2.199, 53.499, -2.198, 53.501), bbox_crs="EPSG:4326", buffer_m=200
    )
    assert report["complete_trips_retained"] == 1


@pytest.mark.parametrize("calendar", ["calendar", "calendar_dates"])
def test_each_calendar_style_and_no_shapes(tmp_path: Path, calendar: str) -> None:
    tables = read_gtfs(make_feed(tmp_path / "feed", calendar=calendar, shapes=False))
    output, _ = subset_gtfs(tables, box(-2.3, 53.4, -2.1, 53.6))
    validate_gtfs(output)
    assert "shapes.txt" not in output


def test_no_selection_has_clear_error(tmp_path: Path) -> None:
    tables = read_gtfs(make_feed(tmp_path / "feed"))
    with pytest.raises(GTFSError, match="No stops intersect"):
        subset_gtfs(tables, box(0, 0, 1, 1))


def test_missing_required_column_is_named(tmp_path: Path) -> None:
    source = make_feed(tmp_path / "feed")
    frame = pd.read_csv(source / "stops.txt", dtype=str)
    frame.drop(columns="stop_lon").to_csv(source / "stops.txt", index=False)
    with pytest.raises(GTFSError, match=r"stops.txt: missing column\(s\) stop_lon"):
        read_gtfs(source)


def test_zip_root_and_source_unchanged(tmp_path: Path) -> None:
    directory = make_feed(tmp_path / "feed")
    source_zip = tmp_path / "source.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        for path in directory.glob("*.txt"):
            archive.write(path, path.name)
    before = digest(source_zip)
    output = tmp_path / "output.zip"
    run(source_zip, output, bbox=(-2.3, 53.4, -2.1, 53.6), bbox_crs="EPSG:4326", buffer_m=0)
    assert digest(source_zip) == before
    with zipfile.ZipFile(output) as archive:
        assert all("/" not in name and "\\" not in name for name in archive.namelist())


def test_validation_detects_dangling_reference(tmp_path: Path) -> None:
    tables = read_gtfs(make_feed(tmp_path / "feed"))
    output, _ = subset_gtfs(tables, box(-2.3, 53.4, -2.1, 53.6))
    output["stops.txt"] = output["stops.txt"][output["stops.txt"]["stop_id"] != "002"]
    with pytest.raises(GTFSError, match="missing.*002"):
        validate_gtfs(output)


def test_configure_logging_creates_missing_directory_and_file(tmp_path: Path) -> None:
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)
    log_dir = tmp_path / "logs"
    try:
        log_path = configure_logging(verbose=False, log_dir=log_dir)
        LOGGER.info("probe message for log file")
        for handler in root.handlers:
            handler.flush()
        assert log_dir.is_dir()
        assert log_path.parent == log_dir
        assert "probe message for log file" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in root.handlers[:]:
            if handler not in original_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(original_level)
