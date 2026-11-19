"""A2-specific failures, kept beside the layout modules.

The shared framework errors stay in :mod:`poster_framework.errors`; these
subclasses only describe failures the layout planner can raise, so other agents
do not need to know about layout contracts.
"""

from __future__ import annotations

from .errors import AgentFrameworkError


class LayoutContractError(AgentFrameworkError, ValueError):
    """PosterSpec violates the shared layout contract."""


class MissingFontError(LayoutContractError):
    """A requested font family/weight is not installed in this environment."""


class TextOverflowError(LayoutContractError):
    """Measured copy cannot fit the template slot at or above the minimum size."""
