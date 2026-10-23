<div align="center">
<a href="https://www.transportforthenorth.com/">
<img
    src="https://raw.githubusercontent.com/Transport-for-the-North/.github/refs/heads/main/profile/tfn-banner.svg"
    alt="Transport for the North logo">
</a>
</div>

<h1 align="center">gtfs-clip</h1>

<p align="center">
<a href="https://transport-for-the-north.github.io/CAF-Handbook/python_tools/framework.html">
  <img alt="CAF Status - Pre-Alpha" src="https://img.shields.io/badge/CAF%20Status-Pre--Alpha-orange">
</a>
</p>
<p align="center">
<a href="https://pypi.org/project/gtfs-clip/">
  <img alt="Supported Python versions" src="https://img.shields.io/pypi/pyversions/gtfs-clip.svg?style=flat-square">
</a>
<a href="https://pypi.org/project/gtfs-clip/">
  <img alt="Latest release" src="https://img.shields.io/github/release/hulsiejames/gtfs-clip.svg?style=flat-square&maxAge=86400">
</a>
<a href="https://anaconda.org/conda-forge/gtfs-clip">
  <img alt="Conda" src="https://img.shields.io/conda/v/conda-forge/gtfs-clip?style=flat-square&logo=condaforge">
</a>
</p>
<p align="center">
<a href="https://github.com/hulsiejames/gtfs-clip/actions?query=event%3Apush">
  <img alt="Testing Badge" src="https://img.shields.io/github/actions/workflow/status/hulsiejames/gtfs-clip/tests.yml?style=flat-square&logo=GitHub&label=Tests">
</a>
<a href="https://app.codecov.io/gh/hulsiejames/gtfs-clip">
  <img alt="Coverage" src="https://img.shields.io/codecov/c/github/hulsiejames/gtfs-clip.svg?branch=main&style=flat-square&logo=CodeCov">
</a>
<a href='https://gtfs-clip.readthedocs.io/en/stable/'>
  <img alt='Documentation Status' src="https://img.shields.io/readthedocs/gtfs-clip?style=flat-square&logo=readthedocs">
</a>
</p>

> [!WARNING]  
> This package is in an early stage of development so features may change or be removed.
> If using this package it is recommended to set a specific version and check before
> upgrading to a new version.

python package to clip a GTFS feed to routes serving a boundary file


# GTFS Spatial Subsetter

A small command-line tool that selects trips through stops intersecting a polygon or bounding box, then retains every selected trip in full. It never clips stop times or shapes at the boundary.

## Install

Use Python 3.11 or later:

```cmd
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Use

Polygon input:

```cmd
python subset_gtfs.py ^
  --input "E:\GTFS\north_west.zip" ^
  --boundary "E:\Boundaries\greater_manchester.gpkg" ^
  --output "E:\GTFS\greater_manchester.zip" ^
  --buffer-m 250 ^
  --report "E:\GTFS\greater_manchester_report.json"
```

Bounding box input uses `MIN_X MIN_Y MAX_X MAX_Y`. Its CRS is mandatory:

```cmd
python subset_gtfs.py ^
  --input "E:\GTFS\north_west.zip" ^
  --bbox -2.75 53.30 -1.90 53.70 ^
  --bbox-crs EPSG:4326 ^
  --output "E:\GTFS\greater_manchester.zip" ^
  --no-buffer
