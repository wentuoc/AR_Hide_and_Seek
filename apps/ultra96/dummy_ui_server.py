#!/usr/bin/env python3
"""Manual TLS UI events. Python 3.8+, standard library; no FPGA/sensors needed."""
import argparse
import asyncio
import contextlib
import json
import secrets
import ssl
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMANDS = {'spawn', 'finetune', 'paint', 'up', 'down', 'left', 'right',
            'front', 'back', 'rotate_h', 'rotate_v', 'select_hider',
            'pinch', 'fine_open', 'fine_close', 'paint_on', 'paint_off', 'resting'}
ALIASES = {'movement': 'up', 'rotation': 'rotate_h', 'move_up': 'up',
           'move_down': 'down', 'move_left': 'left', 'move_right': 'right',
           'move_front': 'front', 'move_back': 'back',
           'rotate_horizontal': 'rotate_h', 'rotate_vertical': 'rotate_v'}

async def read_json(reader, timeout=5):
    raw = await asyncio.wait_for(reader.readline(), timeout)
    if not raw or len(raw) > 4096:
        raise ValueError('Closed connection or oversized frame')
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError('Expected JSON object')
    return obj

class Server:
    def __init__(self, token):
        self.token = token
        self.clients = {}
        self.event_id = 0

    def submit(self, device, command):
        command = ALIASES.get(command, command)
        if command not in COMMANDS:
            raise ValueError('Unknown command: ' + command)
        queue = self.clients.get(device)
        if queue is None:
            print('[NOT SENT] Device {} is not connected; retry after connection.'.format(device), flush=True)
            return None
        if queue.full():
            print('[NOT SENT] Queue full; wait for phone ACK.', flush=True)
            return None
        self.event_id += 1
        queue.put_nowait((self.event_id, command))
        print('[QUEUED] device={} event={} command={}'.format(device, self.event_id, command), flush=True)
        return self.event_id

    async def handle(self, reader, writer):
        device, queue = None, None
        try:
            hello = await read_json(reader)
            if (hello.get('type') != 'DUMMY_HELLO' or hello.get('v') != 1 or
                not isinstance(hello.get('token'), str) or
                not secrets.compare_digest(hello['token'], self.token)):
                raise ValueError('Wrong client protocol or authentication failed')
            device = hello.get('device_id')
            if type(device) is not int or device not in (1, 2):
                raise ValueError('device_id must be 1 or 2')
            if device in self.clients:
                raise ValueError('Device already connected; use a different device ID')
            queue = asyncio.Queue(maxsize=16)
            self.clients[device] = queue
            session, seq = secrets.token_hex(16), 0
            print('[CONNECTED] device={}'.format(device), flush=True)
            while True:
                try:
                    event, command = await asyncio.wait_for(queue.get(), 0.5)
                except asyncio.TimeoutError:
                    event, command = 0, ''
                packet = dict(v=1, type='DUMMY_EVENT' if event else 'PING', simulated=True,
                              session=session, seq=seq, device_id=device,
                              event_id=event, command=command)
                writer.write((json.dumps(packet, separators=(',', ':')) + '\n').encode())
                await writer.drain()
                ack = await read_json(reader)
                if (ack.get('v') != 1 or ack.get('type') != 'DUMMY_ACK' or
                    ack.get('session') != session or type(ack.get('seq')) is not int or
                    ack['seq'] != seq or type(ack.get('event_id')) is not int or
                    ack['event_id'] != event or type(ack.get('ok')) is not bool):
                    raise ValueError('Invalid ACK')
                if event:
                    print('[{}] device={} event={} {}: {}'.format(
                        'APPLIED' if ack['ok'] else 'BLOCKED', device, event, command,
                        str(ack.get('message', ''))[:200]), flush=True)
                seq += 1
        except (Exception, asyncio.CancelledError) as exc:
            print('[CLOSED] device={} {}. Unacknowledged events are NOT replayed.'.format(device, exc), flush=True)
        finally:
            if queue is not None and self.clients.get(device) is queue:
                del self.clients[device]
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def console(self):
        print('Hider dummy: [1|2] command. help lists all commands; select_hider requests the role.')
        print('movement=up; rotation=rotate_h. Default device=1. status/help/quit.')
        # Linux/Ultra96 stdin integration, avoiding a blocking input thread on exit.
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        pipe, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
        try:
            while True:
                print('dummy> ', end='', flush=True)
                line = await reader.readline()
                if not line:
                    return
                parts = line.decode().strip().lower().split()
                if not parts:
                    continue
                if parts == ['quit']:
                    return
                if parts == ['status']:
                    print('Connected devices:', sorted(self.clients), flush=True)
                    continue
                if parts == ['help']:
                    print('Commands:', ', '.join(sorted(COMMANDS)), '\nExamples: spawn | 1 up | 2 paint')
                    continue
                try:
                    if len(parts) == 1:
                        device, command = 1, parts[0]
                    elif len(parts) == 2 and parts[0] in ('1', '2'):
                        device, command = int(parts[0]), parts[1]
                    else:
                        raise ValueError('Use: [1|2] command')
                    self.submit(device, command)
                except ValueError as exc:
                    print('[ERROR]', exc, flush=True)
        finally:
            pipe.close()

async def main(args):
    config = json.loads((ROOT / 'phone_server.json').read_text())
    token = config.get('token')
    if not isinstance(token, str) or not token:
        raise ValueError('Missing token in phone_server.json')
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(str(ROOT / 'phone_cert.pem'), str(ROOT / 'phone_key.pem'))
    hub = Server(token)
    server = await asyncio.start_server(hub.handle, args.bind, args.port, ssl=tls,
                                        ssl_handshake_timeout=5, limit=4096)
    async with server:
        print('[DUMMY TLS] {}:{}; no sensors or FPGA required'.format(args.bind, args.port), flush=True)
        await hub.console()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bind', default='127.0.0.1', help='Default: existing SSH tunnel')
    parser.add_argument('--port', type=int, default=5603)
    try:
        asyncio.run(main(parser.parse_args()))
    except KeyboardInterrupt:
        pass
