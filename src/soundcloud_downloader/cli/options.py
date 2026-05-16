import typer


def parse_optional_bool(value: str) -> bool | None:
    normalized = value.lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    if normalized == "unknown":
        return None
    raise typer.BadParameter("Expected one of: true, false, unknown.")
