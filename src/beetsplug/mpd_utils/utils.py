import asyncio
from typing import Any, AsyncIterable, AsyncIterator


async def debounce[T](source: AsyncIterable[T], delay: int | float) -> AsyncIterator[T]:
    it = source.__aiter__()
    next_task: asyncio.Task[T] = asyncio.ensure_future(it.__anext__())
    timer_task: asyncio.Task[None] | None = None
    latest: T
    has_latest = False

    try:
        while True:
            waiting: set[asyncio.Task[Any]] = {next_task}
            if timer_task is not None:
                waiting.add(timer_task)

            done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

            if next_task in done:
                try:
                    latest = next_task.result()
                    has_latest = True
                except StopAsyncIteration:
                    # Source exhausted: flush any pending item and stop.
                    if timer_task is not None:
                        timer_task.cancel()
                    if has_latest:
                        yield latest
                    return

                # New item arrived: reset the debounce timer.
                if timer_task is not None:
                    timer_task.cancel()
                timer_task = asyncio.ensure_future(asyncio.sleep(delay))
                next_task = asyncio.ensure_future(it.__anext__())

            # Only fires for the *current* timer_task, since a reset above
            # reassigns the variable before this check runs.
            if timer_task is not None and timer_task in done:
                yield latest
                has_latest = False
                timer_task = None
    finally:
        # Clean up outstanding tasks if the consumer stops early
        # (e.g. via `break` or an exception).
        next_task.cancel()
        if timer_task is not None:
            timer_task.cancel()
        await asyncio.gather(
            next_task, *([timer_task] if timer_task else []), return_exceptions=True
        )
