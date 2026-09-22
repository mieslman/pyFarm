from typing import TYPE_CHECKING, Callable, TypeVar

if TYPE_CHECKING:
    from app.modules.seasonpass.handlers.base import BaseTaskHandler

THandler = TypeVar("THandler", bound="BaseTaskHandler")

_TASK_HANDLERS: dict[str, type["BaseTaskHandler"]] = {}


def register_task(task_type: str) -> Callable[[type[THandler]], type[THandler]]:
    """Decorator to register a task handler class for a specific season pass task type."""

    def decorator(handler_cls: type[THandler]) -> type[THandler]:
        _TASK_HANDLERS[task_type.lower()] = handler_cls
        return handler_cls

    return decorator


def get_task_handler(task_type: str) -> type["BaseTaskHandler"] | None:
    """Resolve the registered handler class for a given task type."""
    return _TASK_HANDLERS.get(task_type.lower())


def get_registered_types() -> list[str]:
    """Return all registered task type identifiers."""
    return list(_TASK_HANDLERS.keys())
