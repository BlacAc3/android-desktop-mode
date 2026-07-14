import re
from models import _PACKAGE_JUNK_SEGMENTS

def _friendly_from_package(package: str) -> str:
    """Derive a readable name from a bare package id when no label exists."""
    raw_parts = [p for p in package.split(".") if p]
    parts = [p for p in raw_parts if p.lower() not in _PACKAGE_JUNK_SEGMENTS]
    if not parts:
        parts = raw_parts or [package]
    name_parts = parts[-2:] if len(parts) >= 2 else parts
    name = " ".join(name_parts)
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)  # split camelCase
    name = re.sub(r"\s+", " ", name).strip()
    return name.title() if name else package


def clean_app_label(raw_label: str, package: str) -> str:
    """Strip dumpsys/system jargon from a raw label and format it cleanly."""
    label = (raw_label or "").strip()

    # Strip surrounding quotes/braces artifacts dumpsys sometimes leaves in.
    label = label.strip("\"'")
    label = re.sub(r"\{.*?\}", "", label).strip()
    label = label.strip(" {}();,")

    if not label or label.lower() in ("null", "none") or label == package:
        return _friendly_from_package(package)

    # Collapse stray whitespace and title-case single ALLCAPS/underscored
    # tokens (e.g. "SOME_APP_NAME" -> "Some App Name"), leave normal mixed
    # case labels (e.g. "WhatsApp") untouched.
    label = re.sub(r"\s+", " ", label).strip()
    if label.isupper() or "_" in label:
        label = re.sub(r"[_]+", " ", label).strip()
        label = label.title()

    return label or _friendly_from_package(package)
