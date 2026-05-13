"""Top-level orchestrator: ``parse_spec(path | content) -> Result[ParsedSpec]``.

This is the public entry point of the parser. Its job is wiring:

1. Validate inputs (exactly one of ``path`` / ``content``).
2. Run the lexer (handles ``NOT_FOUND``, ``IO_ERROR``, ``ENCODING_ERROR``,
   binary detection).
3. Run the section detector.
4. Dispatch each known section to the right sub-parser.
5. Infer the spec level from the (effective) path.
6. Assemble a :class:`ParsedSpec` and wrap it in ``Ok`` along with warnings.

No SpecDD validation rules are applied here — that's PR 5's
``validate_spec``. Anomalies surface as ``warnings`` in the result envelope,
not as errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeAlias

from specdd_mcp.parser.bullets import parse_bullets
from specdd_mcp.parser.levels import infer_level
from specdd_mcp.parser.lexer import lex_path, lex_text
from specdd_mcp.parser.scenarios import parse_scenarios
from specdd_mcp.parser.sections import DetectedSection, detect_sections
from specdd_mcp.parser.structure import parse_structure
from specdd_mcp.parser.tasks import parse_tasks
from specdd_mcp.parser.text import parse_text
from specdd_mcp.types import (
    Err,
    KnownSection,
    Ok,
    ParsedSpec,
    SectionPosition,
    UnknownSection,
)

ParseResult: TypeAlias = "Ok[ParsedSpec] | Err"

# (canonical section key, ParsedSpec attribute name) pairs for all sections
# whose body is a flat list of bullets. These all dispatch identically through
# parse_bullets and produce a ``list[str]`` on the spec.
_BULLET_FIELDS: list[tuple[KnownSection, str]] = [
    ("owns", "owns"),
    ("can_modify", "can_modify"),
    ("can_read", "can_read"),
    ("references", "references"),
    ("must", "must"),
    ("must_not", "must_not"),
    ("depends_on", "depends_on"),
    ("forbids", "forbids"),
    ("exposes", "exposes"),
    ("accepts", "accepts"),
    ("returns", "returns"),
    ("raises", "raises"),
    ("handles", "handles"),
    ("done_when", "done_when"),
]


def parse_spec(
    *,
    path: str | None = None,
    content: str | None = None,
    virtual_path: str | None = None,
) -> ParseResult:
    """Parse a SpecDD ``.sdd`` file (by ``path``) or raw content into a
    :class:`ParsedSpec`.

    Exactly one of ``path`` or ``content`` must be supplied. ``virtual_path`` is
    used only when ``content`` is supplied — it gives the spec a name for level
    inference and for downstream error messages.

    Returns ``Ok(data=ParsedSpec, warnings=...)`` on success. Warnings include
    benign anomalies like a missing ``Spec:`` header or duplicate section
    headers. Returns ``Err`` on filesystem failure, encoding failure, or binary
    content.
    """
    # 1. Input validation
    if path is None and content is None:
        return Err(
            error="INVALID_INPUT",
            message="exactly one of `path` or `content` must be provided",
        )
    if path is not None and content is not None:
        return Err(
            error="INVALID_INPUT",
            message="provide `path` or `content`, not both",
        )

    # 2. Lex
    if path is not None:
        lex_result = lex_path(Path(path))
    else:
        assert content is not None  # for mypy — validated above
        lex_result = lex_text(content)
    if isinstance(lex_result, Err):
        return lex_result
    lexed = lex_result.data

    # 3. Section detection
    detected = detect_sections(lexed)

    # 4. Effective path + level inference
    effective_path = _effective_path(path, virtual_path)
    level = infer_level(effective_path)

    # 5. Assemble the spec.
    warnings: list[str] = list(lex_result.warnings)
    positions: dict[KnownSection, SectionPosition] = {}
    fields: dict[str, Any] = {
        "path": effective_path,
        "name": "",
        "level": level,
        "raw": lexed.raw,
        "line_count": len(lexed.lines),
    }

    # --- Spec name (from `Spec:` section) ---
    spec_section = _first_or_warn(detected.known.get("spec", []), "Spec:", warnings)
    if spec_section is not None:
        fields["name"] = parse_text(spec_section)
        positions["spec"] = _to_position(spec_section)
    else:
        warnings.append("spec has no `Spec:` header")

    # --- Single-value text sections (Platform, Purpose) ---
    for key in ("platform", "purpose"):
        section = _first_or_warn(detected.known.get(key, []), key, warnings)
        if section is not None:
            fields[key] = parse_text(section)
            positions[key] = _to_position(section)

    # --- Structure ---
    structure_section = _first_or_warn(
        detected.known.get("structure", []), "Structure:", warnings
    )
    if structure_section is not None:
        fields["structure"] = parse_structure(structure_section.body_lines)
        positions["structure"] = _to_position(structure_section)

    # --- Bullet-list fields ---
    for key, attr in _BULLET_FIELDS:
        section = _first_or_warn(detected.known.get(key, []), key, warnings)
        if section is not None:
            fields[attr] = parse_bullets(section.body_lines)
            positions[key] = _to_position(section)

    # --- Tasks ---
    tasks_section = _first_or_warn(detected.known.get("tasks", []), "Tasks:", warnings)
    if tasks_section is not None:
        fields["tasks"] = parse_tasks(tasks_section.body_lines)
        positions["tasks"] = _to_position(tasks_section)

    # --- Scenarios (multi-occurrence; aggregate) ---
    scenarios_sections = detected.known.get("scenarios", [])
    if scenarios_sections:
        fields["scenarios"] = parse_scenarios(scenarios_sections)
        positions["scenarios"] = SectionPosition(
            start_line=scenarios_sections[0].start_line,
            end_line=scenarios_sections[-1].end_line,
        )

    # --- Examples (multi-occurrence; aggregate by flattening bullets) ---
    examples_sections = detected.known.get("examples", [])
    if examples_sections:
        examples: list[str] = []
        for section in examples_sections:
            examples.extend(parse_bullets(section.body_lines))
        fields["examples"] = examples
        positions["examples"] = SectionPosition(
            start_line=examples_sections[0].start_line,
            end_line=examples_sections[-1].end_line,
        )

    # --- Positions and unknown sections ---
    fields["positions"] = positions
    if detected.unknown:
        fields["unknown_sections"] = [
            UnknownSection(
                name=ds.name,
                lines=[line.text for line in ds.body_lines],
                start_line=ds.start_line,
                end_line=ds.end_line,
            )
            for ds in detected.unknown
        ]

    return Ok(data=ParsedSpec(**fields), warnings=warnings)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _effective_path(path: str | None, virtual_path: str | None) -> str:
    """Pick the path string that lands on :attr:`ParsedSpec.path`."""
    if path is not None:
        return path
    if virtual_path is not None:
        return virtual_path
    return "<inline>"


def _first_or_warn(
    sections: list[DetectedSection],
    label: str,
    warnings: list[str],
) -> DetectedSection | None:
    """Return the first detection, warning if there is more than one."""
    if not sections:
        return None
    if len(sections) > 1:
        warnings.append(
            f"multiple `{label}` headers found ({len(sections)}); using the first"
        )
    return sections[0]


def _to_position(section: DetectedSection) -> SectionPosition:
    return SectionPosition(start_line=section.start_line, end_line=section.end_line)
