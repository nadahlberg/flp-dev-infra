from collections.abc import Callable
from typing import Any

ProjectArgs = tuple[dict[str, str | bool | None], str | None]


def project_args(name: str, get_project: Callable[..., Any]) -> ProjectArgs:
    """Build Project inputs, adopting an existing project of that name if any.

    Returns the inputs and the id to import, or None when the project does
    not exist yet and should simply be created.
    """
    try:
        existing = get_project(name=name)
    except Exception:  # noqa: BLE001 - the provider raises a plain Exception when not found
        return {"name": name}, None
    return {
        "name": name,
        "description": existing.description or None,
        "purpose": existing.purpose or None,
        "environment": existing.environment or None,
        "is_default": existing.is_default,
    }, existing.id
