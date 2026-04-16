"""Abstract base class for EnvironmentBackend.

Kept in a separate module to avoid circular imports between environment.py
and the various *_backend.py modules.
"""

from abc import ABC, abstractmethod


class EnvironmentBackend(ABC):
    """Common interface implemented by all environment backends."""

    @abstractmethod
    def get_tool_schemas(self):
        """Return the tool schema list in OpenAI function-calling format."""

    @abstractmethod
    def get_tool_names(self):
        """Return the names of all registered tools."""

    @abstractmethod
    def get_write_tool_names(self):
        """Return the names of all mutating tools."""

    @abstractmethod
    def get_tool_summary(self):
        """Return a summary of tools exposed by the current backend."""

    @abstractmethod
    def execute_tool(self, name, args):
        """Execute a tool for real and return the result string."""

    @abstractmethod
    def reset(self):
        """Reset the environment between episodes."""

    @abstractmethod
    def check_outcome(self, outcome_spec):
        """Validate the final state against the outcome_check config."""
