# serve.py
import os
import sys
import asyncio
import signal
import contextlib
from aiohttp import web

PORT = int(os.environ.get("PORT", "10000"))

# -------- HTTP (health) --------
async def root(_):
    return web.json_response({"ok": True, "app": "FinTrack", "msg": "alive"})

async def health(_):
    return web.Response(text="ok")

async def start_http():
    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health)
    app.router.add_get("/healthz", health)  # на всякий случай
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    # держим задачу живой
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        with contextlib.suppress(Exception):
            await runner.cleanup()

# -------- Bot runner --------
async def run_bot_forever():
    """
    Запускает Main.py как подпроцесс, перезапускает при завершении.
    Между рестартами делает небольшую паузу, чтобы не уйти в быстрый цикл.
    """
    while True:
        # stdout/stderr прокинем в текущий процесс (полезно для Render logs)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "Main.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        async def pipe_stream(stream, prefix):
            try:
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    # печатаем сразу, чтобы видеть логи бота в Render
                    print(f"[{prefix}] {line.decode(errors='ignore').rstrip()}", flush=True)
            except asyncio.CancelledError:
                pass

        t_out = asyncio.create_task(pipe_stream(proc.stdout, "BOT"))
        t_err = asyncio.create_task(pipe_stream(proc.stderr, "ERR"))

        rc = await proc.wait()
        # дочищаем задачи пайпов
        for t in (t_out, t_err):
            with contextlib.suppress(asyncio.CancelledError):
                t.cancel()
                await t

        print(f"Main.py exited with code {rc}. Restarting in 1s...", flush=True)
        await asyncio.sleep(1)

# -------- Main orchestrator --------
async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    # корректные сигналы для Render (Linux)
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # на всякий случай под Windows
            loop.add_signal_handler(sig, stop.set)

    t_http = asyncio.create_task(start_http(), name="http")
    t_bot  = asyncio.create_task(run_bot_forever(), name="bot")

    # ждём сигнала остановки
    await stop.wai
