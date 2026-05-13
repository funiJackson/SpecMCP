"""SpecDD parser — turn .sdd files into ParsedSpec and resolve their chains.

Public entry points:

- :func:`parse_spec` — parse one .sdd file or string into a :class:`ParsedSpec`.
- :func:`resolve_spec_chain` — walk a repo from root to target, parsing every
  .sdd file along the way into an ordered :class:`SpecChain`.

Sub-modules are internal implementation details that may evolve between minor
versions.
"""

from specdd_mcp.parser.parse_spec import ParseResult, parse_spec
from specdd_mcp.parser.resolve_chain import ResolveChainResult, resolve_spec_chain

__all__ = [
    "ParseResult",
    "ResolveChainResult",
    "parse_spec",
    "resolve_spec_chain",
]
