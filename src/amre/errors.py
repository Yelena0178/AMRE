"""Expected errors that can be reported without a traceback."""


class AmreError(Exception):
    """Base error for user-actionable AMRE failures."""


class ConfigError(AmreError):
    """The settings could not be read or validated."""


class ResearchError(AmreError):
    """Research data is invalid or could not be read or written."""


class WorkspaceChanged(ResearchError):
    """A research operation used a snapshot that is no longer current."""
