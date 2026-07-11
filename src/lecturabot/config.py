"""Validated configuration loading for LecturaBot.

Non-secret settings live in a TOML file.  The Discord token is deliberately
loaded only from the process environment so it cannot be added to a config
file accidentally.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib
from typing import Any, NoReturn

from .models import ChannelMode


TOKEN_ENV_VAR = "LECTURABOT_TOKEN"


class ConfigError(ValueError):
    """Raised when LecturaBot configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class ChannelPairConfig:
    """One isolated reading text/voice channel pair."""

    name: str
    text_channel_id: int
    voice_channel_id: int
    mode: ChannelMode


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Fully validated runtime configuration."""

    token: str = field(repr=False)
    guild_id: int
    bot_status_contact_user_id: int
    issue_contact_user_id: int
    database_path: Path
    channel_pairs: tuple[ChannelPairConfig, ...]
    dev_guild_id: int | None = None

    def pair_for_text_channel(self, channel_id: int) -> ChannelPairConfig | None:
        """Return the configured pair for a text channel, if any."""

        return next(
            (
                pair
                for pair in self.channel_pairs
                if pair.text_channel_id == channel_id
            ),
            None,
        )

    def pair_for_voice_channel(self, channel_id: int) -> ChannelPairConfig | None:
        """Return the configured pair for a voice channel, if any."""

        return next(
            (
                pair
                for pair in self.channel_pairs
                if pair.voice_channel_id == channel_id
            ),
            None,
        )


def load_config(
    path: str | Path = "config.toml",
    *,
    environ: Mapping[str, str] | None = None,
) -> BotConfig:
    """Load and validate TOML configuration plus the environment token.

    Relative database paths are resolved relative to the TOML file, not the
    process working directory.
    """

    config_path = Path(path).expanduser().resolve()
    source_environ = os.environ if environ is None else environ
    token = source_environ.get(TOKEN_ENV_VAR, "").strip()
    if not token:
        raise ConfigError(f"{TOKEN_ENV_VAR} must be set to a non-empty value")

    try:
        with config_path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file not found: {config_path}") from error
    except OSError as error:
        raise ConfigError(f"could not read configuration file: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {config_path}: {error}") from error

    _reject_unknown_keys(raw, {"bot", "database", "channel_pairs"}, "root")
    bot = _require_table(raw, "bot", "root")
    database = _require_table(raw, "database", "root")
    pairs = _require_table_list(raw, "channel_pairs", "root")

    _reject_unknown_keys(
        bot,
        {
            "guild_id",
            "bot_status_contact_user_id",
            "issue_contact_user_id",
            "dev_guild_id",
        },
        "bot",
    )
    _reject_unknown_keys(database, {"path"}, "database")

    guild_id = _require_snowflake(bot, "guild_id", "bot")
    bot_status_contact_user_id = _require_snowflake(
        bot, "bot_status_contact_user_id", "bot"
    )
    issue_contact_user_id = _require_snowflake(
        bot, "issue_contact_user_id", "bot"
    )
    dev_guild_id = _optional_snowflake(bot, "dev_guild_id", "bot")

    database_value = _require_nonempty_string(database, "path", "database")
    database_path = Path(database_value).expanduser()
    if not database_path.is_absolute():
        database_path = config_path.parent / database_path
    database_path = database_path.resolve()

    channel_pairs = tuple(
        _parse_channel_pair(pair, index) for index, pair in enumerate(pairs)
    )
    if not channel_pairs:
        raise ConfigError("channel_pairs must contain at least one channel pair")
    _validate_channel_pair_uniqueness(channel_pairs)

    return BotConfig(
        token=token,
        guild_id=guild_id,
        bot_status_contact_user_id=bot_status_contact_user_id,
        issue_contact_user_id=issue_contact_user_id,
        database_path=database_path,
        channel_pairs=channel_pairs,
        dev_guild_id=dev_guild_id,
    )


def _parse_channel_pair(raw: Mapping[str, Any], index: int) -> ChannelPairConfig:
    location = f"channel_pairs[{index}]"
    _reject_unknown_keys(
        raw,
        {"name", "text_channel_id", "voice_channel_id", "mode"},
        location,
    )
    name = _require_nonempty_string(raw, "name", location)
    text_channel_id = _require_snowflake(raw, "text_channel_id", location)
    voice_channel_id = _require_snowflake(raw, "voice_channel_id", location)
    mode_value = _require_nonempty_string(raw, "mode", location)

    try:
        mode = ChannelMode(mode_value)
    except ValueError as error:
        valid_modes = ", ".join(mode.value for mode in ChannelMode)
        raise ConfigError(
            f"{location}.mode must be one of: {valid_modes}"
        ) from error

    if text_channel_id == voice_channel_id:
        raise ConfigError(
            f"{location} cannot use the same ID for text and voice channels"
        )

    return ChannelPairConfig(
        name=name,
        text_channel_id=text_channel_id,
        voice_channel_id=voice_channel_id,
        mode=mode,
    )


def _validate_channel_pair_uniqueness(
    channel_pairs: tuple[ChannelPairConfig, ...],
) -> None:
    names: dict[str, str] = {}
    channel_ids: dict[int, tuple[str, str]] = {}

    for pair in channel_pairs:
        normalized_name = pair.name.casefold()
        previous_name = names.get(normalized_name)
        if previous_name is not None:
            raise ConfigError(
                "channel pair names must be unique (case-insensitive): "
                f"{previous_name!r} and {pair.name!r}"
            )
        names[normalized_name] = pair.name

        for channel_kind, channel_id in (
            ("text", pair.text_channel_id),
            ("voice", pair.voice_channel_id),
        ):
            previous = channel_ids.get(channel_id)
            if previous is not None:
                previous_name, previous_kind = previous
                raise ConfigError(
                    f"Discord channel ID {channel_id} is reused by "
                    f"{previous_name!r} ({previous_kind}) and "
                    f"{pair.name!r} ({channel_kind})"
                )
            channel_ids[channel_id] = (pair.name, channel_kind)


def _require_table(
    table: Mapping[str, Any], key: str, location: str
) -> Mapping[str, Any]:
    value = table.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{location}.{key} must be a TOML table")
    return value


def _require_table_list(
    table: Mapping[str, Any], key: str, location: str
) -> list[Mapping[str, Any]]:
    value = table.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ConfigError(f"{location}.{key} must be an array of TOML tables")
    return value


def _require_nonempty_string(
    table: Mapping[str, Any], key: str, location: str
) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _require_snowflake(
    table: Mapping[str, Any], key: str, location: str
) -> int:
    if key not in table:
        raise ConfigError(f"missing required setting: {location}.{key}")
    return _validate_snowflake(table[key], f"{location}.{key}")


def _optional_snowflake(
    table: Mapping[str, Any], key: str, location: str
) -> int | None:
    if key not in table:
        return None
    return _validate_snowflake(table[key], f"{location}.{key}")


def _validate_snowflake(value: Any, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{location} must be a positive integer Discord ID")
    return value


def _reject_unknown_keys(
    table: Mapping[str, Any], allowed: set[str], location: str
) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        _raise_unknown_keys(location, unknown)


def _raise_unknown_keys(location: str, unknown: list[str]) -> NoReturn:
    formatted = ", ".join(repr(key) for key in unknown)
    raise ConfigError(f"unknown setting(s) in {location}: {formatted}")
