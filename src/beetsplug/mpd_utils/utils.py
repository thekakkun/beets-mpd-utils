from typing import AsyncIterable, AsyncIterator
import asyncio


async def debounce[T](
    source: AsyncIterable[T],
    delay: float,
) -> AsyncIterator[T]:
    task: asyncio.Task | None = None
    yielded_last = True

    async def timer(value: T) -> T:
        await asyncio.sleep(delay)
        return value

    async for item in source:
        yielded_last = False

        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(timer(item))

        try:
            value = await task
            yield value
            yielded_last = True
            task = None
        except asyncio.CancelledError:
            continue

    if task and not task.done() and not yielded_last:
        try:
            value = await task
            yield value
        except asyncio.CancelledError:
            pass
