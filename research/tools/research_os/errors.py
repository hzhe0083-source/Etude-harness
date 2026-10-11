"""User-facing failures. The CLI prints them to stderr and exits with code 2."""


class ResearchError(Exception):
    """A request the CLI refuses (bad input, missing record, missing root)."""


class RuleViolation(ResearchError):
    """An event would break SCHEMA.json or an AGENTS.md rule."""

    def __init__(self, violations):
        self.violations = list(violations)
        super().__init__("; ".join(self.violations))
