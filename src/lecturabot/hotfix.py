"""Refresh the reading runtime from the existing Discord extension reload."""

from __future__ import annotations

from contextlib import AsyncExitStack, contextmanager
from importlib.util import module_from_spec
import logging
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import LecturaBot


LOGGER = logging.getLogger(__name__)
# Import dependencies before their callers. The gateway, bot, and socket stay live.
RUNTIME_MODULES = (
    "corrections", "models", "repository", "service", "rendering",
    "queue_panel", "views", "controller",
)
LIVE_ENUMS = ("ChannelMode", "Language", "Level", "SessionPhase", "CorrectionSource", "QueueLayout")


@contextmanager
def _load_runtime(sources: dict[str, bytes]):
    """Load replacements without editing globals already used by running tasks."""
    package = sys.modules[__package__]
    originals = {name: sys.modules[f"{__package__}.{name}"] for name in RUNTIME_MODULES}
    replacements = {}
    try:
        for name, original in originals.items():
            module = module_from_spec(original.__spec__)
            sys.modules[original.__name__] = module
            setattr(package, name, module)
            exec(compile(sources[name], module.__file__, "exec"), vars(module))
            if name == "models":
                # Live config, callbacks and forms can still contain these values.
                for enum_name in LIVE_ENUMS:
                    old_enum = getattr(original, enum_name, None)
                    if old_enum is not None:
                        if list(old_enum) != list(getattr(module, enum_name)):
                            raise RuntimeError(f"Changing {enum_name} requires a restart")
                        setattr(module, enum_name, old_enum)
            replacements[name] = module
        yield replacements
    except BaseException:
        for name, original in originals.items():
            sys.modules[original.__name__] = original
            setattr(package, name, original)
        raise


async def _replace_runtime(bot: LecturaBot, sources: dict[str, bytes]) -> None:
    controller, service, repository = bot.controller, bot.service, bot.repository
    rooms = sorted({pair.text_channel_id for pair in bot.config.channel_pairs} | service._states.keys())
    async with AsyncExitStack() as locks:
        await locks.enter_async_context(bot._reconcile_lock)
        if hasattr(controller, "_layout_lock"):
            await locks.enter_async_context(controller._layout_lock)
        for room in rooms:
            await locks.enter_async_context(controller._ui_lock_for(room))
        for room in rooms:
            await locks.enter_async_context(service._lock_for(room))

        snapshots = {room: state.to_dict() for room, state in service._states.items()}
        with _load_runtime(sources) as runtime:
            new_repository = runtime["repository"].SQLiteRepository(repository.database_path)
            new_service = runtime["service"].SessionService(repository)
            new_controller = runtime["controller"].LecturaController(bot, bot.config, service)
            sessions = {
                room: runtime["models"].SessionState.from_dict(snapshot)
                for room, snapshot in snapshots.items()
            }
            layout = await new_repository.get_queue_layout()
            replacements = (
                (repository, new_repository), (service, new_service), (controller, new_controller),
            )
            originals = [(obj, type(obj), vars(obj).copy()) for obj, _ in replacements]
            try:
                # Early socket builds restored this method on the instance at cog unload.
                old_action = vars(controller).get("handle_queue_action")
                if getattr(old_action, "__func__", None) is type(controller).handle_queue_action:
                    del controller.handle_queue_action
                # Retain the object identities, including those held by open modals
                # and reconnect tasks. Constructors supply any newly added fields.
                for obj, replacement in replacements:
                    obj.__class__ = type(replacement)
                    for key, value in vars(replacement).items():
                        vars(obj).setdefault(key, value)
                service._states = sessions
                controller.queue_layout = layout
                _register_views(bot)
            except BaseException:
                for obj, cls, attributes in originals:
                    obj.__class__ = cls
                    vars(obj).clear()
                    vars(obj).update(attributes)
                raise


def _register_views(bot: LecturaBot) -> None:
    from .queue_panel import QueuePanel, QueueSkipButton
    from .views import QueueView, ReadingView, TextPickerView

    views = (
        QueueView(bot.controller), TextPickerView(bot.controller), QueuePanel(bot.controller),
        ReadingView(bot.controller, legacy_turn_controls=True),
    )
    if not all(view.is_persistent() for view in views):
        raise ValueError("Reading views must be persistent")
    for view in getattr(bot, "_reading_views", ()):
        view.stop()
    old_skip_button = getattr(bot, "_reading_skip_button", None)
    if old_skip_button is not None:
        bot.remove_dynamic_items(old_skip_button)
    for view in views:
        bot.add_view(view)
    bot.add_dynamic_items(QueueSkipButton)
    bot._reading_views = views
    bot._reading_skip_button = QueueSkipButton


async def refresh_runtime(bot: LecturaBot) -> None:
    root = Path(__file__).parent
    sources = {name: (root / f"{name}.py").read_bytes() for name in RUNTIME_MODULES}
    previous = getattr(bot, "_reading_sources", None)
    if previous is None and hasattr(bot.controller, "set_queue_layout"):
        _register_views(bot)  # Fresh startup already imported the current files.
    elif sources != previous:
        await _replace_runtime(bot, sources)
        bot._reading_panels_pending = True
        LOGGER.info("Replaced the live reading runtime")
    bot._reading_sources = sources

    if getattr(bot, "_reading_panels_pending", False):
        if not bot.is_ready():
            bot._reconciled = False
            return
        try:
            bot._reading_panels_pending = not await bot.controller.reconcile_session_messages(set())
        except Exception:
            LOGGER.exception("Could not refresh reading panels during reload")
        if bot._reading_panels_pending:
            LOGGER.warning("Some reading panels still need refreshing; retry reload features")
