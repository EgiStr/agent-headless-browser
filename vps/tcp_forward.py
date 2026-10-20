"""Container-only stand-in for `ssh -L`: expose 127.0.0.1:9333 on 0.0.0.0:9334."""
import asyncio

async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data); await w.drain()
    finally:
        w.close()

async def handle(cr, cw):
    ur, uw = await asyncio.open_connection("127.0.0.1", 9333)
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw), return_exceptions=True)

async def main():
    async with await asyncio.start_server(handle, "0.0.0.0", 9334) as srv:
        await srv.serve_forever()

asyncio.run(main())