```

All boundaries are transformed to EPSG:27700 for the metre buffer and then to EPSG:4326 for stop intersection. `--no-buffer` takes precedence over `--buffer-m`.

## Behaviour and assumptions

- All source `.txt` tables are read as strings with empty strings preserved.
- Stops on the boundary qualify because selection uses `intersects`.
- One qualifying stop selects the complete trip, all its stop times, all stops it uses, and its complete shape.
- Parent-station ancestors and connected pathway endpoints are expanded without automatically retaining siblings.
- `calendar.txt` and `calendar_dates.txt` are independent; a selected service must occur in at least one.
- Transfer rows are retained only when every non-empty referenced entity remains.
- `feed_info.txt` is copied unchanged. Calendar, shape, frequency, agency, level, pathway, transfer and attribution tables are filtered.
- Fare v1/v2, area/location-group, network, booking-rule and translation tables are deliberately omitted when present. Their cross-table semantics need feed-specific handling; copying them could produce dangling references. Every decision appears in the JSON report.
- Invalid polygon geometry is repaired with Shapely `make_valid`, logged and reported. Invalid GTFS relationships are not repaired.
- The output is written to a temporary ZIP and atomically moved into place after validation.

Run an external canonical GTFS validator on the result as a final specification-compliance check.

## Quality checks

```cmd
python -m pytest
ruff format --check .
ruff check .
```



> [!TIP]
> For more detailed information including a user guide, tutorials and API reference see the full
> [gtfs-clip documentation](https://gtfs-clip.readthedocs.io/en/stable/)


## Table of Contents

- [Table of Contents](#table-of-contents)
- [Overview](#overview)
  - [What does it do?](#what-does-it-do)
  - [Main Features](#main-features)
    - [Work-in-Progress](#work-in-progress)
  - [Who is it for?](#who-is-it-for)
- [Where to get it](#where-to-get-it)
  - [Installation from GitHub](#installation-from-github)
- [Usage](#usage)
  - [Command Line](#command-line)
- [Documentation](#documentation)
- [Contribution](#contribution)
- [Contact Us](#contact-us)

## Overview

### What does it do?

> [!IMPORTANT]
> This section of the README hasn't been written yet, but it will contain a brief
> description of what the tool is intended to do.

### Main Features

> [!IMPORTANT]
> This section of the README hasn't been written yet.

- **Feature 1** - description

#### Work-in-Progress

- **Work in progress feature** - description of feature not yet release.

> [!WARNING]
> These features are work-in-progress and are not available in a released version of gtfs-clip, to
> access these features a specific branch of gtfs-clip should be installed, see [Installation from GitHub](#installation-from-github).

### Who is it for?

- **Target audience:** *TODO*

## Where to get it

> [!IMPORTANT]
> gtfs-clip has not been published yet so cannot be installed from
> conda-forge or PyPI, see [Installation from GitHub](#installation-from-github).

The latest released version are available at the [Python
Package Index (PyPI)](https://pypi.org/project/gtfs-clip) and on [Conda](https://anaconda.org/conda-forge/gtfs-clip).

```sh
conda install -c conda-forge gtfs-clip
```

```sh
pip install gtfs-clip
```

> [!TIP]
>
> - See the [Quick Start Guide](https://gtfs-clip.readthedocs.io/en/stable/start.html#quick-start) for more detailed instructions.
> - See the [requirements.txt](requirements.txt) for the full list of package dependencies.

### Installation from GitHub

> [!WARNING]
> Unreleased GitHub versions should **not** be considered stable.

The latest, unreleased, version can be installed directly from GitHub using:

```sh
pip install "git+https://github.com/hulsiejames/gtfs-clip"
```

> [!TIP]
> `pip install` can install a specific tag, or branch, using `@{tag-name}`
> after the git URL.

## Usage

gtfs-clip provides and Command-line (CLI) and graphical interface (GUI) to use many of it's
features without the need to write any Python code, see the [Tool Usage section](https://gtfs-clip.readthedocs.io/en/stable/usage/index.html)
of the user guide for more details.

### Command Line

The tool can be run from command line, with the command:

```sh
gtfs-clip
```

See [Command-Line Interface (User Guide)](https://gtfs-clip.readthedocs.io/en/stable/usage/cli.html)
for full explanations of the parameters.

## Documentation

The code documentation is hosted at <https://gtfs-clip.readthedocs.io/en/stable/>.


## Contribution

We encourage use of, and contributions to, the repositories within this organisation, licenses are provided within
the repositories and the [organisation contribution guide](https://github.com/Transport-for-the-North/.github/blob/main/CONTRIBUTING.rst)
provides details for contributions.

---

## Contact Us

For further information about using this tool or CAF tools in your projects and work contact Transport for the North - <TfNOffer@transportforthenorth.com>

---


[Go to Top](#table-of-contents)
