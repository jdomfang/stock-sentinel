"""Compatibility shim for environments where setuptools' pkg_resources isn't available.

Some third-party packages (e.g., polygon-api-client) still import `pkg_resources`.
Streamlit Community Cloud environments occasionally lack the `pkg_resources` module
even when `setuptools` is installed.

This shim provides the minimal API those packages typically need.
"""

from __future__ import annotations

from dataclasses import dataclass


class DistributionNotFound(Exception):
    pass


@dataclass(frozen=True)
class _Dist:
    project_name: str
    version: str


def get_distribution(project_name: str) -> _Dist:
    """Return a distribution-like object with a .version attribute."""
    try:
        from importlib.metadata import version as _version  # py3.8+

        return _Dist(project_name=project_name, version=_version(project_name))
    except Exception as e:
        raise DistributionNotFound(project_name) from e
