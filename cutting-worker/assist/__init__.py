"""Vendored snapshot of `assist` (github.com/vives-devbit/assist@55b0fb1) —
ticket #112. See VENDORED.md in this directory for what was copied, what
was left out, and why.

This file is the one deliberate change from upstream: its `__init__.py` ran
`assist`'s Click CLI at import time, which made importing any `assist.*`
module exit or print a usage error. Only the library modules are vendored,
so there is no CLI to run.
"""
