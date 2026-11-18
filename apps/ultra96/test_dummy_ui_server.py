"""TLS protocol tests using a temporary self-signed test certificate."""
import asyncio
import contextlib
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from dummy_ui_server import Server, read_json

class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem'),
                        '-days', '1', '-subj', '/CN=localhost'], check=True, capture_output=True)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(root/'cert.pem'), str(root/'key.pem'))
        self.client_tls = ssl.create_default_context(cafile=str(root/'cert.pem'))
        self.hub = Server('test-token')
        self.server = await asyncio.start_server(self.hub.handle, '127.0.0.1', 0, ssl=tls, limit=4096)
        self.port = self.server.sockets[0].getsockname()[1]
        self.writers = []

    async def asyncTearDown(self):
        for w in self.writers:
            w.close()
            with contextlib.suppress(Exception): await w.wait_closed()
        self.server.close()
        await self.server.wait_closed()
        self.tmp.cleanup()

    async def connect(self, device=1, token='test-token'):
        r,w = await asyncio.open_connection('127.0.0.1', self.port, ssl=self.client_tls, server_hostname='localhost')
        self.writers.append(w)
        w.write((json.dumps(dict(v=1,type='DUMMY_HELLO', device_id=device,token=token))+'\n').encode())
        await w.drain()
        return r,w

    async def ack(self, r, w, ok=True):
        p = await read_json(r)
        w.write((json.dumps(dict(v=1,type='DUMMY_ACK',session=p['session'],seq=p['seq'],
                                event_id=p['event_id'],ok=ok,message='test'))+'\n').encode())
        await w.drain()
        return p

    async def test_repeat_commands_and_device_routing(self):
        r,w = await self.connect()
        r2,w2 = await self.connect(2)
        await self.ack(r,w); await self.ack(r2,w2)
        a = self.hub.submit(1,'spawn'); b = self.hub.submit(1,'spawn')
        c = self.hub.submit(2,'rotation')
        events=[]
        while len(events)<2:
            p=await self.ack(r,w)
            if p['event_id']: events.append(p)
        self.assertEqual([p['event_id'] for p in events],[a,b])
        self.assertEqual([p['command'] for p in events],['spawn','spawn'])
        while True:
            p=await self.ack(r2,w2,False)
            if p['event_id']: break
        self.assertEqual((p['event_id'],p['command'],p['device_id']),(c,'rotate_h',2))

    async def test_reject_bad_auth(self):
        r,w=await self.connect(token='wrong')
        self.assertEqual(await asyncio.wait_for(r.readline(),2),b'')
        self.assertNotIn(1,self.hub.clients)

    async def test_offline_drop_and_no_replay(self):
        self.assertIsNone(self.hub.submit(1,'spawn'))
        r,w=await self.connect(); await self.ack(r,w)
        self.hub.submit(1,'paint')
        while True:
            p=await read_json(r)
            if p['event_id']: break
            w.write((json.dumps(dict(v=1,type='DUMMY_ACK',session=p['session'],seq=p['seq'],event_id=0,ok=True))+'\n').encode()); await w.drain()
        # Disconnect after delivery but before acknowledgement; never replay toggle.
        w.close(); await w.wait_closed()
        for _ in range(100):
            if 1 not in self.hub.clients: break
            await asyncio.sleep(.01)
        r2,w2=await self.connect()
        p2=await self.ack(r2,w2)
        self.assertEqual(p2['type'],'PING')
        self.assertNotEqual(p2['session'],p['session'])

    async def test_invalid_ack_closes_connection(self):
        r,w=await self.connect(); p=await read_json(r)
        w.write(b'{"v":1,"type":"DUMMY_ACK","session":"wrong","seq":0,"event_id":0,"ok":true}\n'); await w.drain()
        self.assertEqual(await asyncio.wait_for(r.readline(),2),b'')

if __name__=='__main__': unittest.main()
