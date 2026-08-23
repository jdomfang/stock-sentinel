"""Compatibility shim for `pkg_resources`. DO NOT DELETE ON A DEAD-CODE SWEEP.

Nothing in this repo imports it, so every unused-file scan flags it. It is
imported by DEPENDENCIES, not by us -- it was added when polygon-api-client
imported `pkg_resources` at module scope, which made a missing module a hard
ImportError at portal startup rather than a degraded feature.

TWO THINGS TO KNOW BEFORE TOUCHING IT, both verified 2026-08-23:

1. polygon-api-client 1.13.7 no longer needs it -- it uses importlib.metadata.
   So on the CURRENT pins this file is not load-bearing. That is a fact about
   today's lockfile, not a property of the app.

2. IT SHADOWS THE REAL MODULE. The repo root precedes site-packages on
   sys.path, so `import pkg_resources` anywhere in this process resolves HERE,
   not to setuptools'. Anything needing the real API -- require(),
   resource_filename(), working_set -- gets an AttributeError instead. The
   surface below is deliberately tiny; widen it only for a dependency that
   actually asks.

Also note setuptools >= 81 removed pkg_resources entirely, and
requirements.txt pins it unbounded, so the real module may simply be absent.
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
