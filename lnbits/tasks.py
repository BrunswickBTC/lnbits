import asyncio
import uuid
from collections.abc import Callable, Coroutine

from loguru import logger

from lnbits.core.models import Payment
from lnbits.task_manager import task_manager


# DEPRECATED: use task_manager.create_task instead.
def create_task(coro: Coroutine) -> asyncio.Task:
    logger.warning("`create_task` is deprecated, use task_manager.create_task instead.")
    return task_manager.create_task(coro)._task


# DEPRECATED: use task_manager.create_task with `name` kwarg.
def create_unique_task(name: str, coro: Coroutine) -> asyncio.Task:
    logger.warning("`create_unique_task` is deprecated, use task_manager.create_task.")
    return task_manager.create_task(coro, name=name)._task


# DEPRECATED: use task_manager.create_permanent_task instead.
def create_permanent_task(func: Callable[[], Coroutine]) -> asyncio.Task:
    logger.warning(
        "`create_permanent_task` is deprecated, "
        "use task_manager.create_permanent_task instead."
    )
    return task_manager.create_permanent_task(func)._task


# DEPRECATED: use task_manager.create_permanent_task with `name` argument instead.
def create_permanent_unique_task(
    name: str, coro: Callable[[], Coroutine]
) -> asyncio.Task:
    logger.warning(
        "`create_permanent_unique_task` is deprecated, "
        "use task_manager.create_permanent_task."
    )
    return create_unique_task(name, catch_everything_and_restart(coro, name))


# DEPRECATED don't use this, use task_manager.create_permanent_task instead.
async def catch_everything_and_restart(
    func: Callable[[], Coroutine],
    name: str = "unnamed",
) -> Coroutine:
    logger.warning(
        "`catch_everything_and_restart` is deprecated, it is internal to task_manager "
        "and should not be needed outside. Use task_manager.create_permanent_task."
    )
    _ = name
    return await task_manager._catch_everything_and_restart(func)


def register_invoice_listener(send_chan: asyncio.Queue, name: str | None = None):
    """
    DEPRECATED: use task_manager.register_invoice_listener instead,
    which also allows to pass a callback instead of a queue.
    This method will still work but it is not recommended for new code.
    """
    logger.warning(
        "register_invoice_listener is deprecated use "
        "task_manager.register_invoice_listener instead."
    )
    if not name:
        # fallback to a random name if extension didn't provide one
        name = f"deprecated_listener_{str(uuid.uuid4())[:8]}"

    # workaround we register to pass in the queue to the task manager and it
    # will be filled with the payments, but we don't want to do anything with
    # it here, so we just create a dummy consumer that does nothing. legacy
    # extension code will still work because they will get the payments from
    # the queue and register another task to consum it.
    async def nop(_: Payment):
        pass

    task_manager.create_permanent_task(
        task_manager._invoice_listener_worker(nop, send_chan),
        name=name,
    )
