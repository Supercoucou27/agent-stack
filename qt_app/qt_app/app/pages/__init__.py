"""Page widgets displayed in the central stack of the shell."""

from .base import PageBase
from .library import LibraryPage
from .placeholders import PlaceholderPage, RunPlaceholderPage

__all__ = [
    "PageBase",
    "LibraryPage",
    "PlaceholderPage",
    "RunPlaceholderPage",
]
