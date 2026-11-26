"""configuration.yaml editing must be safe for every common layout."""

from pathlib import Path

import pytest
import yaml

from custom_components.portier.yaml_setup import (
    LOADER_URL,
    CannotEdit,
    add_loader,
    install_loader,
    remove_loader,
    uninstall_loader,
)


def _modules(text: str):
    class L(yaml.SafeLoader):
        pass

    L.add_multi_constructor("!", lambda loader, suffix, node: None)
    return (yaml.load(text, Loader=L).get("frontend") or {}).get("extra_module_url")


def test_no_frontend_block():
    src = "default_config:\n\nhomeassistant:\n  name: Haus\n"
    out = add_loader(src)
    assert _modules(out) == [LOADER_URL]
    assert out.startswith(src.rstrip("\n"))


def test_frontend_with_themes():
    src = "default_config:\nfrontend:\n  themes: !include_dir_merge_named themes\nautomation: !include automations.yaml\n"
    out = add_loader(src)
    assert _modules(out) == [LOADER_URL]
    assert "themes: !include_dir_merge_named themes" in out
    assert "automation: !include automations.yaml" in out


def test_frontend_empty_block_with_comment():
    src = "frontend: # UI\n\nsensor:\n  - platform: time_date\n"
    assert _modules(add_loader(src)) == [LOADER_URL]


def test_existing_extra_module_url_list():
    src = "frontend:\n    themes: {}\n    extra_module_url:\n        - /local/card-mod.js\nhttp:\n  server_port: 8123\n"
    out = add_loader(src)
    assert _modules(out) == ["/local/card-mod.js", LOADER_URL]
    assert "server_port: 8123" in out


def test_already_there_is_unchanged():
    src = f"frontend:\n  extra_module_url:\n    - {LOADER_URL}\n"
    assert add_loader(src) == src


def test_crlf_kept():
    src = "default_config:\r\nfrontend:\r\n  themes: {}\r\n"
    out = add_loader(src)
    assert "\r\n" in out and "\n" not in out.replace("\r\n", "")
    assert _modules(out.replace("\r\n", "\n")) == [LOADER_URL]


@pytest.mark.parametrize(
    "src",
    [
        "frontend: !include frontend.yaml\n",
        "frontend:\n  extra_module_url: [/local/a.js]\n",
        "frontend:\n\textra_module_url:\n",
    ],
)
def test_unusual_layouts_are_left_alone(src):
    with pytest.raises(CannotEdit):
        add_loader(src)


def test_install_with_backup_secrets_and_includes(tmp_path: Path):
    (tmp_path / "secrets.yaml").write_text("pw: geheim\n")
    (tmp_path / "automations.yaml").write_text("[]\n")
    cfg = tmp_path / "configuration.yaml"
    src = "default_config:\nhttp:\n  api_password: !secret pw\nautomation: !include automations.yaml\n"
    cfg.write_text(src)
    assert install_loader(cfg) is True
    assert (tmp_path / "configuration.yaml.portier-backup").read_text() == src
    assert LOADER_URL in cfg.read_text()
    assert install_loader(cfg) is False  # second run changes nothing


def test_install_restores_backup_when_result_invalid(tmp_path: Path):
    cfg = tmp_path / "configuration.yaml"
    src = "frontend:\n  themes: !include missing_folder_file.yaml\n"
    cfg.write_text(src)
    with pytest.raises(CannotEdit):
        install_loader(cfg)
    assert cfg.read_text() == src


@pytest.mark.parametrize(
    "src",
    [
        "default_config:\n\nhomeassistant:\n  name: Haus\n",
        "default_config:\nfrontend:\n  themes: !include_dir_merge_named themes\nautomation: !include automations.yaml\n",
        "frontend:\n  extra_module_url:\n    - /local/card-mod.js\nsensor:\n  - platform: time_date\n",
        "default_config:\r\nfrontend:\r\n  themes: !include_dir_merge_named themes\r\n",
    ],
)
def test_remove_undoes_add(src):
    out = remove_loader(add_loader(src))
    assert LOADER_URL not in out
    assert out.strip() == src.strip()


def test_remove_keeps_other_modules():
    src = "frontend:\n  extra_module_url:\n    - /local/card-mod.js\n    - /local/portier/loader.js\n"
    assert _modules(remove_loader(src)) == ["/local/card-mod.js"]


def test_remove_inline_list_is_left_alone():
    with pytest.raises(CannotEdit):
        remove_loader("frontend:\n  extra_module_url: [/local/portier/loader.js]\n")


def test_uninstall_with_backup(tmp_path: Path):
    cfg = tmp_path / "configuration.yaml"
    cfg.write_text("default_config:\n")
    install_loader(cfg)
    assert uninstall_loader(cfg) is True
    assert LOADER_URL not in cfg.read_text()
    assert (tmp_path / "configuration.yaml.portier-backup").exists()
    assert uninstall_loader(cfg) is False


def test_line_endings_kept(tmp_path: Path):
    """A Windows-edited configuration.yaml keeps its CRLF line endings."""
    cfg = tmp_path / "configuration.yaml"
    cfg.write_bytes(b"default_config:\r\nhomeassistant:\r\n  name: Haus\r\n")
    assert install_loader(cfg) is True
    raw = cfg.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert uninstall_loader(cfg) is True
    assert cfg.read_bytes() == b"default_config:\r\nhomeassistant:\r\n  name: Haus\r\n"


def test_failed_check_restores_exact_bytes(tmp_path: Path):
    cfg = tmp_path / "configuration.yaml"
    src = b"frontend:\r\n  themes: !include missing_folder_file.yaml\r\n"
    cfg.write_bytes(src)
    with pytest.raises(CannotEdit):
        install_loader(cfg)
    assert cfg.read_bytes() == src
    assert not (tmp_path / "configuration.yaml.portier-tmp").exists()


def test_commented_out_line_does_not_count():
    src = "frontend:\n  extra_module_url:\n    # - /local/portier/loader.js\n    - /local/other.js\n"
    out = add_loader(src)
    assert _modules(out) == ["/local/other.js", LOADER_URL]
    back = remove_loader(out)
    assert _modules(back) == ["/local/other.js"] and "# - /local/portier/loader.js" in back


def test_package_with_extra_module_url_is_left_alone(tmp_path: Path):
    """A package that already sets extra_module_url (e.g. card-mod) would clash."""
    cfg = tmp_path / "configuration.yaml"
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "ui.yaml").write_text(
        "frontend:\n  extra_module_url:\n    - /local/card-mod.js\n"
    )
    src = "homeassistant:\n  packages: !include_dir_named packages\n"
    cfg.write_text(src)
    with pytest.raises(CannotEdit):
        install_loader(cfg)
    assert cfg.read_text() == src
