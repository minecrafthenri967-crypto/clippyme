"""Tests for the ``clipper_pro`` package.

Spelled without the underscore on purpose. Every test directory in this repo is
a package and pytest puts ``tests/`` on ``sys.path`` to import them — so a
directory named ``clipper_pro`` here would shadow the real top-level
``clipper_pro`` package, and every import in these modules would resolve to the
test directory instead.
"""
