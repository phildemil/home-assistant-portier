"""Add the Portier loader to configuration.yaml – carefully, with a backup."""

from __future__ import annotations

from pathlib import Path
import re
import shutil

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.yaml import Secrets
from homeassistant.util.yaml.loader import load_yaml

LOADER_URL = "/local/portier/loader.js"
BACKUP_SUFFIX = ".portier-backup"

_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")


class CannotEdit(Exception):
    """The file is structured in a way we should not touch automatically."""


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def add_loader(text: str, url: str = LOADER_URL) -> str:
    """Return the YAML text with the loader added under frontend.extra_module_url.

    Only plain, block-style YAML is changed. Anything unusual (frontend loaded
    via !include, inline lists, tabs) raises CannotEdit so the user does it by hand.
    """
    if url in text:
        return text
    if "\t" in text:
        raise CannotEdit("tabs")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    start = None
    for i, line in enumerate(lines):
        m = _TOP_KEY.match(line)
        if m and m.group(1) == "frontend":
            rest = m.group(2).split("#", 1)[0].strip()
            if rest:  # e.g. "frontend: !include frontend.yaml" or "frontend: {}"
                raise CannotEdit("frontend inline")
            start = i
            break

    if start is None:
        block = ["", "frontend:", "  extra_module_url:", f"    - {url}"]
        body = text.rstrip("\r\n")
        return body + newline + newline.join(block) + newline

    # The frontend block runs until the next line that is not indented.
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _is_content(lines[j]) and _indent(lines[j]) == 0:
            end = j
            break
    children = [k for k in range(start + 1, end) if _is_content(lines[k])]
    child_indent = _indent(lines[children[0]]) if children else 2

    key_line = None
    for k in children:
        if _indent(lines[k]) == child_indent and lines[k].strip().startswith("extra_module_url:"):
            key_line = k
            break

    if key_line is None:
        insert = [
            " " * child_indent + "extra_module_url:",
            " " * (child_indent + 2) + f"- {url}",
        ]
        lines[start + 1 : start + 1] = insert
        return newline.join(lines) + newline

    after = lines[key_line].split(":", 1)[1].split("#", 1)[0].strip()
    if after:  # inline list like "extra_module_url: [a, b]"
        raise CannotEdit("inline list")
    items = [
        k
        for k in range(key_line + 1, end)
        if _is_content(lines[k]) and _indent(lines[k]) > child_indent
    ]
    item_indent = _indent(lines[items[0]]) if items else child_indent + 2
    if items and not lines[items[0]].strip().startswith("-"):
        raise CannotEdit("unexpected structure")
    insert_at = (items[-1] + 1) if items else key_line + 1
    lines.insert(insert_at, " " * item_indent + f"- {url}")
    return newline.join(lines) + newline


def install_loader(config_path: Path, url: str = LOADER_URL) -> bool:
    """Edit configuration.yaml in place. Returns True if the file was changed.

    A backup is written first. If the edited file does not load as valid
    Home Assistant YAML with the loader in place, the backup is restored.
    """
    original = config_path.read_text(encoding="utf-8")
    new = add_loader(original, url)
    if new == original:
        return False
    backup = config_path.with_name(config_path.name + BACKUP_SUFFIX)
    shutil.copy2(config_path, backup)
    config_path.write_text(new, encoding="utf-8")
    try:
        data = load_yaml(str(config_path), Secrets(config_path.parent))
        modules = ((data or {}).get("frontend") or {}).get("extra_module_url") or []
        if url not in modules:
            raise CannotEdit("check failed")
    except (HomeAssistantError, CannotEdit, AttributeError, OSError) as err:
        shutil.copy2(backup, config_path)
        raise CannotEdit(str(err)) from err
    return True


def remove_loader(text: str, url: str = LOADER_URL) -> str:
    """Return the YAML text without the loader (the reverse of add_loader).

    Empty `extra_module_url:` and `frontend:` keys that only held the loader are
    removed as well. Inline lists and other unusual layouts raise CannotEdit.
    """
    if url not in text:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if url in line]
    for i in hits:
        if lines[i].strip().split("#", 1)[0].strip() != f"- {url}":
            raise CannotEdit("inline")
    for i in reversed(hits):
        del lines[i]

    def _drop_empty_key(key: str, top_level: bool) -> None:
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.split("#", 1)[0].strip() != f"{key}:":
                continue
            if top_level != (_indent(line) == 0):
                continue
            nxt = next((j for j in range(i + 1, len(lines)) if _is_content(lines[j])), None)
            if nxt is None or _indent(lines[nxt]) <= _indent(line):
                del lines[i]
                # drop a blank line we left behind before the key
                if i > 0 and i <= len(lines) and not lines[i - 1].strip() and (
                    i == len(lines) or not lines[i].strip()
                ):
                    del lines[i - 1]
            return

    _drop_empty_key("extra_module_url", top_level=False)
    _drop_empty_key("frontend", top_level=True)
    return newline.join(lines).rstrip("\r\n") + newline


def uninstall_loader(config_path: Path, url: str = LOADER_URL) -> bool:
    """Take the loader out of configuration.yaml again (backup + check)."""
    original = config_path.read_text(encoding="utf-8")
    new = remove_loader(original, url)
    if new == original:
        return False
    backup = config_path.with_name(config_path.name + BACKUP_SUFFIX)
    shutil.copy2(config_path, backup)
    config_path.write_text(new, encoding="utf-8")
    try:
        data = load_yaml(str(config_path), Secrets(config_path.parent))
        modules = ((data or {}).get("frontend") or {}).get("extra_module_url") or []
        if url in modules:
            raise CannotEdit("still there")
    except (HomeAssistantError, CannotEdit, AttributeError, OSError) as err:
        shutil.copy2(backup, config_path)
        raise CannotEdit(str(err)) from err
    return True
