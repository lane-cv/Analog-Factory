"""Effort vocabulary and conservative native Claude model capabilities.

None means inherit the CLI/provider default, never a fabricated effort value.
Codex capabilities come from its live model catalog instead of this table.
"""

import re


EFFORT_LEVELS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')
CLAUDE_LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')
_CLAUDE_STANDARD = re.compile(r'claude-(?:opus|sonnet)-4-6(?:-\d{8})?(?:\[1m\])?\Z')
_CLAUDE_EXTENDED = re.compile(
    r'claude-(?:opus-(?:4-7|4-8|5|5-5)|sonnet-(?:5|5-5)|fable-(?:5|5-1))'
    r'(?:-\d{8})?(?:\[1m\])?\Z')


def claude_effort_levels(model, environment=None):
    # Opus aliases can resolve to 4.6, whose effort range lacks xhigh. Sonnet
    # aliases can resolve to 4.5, which has no effort support. Unknown IDs stay
    # Default-only; the CLI remains responsible for actual account availability.
    alias = (model or '').removesuffix('[1m]')
    if alias in ('opus', 'sonnet', 'haiku'):
        override = (environment or {}).get(f'ANTHROPIC_DEFAULT_{alias.upper()}_MODEL')
        if override:
            model = override
    if model in ('opus', 'opus[1m]') or _CLAUDE_STANDARD.fullmatch(model or ''):
        return ['low', 'medium', 'high', 'max']
    if _CLAUDE_EXTENDED.fullmatch(model or ''):
        return list(CLAUDE_LEVELS)
    return []


def validate_effort(backend, model, effort, *, environment=None):
    """Reject explicit unsupported syntax before execution; None adds no flag."""
    if effort is None:
        return
    if not isinstance(effort, str) or effort not in EFFORT_LEVELS:
        raise ValueError('Effort must be null (Default) or a supported effort level')
    if backend not in ('codex', 'claude'):
        raise ValueError('Effort capabilities are not verified for this API provider; choose Default')
    if backend == 'claude' and effort not in claude_effort_levels(model, environment):
        raise ValueError('Effort is not supported for this Claude model; choose Default or a supported model')
