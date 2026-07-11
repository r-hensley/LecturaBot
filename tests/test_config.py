from __future__ import annotations

from pathlib import Path

import pytest

from lecturabot.config import ConfigError, load_config
from lecturabot.models import ChannelMode


TOKEN_ENV = {"LECTURABOT_TOKEN": "test-token-do-not-log"}


def _pair(
    *,
    name: str = "lectura-1",
    text_channel_id: int = 101,
    voice_channel_id: int = 201,
    mode: str = "standard",
) -> str:
    return f"""
[[channel_pairs]]
name = {name!r}
text_channel_id = {text_channel_id}
voice_channel_id = {voice_channel_id}
mode = {mode!r}
"""


def _config_text(*pairs: str, bot_extra: str = "") -> str:
    configured_pairs = pairs or (_pair(),)
    return (
        """
[bot]
guild_id = 1
bot_status_contact_user_id = 2
issue_contact_user_id = 3
"""
        + bot_extra
        + """

[database]
path = "state/lecturabot.sqlite3"
"""
        + "".join(configured_pairs)
    )


def _write_config(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "deployment" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(content, encoding="utf-8")
    return config_path


def test_loads_typed_config_and_resolves_database_from_config_directory(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        _config_text(
            _pair(),
            _pair(
                name="other-languages",
                text_channel_id=102,
                voice_channel_id=202,
                mode="custom_only",
            ),
            bot_extra="dev_guild_id = 4\n",
        ),
    )

    config = load_config(config_path, environ=TOKEN_ENV)

    assert config.token == TOKEN_ENV["LECTURABOT_TOKEN"]
    assert config.guild_id == 1
    assert config.bot_status_contact_user_id == 2
    assert config.issue_contact_user_id == 3
    assert config.dev_guild_id == 4
    assert config.database_path == (
        config_path.parent / "state" / "lecturabot.sqlite3"
    ).resolve()
    assert config.channel_pairs[0].mode is ChannelMode.STANDARD
    assert config.channel_pairs[1].mode is ChannelMode.CUSTOM_ONLY
    assert config.pair_for_text_channel(102) == config.channel_pairs[1]
    assert config.pair_for_voice_channel(201) == config.channel_pairs[0]
    assert config.pair_for_text_channel(999) is None


def test_token_is_required_from_environment_and_hidden_from_repr(
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path, _config_text())

    with pytest.raises(ConfigError, match="LECTURABOT_TOKEN"):
        load_config(config_path, environ={})

    config = load_config(config_path, environ=TOKEN_ENV)
    assert TOKEN_ENV["LECTURABOT_TOKEN"] not in repr(config)


def test_rejects_token_in_toml(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        _config_text(bot_extra='token = "must-not-live-here"\n'),
    )

    with pytest.raises(ConfigError, match=r"unknown setting.*token"):
        load_config(config_path, environ=TOKEN_ENV)


@pytest.mark.parametrize(
    ("pairs", "error_match"),
    [
        (
            (_pair(), _pair(name="LECTURA-1", text_channel_id=102, voice_channel_id=202)),
            "names must be unique",
        ),
        (
            (_pair(), _pair(name="lectura-2", text_channel_id=101, voice_channel_id=202)),
            "channel ID 101 is reused",
        ),
        (
            (_pair(), _pair(name="lectura-2", text_channel_id=102, voice_channel_id=201)),
            "channel ID 201 is reused",
        ),
        (
            (_pair(), _pair(name="lectura-2", text_channel_id=201, voice_channel_id=202)),
            "channel ID 201 is reused",
        ),
    ],
)
def test_rejects_duplicate_names_and_channel_id_collisions(
    tmp_path: Path,
    pairs: tuple[str, ...],
    error_match: str,
) -> None:
    config_path = _write_config(tmp_path, _config_text(*pairs))

    with pytest.raises(ConfigError, match=error_match):
        load_config(config_path, environ=TOKEN_ENV)


def test_rejects_same_text_and_voice_channel(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        _config_text(_pair(text_channel_id=101, voice_channel_id=101)),
    )

    with pytest.raises(ConfigError, match="same ID"):
        load_config(config_path, environ=TOKEN_ENV)


def test_rejects_unknown_channel_mode(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        _config_text(_pair(mode="surprise")),
    )

    with pytest.raises(ConfigError, match=r"standard.*custom_only"):
        load_config(config_path, environ=TOKEN_ENV)


@pytest.mark.parametrize("bad_id", [0, -1, True])
def test_rejects_non_positive_or_boolean_discord_ids(
    tmp_path: Path, bad_id: int
) -> None:
    literal = "true" if bad_id is True else str(bad_id)
    config_path = _write_config(
        tmp_path,
        _config_text().replace("guild_id = 1", f"guild_id = {literal}", 1),
    )

    with pytest.raises(ConfigError, match="positive integer Discord ID"):
        load_config(config_path, environ=TOKEN_ENV)
