# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html


from __future__ import annotations

# Built-Ins
import importlib
import inspect
import os
import pathlib
import re
import sys

dir_path = pathlib.Path(__file__).parents[2]
source = dir_path / "src"
sys.path.insert(0, str(source.absolute()))

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "gtfs-clip"
copyright = "2026, hulsiejames"
author = "hulsiejames"

# Third Party
import gtfs_clip

version = str(gtfs_clip.__version__)
release = version


# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.duration",
    "sphinx.ext.doctest",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.autosectionlabel",
    "sphinx_gallery.gen_gallery",
    "sphinx.ext.intersphinx",
    "sphinx.ext.linkcode",
    "sphinx.ext.todo",
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates", "_templates/autosummary"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []

# -- Options for API summary -------------------------------------------------
napoleon_google_docstring = False
napoleon_numpy_docstring = True
numpydoc_show_class_members = False

# Change autodoc settings
autodoc_member_order = "groupwise"
autoclass_content = "class"
autodoc_default_options = {
    "undoc-members": True,
    "show-inheritance": True,
    "special-members": False,
    "private-members": False,
    "exclude-members": "__module__, __weakref__, __dict__",
}
autodoc_typehints = "description"

# Auto summary options
autosummary_generate = True
autosummary_imported_members = True
modindex_common_prefix = ["gtfs_clip."]

autosummary_context = {
    # Enable inherited methods / attributes in all classes
    "include_inherited_methods": False,
    "include_inherited_attributes": False,
    # Enable / disable inherited methods / attributes in some classes
    "show_inherited": [],
    "exclude_inherited": [],
}

# -- Options for Sphinx Examples gallery -------------------------------------
sphinx_gallery_conf = {
    "examples_dirs": "../../examples",  # path to your example scripts
    "gallery_dirs": "_generated/examples",  # path to where to save gallery generated output
    "backreferences_dir": "_generated/examples/backrefs",  # path to the backreferences files
    "doc_module": ("gtfs_clip",),
    # Regex pattern of filenames to be ran so the output can be included
    "filename_pattern": rf"{re.escape(os.sep)}run_.*\.py",
}

# -- Options for Linking to external docs (intersphinx) ----------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
intersphinx_timeout = 30

# -- Options for Todo extension ----------------------------------------------
def get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, default)
    if isinstance(value, bool):
        return value
    return value.lower().strip() in ("true", "t", "yes", "y", "1")


todo_include_todos = get_env_bool("SPHINX_INCLUDE_TODOS", True)
todo_emit_warnings = True

# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "pydata_sphinx_theme"
html_show_sourcelink = False
html_logo = "https://www.transportforthenorth.com/logo.svg"

master_doc = "index"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]
html_css_files = ["css/logo.css"]

html_theme_options = {
    "use_edit_page_button": True,
    "logo": {
        "image_dark": "https://raw.githubusercontent.com/Transport-for-the-North"
        "/.github/refs/heads/main/profile/tfn-logo-white.png",
        "text": f"{project} {version}",
        "alt_text": "Home",
    },
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/hulsiejames/gtfs-clip",
            "icon": "fa-brands fa-square-github",
            "type": "fontawesome",
        }
    ],
    "header_links_before_dropdown": 3,
    "external_links": [
        {
            "name": "Changelog",
            "url": "https://github.com/hulsiejames/gtfs-clip/releases",
        },
        {
            "name": "Issues",
            "url": "https://github.com/hulsiejames/gtfs-clip/issues",
        },
        {
            "name": "TfN GitHub",
            "url": "https://github.com/Transport-for-the-North",
        },
    ],
    "primary_sidebar_end": ["indices.html", "sidebar-ethical-ads.html"],
    "announcement": """
        The documentation pages are currently work-in-progress, if you have any suggestions
        for improvements please raise an issue on the
        <a href="https://github.com/hulsiejames/gtfs-clip/issues/new/choose">gtfs-clip repository</a>.
    """,
}
html_context = {
    "github_url": "https://github.com",
    "github_user": "hulsiejames",
    "github_repo": "gtfs-clip",
    "github_version": "main",
    "doc_path": "docs/source",
}

# -- Options for Linkcode extension ------------------------------------------


def _get_object_filepath(module: str, fullname: str) -> str:
    """Get filepath (including line numbers) for object in module."""
    mod = importlib.import_module(module)
    if "." in fullname:
        objname, attrname = fullname.split(".")
        obj = getattr(mod, objname)

        try:
            # object is method of a class
            obj = getattr(obj, attrname)
        except AttributeError:
            # object is attribute of a class so use class
            obj = getattr(mod, objname)

    else:
        try:
            obj = getattr(mod, fullname)
        except AttributeError:
            return module.replace(".", "/") + ".py"

    try:
        file = inspect.getsourcefile(obj)
        lines = inspect.getsourcelines(obj)
        filepath = f"{file}#L{lines[1]}"
    except (TypeError, OSError):
        filepath = module.replace(".", "/") + ".py"

    return filepath


def linkcode_resolve(domain: str, info: dict) -> str | None:
    """Resolve URLs for linking to code on GitHub.

    See sphinx.ext.linkcode extension docs for more details
    https://www.sphinx-doc.org/en/master/usage/extensions/linkcode.html
    """
    if domain != "py":
        return None
    if not info["module"]:
        return None

    filepath = _get_object_filepath(info["module"], info["fullname"])
    # Check if path is in the directory
    try:
        filepath = str(pathlib.Path(filepath).relative_to(dir_path))
    except ValueError:
        return None

    tag = f"v{version.split('+', maxsplit=1)[0]}"
    github_url = (
        f"{html_context['github_url']}/{html_context['github_user']}"
        f"/{html_context['github_repo']}/tree/{tag}"
    )

    return f"{github_url}/{filepath}"
