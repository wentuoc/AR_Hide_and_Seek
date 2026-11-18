import asyncio
import json
import secrets
import secure_transport as s

def crypto():
    key=secrets.token_bytes(32)
    tx=s.Cipher(key,1,'e2p');rx=s.Cipher(key,1,'e2p')
    packet=json.loads(tx.seal(b'{"device_id":1}'))
    bad=dict(packet);bad['tag']=('00' if packet['tag'][:2]!='00' else '01')+packet['tag'][2:]
    for cipher,frame in [(rx,bad),(s.Cipher(secrets.token_bytes(32),1,'e2p'),packet),
                         (s.Cipher(key,2,'e2p'),packet),(s.Cipher(key,1,'p2e'),packet)]:
        try:cipher.open(frame)
        except ValueError:pass
        else:raise AssertionError('Invalid authentication accepted')
    assert rx.open(packet)==b'{"device_id":1}'  # Invalid tag did not consume sequence.
    try:rx.open(packet)
    except ValueError:pass
    else:raise AssertionError('Replay accepted')
    k1=s.derive(key,1,'a'*64,'b'*64,'e2p')
    k2=s.derive(key,1,'a'*64,'c'*64,'e2p')
    assert k1!=k2
    try:s.Cipher(k2,1,'e2p').open(json.loads(s.Cipher(k1,1,'e2p').seal(b'hello')))
    except ValueError:pass
    else:raise AssertionError('Old-session ciphertext accepted')
    print('[PASS] Python AES-GCM tag/key/direction/ID/replay/new-session checks')

async def framing():
    r=asyncio.StreamReader(limit=2050)
    task=asyncio.create_task(s.read_packet(r))
    r.feed_data(b'{"n":');await asyncio.sleep(0)
    assert not task.done()
    r.feed_data(b'1}\n{"n":2}\n{"n":')
    assert await task=={'n':1}
    assert await s.read_packet(r)=={'n':2}
    third=asyncio.create_task(s.read_packet(r));await asyncio.sleep(0)
    assert not third.done()
    r.feed_data(b'3}\n');assert await third=={'n':3}
    r.feed_eof()
    broken=asyncio.StreamReader(limit=2050);broken.feed_data(b'{"n":4}');broken.feed_eof()
    try:await s.read_packet(broken)
    except ValueError:pass
    else:raise AssertionError('Incomplete tail accepted')
    long=asyncio.StreamReader(limit=2050);long.feed_data(b'x'*2051+b'\n')
    try:await s.read_packet(long)
    except ValueError:pass
    else:raise AssertionError('Overlong frame accepted')
    print('[PASS] Python fragmentation/combined frames/partial tail/overlong rejection')

if __name__=='__main__':crypto();asyncio.run(framing())
