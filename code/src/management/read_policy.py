"""Versioned, opt-in Writer paging policy; see docs/writer-long-context-reading.md.

Keep the resolved page size in the Writer fingerprint. Changing tool semantics
requires a new protocol version, not rewriting an existing checkpoint hash.
"""

DEFAULT_WRITER_READ_PAGE_CHARS = 12_000
WRITER_READ_POLICY_VERSION = "paged-read-grep-v1"


def resolve_writer_read_cap(enabled: bool | None, max_chars: int | None) -> int | None:
    """Explicit off wins; an omitted switch preserves legacy explicit caps."""
    if enabled is not None and type(enabled) is not bool:
        raise ValueError("writer_long_context_reading must be a boolean")
    if max_chars is not None and (type(max_chars) is not int or max_chars < 1024):
        raise ValueError("writer_tool_output_max_chars must be an integer >= 1024")
    if enabled is False:
        return None
    if enabled is True and max_chars is None:
        return DEFAULT_WRITER_READ_PAGE_CHARS
    return max_chars
