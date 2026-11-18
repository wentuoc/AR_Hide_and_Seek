import asyncio
from pipeline import proxy
async def main():
    boards=await asyncio.start_server(lambda r,w:proxy(r,w,15602),'0.0.0.0',5500)
    phones=await asyncio.start_server(lambda r,w:proxy(r,w,15603),'0.0.0.0',5503)
    print('[RELAY] boards :5500; phones TLS :5503; SSH must be connected',flush=True)
    async with boards,phones:
        await asyncio.gather(boards.serve_forever(),phones.serve_forever())
if __name__=='__main__':
    try:asyncio.run(main())
    except KeyboardInterrupt:pass
