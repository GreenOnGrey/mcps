"""An error that an agent reads as the result of a tool."""


class DocError(Exception):
    """An error an agent understands: English text, with a field or a limit."""
