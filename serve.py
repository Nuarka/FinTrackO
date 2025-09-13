# serve.py (v2, robust)
import os
import sys
import asyncio
import contextlib
from aiohttp import web

PORT = int(os.environ.get("PORT", "10000"))

# ---------- HTTP (health) ----------
async def root(_):
    return web.json_response({"ok": True, "app": "FinTrack", "msg": "alive"})

async def health(_):
    return web.Response(text="ok")

async def start_http():
    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health)
    app.router.add_get("/healthz", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"[SERVE] HTTP up on 0.0.0.0:{PORT}", flush=True)

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        with contextlib.suppress(Exception):
            await runner.cleanup()

# ---------- Bot runner ----------
async def run_bot_forever():
    while True:
        print("[SERVE] Spawning Main.py ...", flush=True)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "Main.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def pipe(stream, tag):
            try:
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    print(f"[{tag}] {line.decode(errors='ignore').rstrip()}", flush=True)
            except asyncio.CancelledError:
                pass

        t_out = asyncio.create_task(pipe(proc.stdout, "BOT"))
        t_err = asyncio.create_task(pipe(proc.stderr, "ERR"))

        rc = await proc.wait()
        for t in (t_out, t_err):
            with contextlib.suppress(asyncio.CancelledError):
                t.cancel()
                await t

        print(f"[SERVE] Main.py exited with code {rc}. Restarting in 1s", flush=True)
        await asyncio.sleep(1)

# ---------- Main orchestrator ----------
async def main():
    # 1) сначала поднимаем HTTP, чтобы health-check Render прошёл быстро
    http_task = asyncio.create_task(start_http(), name="http")

    # ждём чуть-чуть, чтобы health уже отвечал до запуска бота
    await asyncio.sleep(0.5)

    # 2) запускаем перезапускаемый бот-процесс
    bot_task = asyncio.create_task(run_bot_forever(), name="bot")

    # 3) никогда не выходим сами; ждём, пока любая задача не упадёт
    done, pending = await asyncio.wait(
        {http_task, bot_task},
        return_when=asyncio.FIRST_EXCEPTION,
    )
    # если упало — выведем причину, но не завершимся мгновенно
    for d in done:
        ex = d.exception()
        if ex:
            print(f"[SERVE] Task crashed: {ex!r}", flush=True)
    # держим процесс живым
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        # финальный предохранитель: печатаем и не гасим контейнер мгновенно
        print(f"[SERVE] Fatal at top-level: {e!r}", flush=True)
        # делаем «вечный» sleep, чтобы Render не счёл деплой завершившимся сразу
        try:
            asyncio.get_event_loop().run_until_complete(asyncio.sleep(10**9))
        except Exception:
            pass
