import argparse
import asyncio
import contextlib
import json
import math
import secrets
import ssl
import hashlib
import datetime
import time
from pathlib import Path
import secure_transport as s

ROOT = Path(__file__).resolve().parent

# Use a distinct terminal colour for each board
def log(text, device=0):
    color = {1: '36', 2: '35'}.get(device, '32')
    print('\033[' + color + 'm' + text + '\033[0m', flush=True)

# Bound shutdown time: a broken peer must not hold up connection cleanup
async def close(w):
    if w:
        w.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(w.wait_closed(), 2)

# Read one complete JSON-line message using secure_transport framing
# The helper buffers TCP fragments and preserves subsequent messages; wait_for bounds the wait
async def read(r, timeout=10):
    return await asyncio.wait_for(s.read_packet(r), timeout)

# Server-side coordinator, used on Ultra96 or on the PC in local assessment mode
# All Hub coroutines share one asyncio event loop: waits yield to other connections
class Hub:
    def __init__(self, keys, token, log_path=None):
        self.keys, self.token = keys, token
        # Per-device connection state: encrypted writer, boot ID and one pending command
        # Each board has its own state, so a board disconnect is handled independently
        self.boards = {}
        self.phone = None
        self.command = 0
        self.tasks = set()
        self.totals = {d: [0, 0] for d in (1, 2)}
        # Increment generation on every accepted connection, even if boot_id is unchanged(for reconnection test)
        self.generation={1:0,2:0}
        self.gaps={1:0,2:0};self.errors={1:0,2:0}
        self.audit=open(log_path,'x',encoding='utf-8') if log_path else None

    def sensor_open(self, device): pass
    def sensor_close(self, device): pass
    def sensor_sample(self, packet): pass
    def calibration_status(self, packet): pass

    def record(self, direction, packet):
        # Decrypted, readable application data
        if self.audit:
            self.audit.write(json.dumps(dict(time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                direction=direction, protection='TLS' if 'phone' in direction else 'AES-GCM',
                auth_verified=direction in ('board_to_host','phone_to_host'), packet=packet),ensure_ascii=False)+'\n')
            self.audit.flush()

    # Return cumulative counters; assessment clients subtract snapshots over a timed window
    def snapshot(self):
        return {str(d):dict(online=d in self.boards,boot=self.boards.get(d,{}).get('boot'),
            generation=self.generation[d],imu=self.totals[d][0],bytes=self.totals[d][1],
            gaps=self.gaps[d],errors=self.errors[d]) for d in (1,2)}

    # Validate application headers after transport authentication
    def valid_board_packet(self,p,d):
        if p.get('type') in ('CAL_STATUS','CAL_RESULT'):
            states={'WAIT_FLEX','FLEX_COUNTDOWN','FLEX_RUNNING','WAIT_GYRO','GYRO_RUNNING','READY','SENSOR_ERROR'}
            if (type(p.get('v')) is not int or p['v']!=1 or type(p.get('device_id')) is not int or p['device_id']!=d
                or not s.uint32(p.get('seq')) or not s.uint32(p.get('ts_ms'))
                or not isinstance(p.get('boot_id'),str) or not s.re.fullmatch('[0-9a-f]{8}',p['boot_id'])
                or p.get('state') not in states): raise ValueError('Invalid calibration status')
            if p['type']=='CAL_RESULT' and (not s.uint32(p.get('reply_to')) or type(p.get('accepted')) is not bool):
                raise ValueError('Invalid calibration response')
            return
        if p.get('type') in ('IMU','RESULT'):s.validate(p,d);return
        if p.get('v')!=1 or p.get('type') not in ('CONFIG_RESULT','FILE_RESULT') or p.get('device_id')!=d or not s.uint32(p.get('seq')) or not s.uint32(p.get('reply_to')) or not s.uint32(p.get('ts_ms')):
            raise ValueError('Invalid auxiliary board response')

    async def board(self, r, w):
        """One receive task per board; disconnecting one never stops the other."""
        task = asyncio.current_task(); self.tasks.add(task)
        d = None; state = None
        try:
            # HELLO exchanges public random challenges; it is not itself authenticated
            # Only accept the board as online after a valid encrypted IMU frame arrives
            h = await asyncio.wait_for(s.outer(r), 8)
            d = h.get('device_id') if isinstance(h, dict) else None
            b = h.get('challenge') if isinstance(h, dict) else None
            if type(d) is not int or d not in (1, 2) or h.get('type') != 'HELLO' or not isinstance(b, str) or not s.re.fullmatch('[0-9a-f]{64}', b):
                raise ValueError('Bad HELLO: ensure secure firmware is uploaded')
            host = secrets.token_hex(32)
            await s.send(w, dict(type='HELLO', challenge=host))
            # Derive separate receive/send session keys from the board PSK and both challenges
            # Historical direction labels: e2p = board to host, p2e = host to board
            # secure_transport performs AES-GCM authentication and encrypted-sequence replay checks
            reader = s.SecureReader(r, s.Cipher(s.derive(self.keys[d], d, b, host, 'e2p'), d, 'e2p'), d)
            writer = s.SecureWriter(w, s.Cipher(s.derive(self.keys[d], d, b, host, 'p2e'), d, 'p2e'))
            p = await read(reader, 8)
            self.valid_board_packet(p, d)
            if p['type'] not in ('IMU','CAL_STATUS'): raise ValueError('Expected authenticated IMU or calibration status')
            if d in self.boards: raise ValueError('Duplicate device ID; wait for old connection timeout')
            state = dict(writer=writer, boot=p['boot_id'], pending=None)
            self.boards[d] = state
            self.generation[d]+=1
            self.sensor_open(d)
            log('[READY] device={} boot={}'.format(d, p['boot_id']), d)
            # Application sequence across IMU and all replies, distinct from cipher seq
            # A new connection starts a new local sequence baseline
            last_seq = -1
            while True:
                self.valid_board_packet(p, d)
                if p['boot_id'] != state['boot'] or p['seq'] <= last_seq:
                    raise ValueError('Boot/sequence mismatch')
                # Count missing application sequence values.
                if last_seq>=0:self.gaps[d]+=p['seq']-last_seq-1
                last_seq = p['seq']
                self.record('board_to_host',p)
                if p['type'] == 'IMU':
                    self.sensor_sample(p)
                    self.totals[d][0] += 1
                    # Count original decrypted IMU JSON plus newline, excluding crypto and network overhead.
                    self.totals[d][1] += reader.last_size
                    ack=dict(v=1,type='ACK',device_id=d,boot_id=state['boot'],ack_seq=p['seq'])
                    self.record('host_to_board',ack)
                    await s.send(writer,ack)
                elif p['type']=='CAL_STATUS':
                    self.calibration_status(p)
                    ack=dict(v=1,type='STATUS_ACK',device_id=d,boot_id=state['boot'],ack_seq=p['seq'])
                    await s.send(writer,ack)
                else:
                    # Match a reply to the outstanding request before completing its Future(six axis value)
                    pending = state['pending']
                    if pending is None: raise ValueError('Unsolicited RESULT')
                    seq, expected, future = pending
                    if '_type' in expected:
                        matches=all(p.get('type' if k=='_type' else k)==v for k,v in expected.items())
                    else:
                        matches=p['type']=='RESULT' and s.result_matches(p,expected)
                    if p['reply_to'] != seq or not matches:
                        raise ValueError('RESULT correlation/six-axis verification failed')
                    if not future.done(): future.set_result(p)
                    state['pending'] = None
                p = await read(reader, 8)
        except (Exception,) as e:
            if type(d) is int and d in self.errors:self.errors[d]+=1
            log('[BOARD CLOSED] device={} {}: {}'.format(d, type(e).__name__, e), d or 0)
        finally:
            # Remove only this connection state. Fail any waiter rather than silently replaying a command whose effects may already have happened before the disconnection
            if state is not None and self.boards.get(d) is state:
                self.sensor_close(d)
                del self.boards[d]
                pending = state['pending']
                if pending and not pending[2].done(): pending[2].set_exception(ConnectionError('Board disconnected; no automatic replay'))
            await close(w); self.tasks.discard(task)

    async def phone_client(self, r, w):
        """TLS is terminated on Ultra. A bounded queue isolates slow/offline UI."""
        task = asyncio.current_task(); self.tasks.add(task)
        state = None
        try:
            # The TLS handshake is completed by the server before this callback reads PHONE_HELLO.
            h = await read(r)
            if h.get('type') != 'PHONE_HELLO' or not isinstance(h.get('token'), str) or not secrets.compare_digest(h['token'], self.token):
                raise ValueError('Phone token rejected')
            if self.phone is not None: raise ValueError('Only one active visualizer allowed')
            # Bound the phone event backlog to 16 items. New submissions fail if the queue is full.
            # A random session ID prevents an ACK from an earlier connection matching this session.
            state = dict(queue=asyncio.Queue(maxsize=16), session=secrets.token_hex(16))
            self.phone = state
            log('[PHONE READY] TLS authenticated visualizer')
            seq = 0
            while True:
                item = None
                try: item = await asyncio.wait_for(state['queue'].get(), 2)
                except asyncio.TimeoutError: pass
                # Send a heartbeat when idle; a queued event changes the type to DUMMY_STATE.
                # One event/heartbeat at a time is sent and acknowledged on this phone connection.
                packet = dict(v=1, type='PING', session=state['session'], seq=seq)
                if item:
                    if item[1].cancelled():continue
                    packet.update(item[0])
                    self.record('host_to_phone',packet)
                await s.send(w, packet)
                # Require the current session and exact sequence. The Unity client sends its ACK
                # after main-thread state update; receipt is not proof a person viewed the screen
                ack = await read(r, 5)
                if ack.get('v') != 1 or ack.get('type') != 'UI_ACK' or ack.get('session') != state['session'] or type(ack.get('seq')) is not int or ack['seq'] != seq:
                    raise ValueError('Invalid UI ACK')
                if item:
                    self.record('phone_to_host',ack)
                    if not item[1].done(): item[1].set_result(True)
                    log('[UI ACK] device={} command={} gesture={} state={}'.format(packet['device_id'], packet['command_seq'], packet['gesture'], packet['state']), packet['device_id'])
                seq += 1
        except Exception as e:
            log('[PHONE CLOSED] {}: {}'.format(type(e).__name__, e))
        finally:
            if state is not None:
                if self.phone is state: self.phone = None
                if 'item' in locals() and item and not item[1].done(): item[1].set_exception(ConnectionError('Phone ACK missing'))
                # Fail queued callers on disconnect so they do not wait indefinitely for an offline UI
                while not state['queue'].empty():
                    _, fut = state['queue'].get_nowait()
                    if not fut.done(): fut.set_exception(ConnectionError('Phone disconnected'))
            await close(w); self.tasks.discard(task)

    async def transact(self,d,kind,fields,expected):
        """One outstanding command per device, registered before any network await."""
        board=self.boards.get(d)
        if not board:raise ValueError('Target board offline')
        # One outstanding control transaction per board. Overlapping assessment/keyboard requests can receive Target board busy; continuous IMU reception still runs.
        if board['pending']:raise ValueError('Target board busy')
        self.command+=1;seq=self.command
        if not s.uint32(seq):raise ValueError('Restart service: command counter exhausted')
        future=asyncio.get_running_loop().create_future()
        # Register the Future before the first await so a fast reply cannot arrive untracked
        board['pending']=(seq,expected,future)
        packet=dict(v=1,type=kind,device_id=d,boot_id=board['boot'],seq=seq,ts_ms=int(time.monotonic()*1000)&0xffffffff,**fields)
        try:
            self.record('host_to_board',packet)
            await s.send(board['writer'],packet)
            return await asyncio.wait_for(future,5),seq
        except BaseException:
            board['writer'].close();future.cancel();raise

    async def execute(self,p):
        """Validate control requests; only keyboard commands require a phone event."""
        kind=p.get('type')
        if kind=='STATUS':return dict(type='STATUS',devices=self.snapshot(),phone=self.phone is not None)
        d=p.get('device_id')
        if type(d) is not int or d not in (1,2):raise ValueError('Invalid device ID')
        # Assessment request: change the firmware send interval and outstanding IMU window.
        if kind=='CONFIG':
            interval=p.get('interval_ms');window=p.get('window')
            if type(interval)is not int or not 0<=interval<=1000 or type(window)is not int or not 1<=window<=32:raise ValueError('Invalid rate/window')
            fields=dict(interval_ms=interval,window=window)
            result,_=await self.transact(d,'CONFIG',fields,dict(_type='CONFIG_RESULT',**fields))
            return result
        # Send and verify one 1..128-byte file chunk with file ID and offset.
        # The assessment client handles whole-file reconstruction and SHA-256, not this Hub.
        if kind=='FILE_CHUNK':
            fid=p.get('file_id');data=p.get('data');offset=p.get('offset')
            if not isinstance(fid,str) or not s.re.fullmatch('[0-9a-f]{16}',fid) or not isinstance(data,str) or not s.re.fullmatch('(?:[0-9a-f]{2}){1,128}',data) or not s.uint32(offset):raise ValueError('Invalid file chunk')
            fields=dict(file_id=fid,offset=offset,data=data)
            result,_=await self.transact(d,'FILE_CHUNK',fields,dict(_type='FILE_RESULT',**fields))
            return result
        if kind not in ('KEY_COMMAND','PROBE'):raise ValueError('Invalid control type')
        imu=p.get('imu')
        if not isinstance(imu,dict) or set(imu)!=set(s.AXES) or any(type(v) not in (float,int) or not math.isfinite(v) or abs(v)>10000 for v in imu.values()):raise ValueError('Invalid six-axis dummy payload')
        if kind=='KEY_COMMAND' and not self.phone:raise ValueError('Phone offline: command not sent')
        # The demo transformation adds 1 to ax and leaves the other five axes unchanged.
        # A PROBE checks the same round trip without requiring or updating the phone.
        expected=dict(imu);expected['ax']+=1
        started=time.monotonic()
        result,seq=await self.transact(d,'MODIFY',dict(imu=imu),expected)
        if kind=='PROBE':return dict(type='PROBE_PASS',device_id=d,command_seq=seq,rtt_ms=round((time.monotonic()-started)*1000,2))
        log('[RESULT VERIFIED] device={} command={} ax={} -> {}'.format(d,seq,imu['ax'],result['imu']['ax']),d)
        # Generate a random dummy AI event only after the board RESULT has been verified.
        # This is a communication demo event, not an actual gesture-model inference.
        gesture,state=secrets.choice([('FIST','LOCKED'),('OPEN_PALM','FLOATING')])
        event=dict(type='DUMMY_STATE',device_id=d,command_seq=seq,gesture=gesture,state=state)
        phone=self.phone
        if not phone:raise ConnectionError('Board applied command, phone offline; no replay')
        ui=asyncio.get_running_loop().create_future()
        phone['queue'].put_nowait((event,ui))
        # PIPELINE_PASS requires the correlated phone ACK as well as the board result.
        # A timeout cancels this waiter; it does not undo an action already applied remotely.
        await asyncio.wait_for(ui,8)
        return dict(type='PIPELINE_PASS',device_id=d,command_seq=seq,gesture=gesture,state=state,elapsed_ms=round((time.monotonic()-started)*1000,1))

    async def control(self, r, w):
        # Bind loopback only; access is through the authenticated SSH forward.
        task = asyncio.current_task(); self.tasks.add(task)
        try:
            while True:
                p = await read(r, 3600)
                try: answer = await self.execute(p)
                except Exception as e: answer = dict(type='PIPELINE_FAIL', reason=type(e).__name__ + ': ' + str(e))
                await s.send(w, answer)
        except Exception: pass
        finally: await close(w); self.tasks.discard(task)

    # Report 10-second deltas to keep the terminal readable while logging individual packets.
    # File writes/flushes are synchronous and can contribute overhead to measured throughput.
    async def report(self):
        previous = {d: [0, 0] for d in (1, 2)}; start = time.monotonic()
        while True:
            await asyncio.sleep(10)
            if self.audit:self.audit.flush()
            now = time.monotonic(); elapsed = now-start; start=now
            for d in (1, 2):
                count, size = self.totals[d]; pc, pb = previous[d]
                log('[STATS] device={} online={} IMU_pps={:.2f} plaintext_IMU_kbps={:.3f}'.format(d, d in self.boards, (count-pc)/elapsed, (size-pb)*8/elapsed/1000), d)
                previous[d] = [count, size]

# ultra: loopback services reached through SSH; TLS terminates here for the phone.
# local: direct PC AES-GCM endpoint for dual-board tests; no phone listener is started.
async def ultra(local=False):
    config = dict(token='') if local else json.loads((ROOT/'phone_server.json').read_text())
    (ROOT/'logs').mkdir(exist_ok=True)
    log_path=ROOT/'logs'/('packets-'+time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)+'.jsonl')
    hub = Hub(s.load_keys(), config['token'],log_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if not local:ctx.load_cert_chain(ROOT/'phone_cert.pem', ROOT/'phone_key.pem')
    servers = []
    reporter = None
    try:
        # Ultra ports: 5602 boards, 5603 TLS phone, 5604 control.
        # Local test ports: 5500 boards on LAN, 15604 control on loopback.
        # The control protocol has no separate TLS/token; remote access relies on SSH forwarding.
        endpoints=[(hub.board,5500,{},'0.0.0.0'),(hub.control,15604,{},'127.0.0.1')] if local else [(hub.board,5602,{},'127.0.0.1'),(hub.control,5604,{},'127.0.0.1'),(hub.phone_client,5603,dict(ssl=ctx,ssl_handshake_timeout=8),'127.0.0.1')]
        for callback,port,options,host in endpoints:
            servers.append(await asyncio.start_server(callback,host,port,limit=2050,**options))
        log('[LOCAL] AES-GCM server :5500 control :15604' if local else '[ULTRA] boards=:5602 phone TLS=:5603 keyboard=:5604; persistent service')
        log('[LOG] '+str(log_path))
        # Connection callbacks run as independent tasks alongside periodic reporting.
        # Keep listeners alive until cancellation; the finally block closes services and tasks.
        reporter = asyncio.create_task(hub.report())
        await asyncio.Future()
    finally:
        for server in servers: server.close(); await server.wait_closed()
        tasks=list(hub.tasks)+([reporter] if reporter else [])
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if hub.audit:hub.audit.close()

# Opaque bidirectional byte relay to a local SSH-forwarded port.
# Forward arbitrary TCP chunks unchanged: this layer neither parses frames nor decrypts them.
async def proxy(r, w, port):
    remote = None; tasks = []
    try:
        rr, remote = await asyncio.wait_for(asyncio.open_connection('127.0.0.1',port),5)
        async def pump(src,dst):
            while True:
                data=await src.read(4096)
                if not data: return
                dst.write(data); await asyncio.wait_for(dst.drain(),5)
        # Run both directions concurrently. EOF/error in either direction closes the pair.
        tasks=[asyncio.create_task(pump(r,remote)),asyncio.create_task(pump(rr,w))]
        done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for t in done: t.result()
    except Exception as e: log('[RELAY] '+str(e))
    finally:
        for t in tasks:t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await close(w);await close(remote)

async def pc():
    import msvcrt  # Windows PowerShell console; each 1/2 key is one command, no Enter.
    active = None
    # Bound keyboard backlog; reject additional keys when full rather than growing forever.
    queue=asyncio.Queue(maxsize=128)
    async def command(d,imu):
        w=None
        try:
            r,w=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',15604),5)
            await s.send(w,dict(v=1,type='KEY_COMMAND',device_id=d,imu=imu))
            log('[KEY] device={} dummy={}'.format(d,imu),d)
            answer=await read(r,20)
            log('['+answer['type']+'] '+json.dumps(answer,ensure_ascii=False),d)
        except Exception as e: log('[CONTROL ERROR] '+str(e))
        finally: await close(w)
    # Serialize keyboard commands through one worker. While it awaits results,
    # keyboard input and all relay connections continue on the same event loop.
    async def worker():
        while True:
            d,imu=await queue.get()
            try:await command(d,imu)
            finally:queue.task_done()
    active=asyncio.create_task(worker())
    # LAN listeners: board 5500 -> SSH forward 15602; phone 5503 -> forward 15603.
    # Keyboard control uses forward 15604. Start-PC.ps1 creates these SSH forwards.
    a=await asyncio.start_server(lambda r,w:proxy(r,w,15602),'0.0.0.0',5500)
    try:
        b=await asyncio.start_server(lambda r,w:proxy(r,w,15603),'0.0.0.0',5503)
        async with a,b:
            log('[PC] opaque relays :5500 / :5503. Press 1 or 2; each key queues a random packet. q exits (finish pending commands first).')
            while True:
                if msvcrt.kbhit():
                    key=msvcrt.getwch()
                    if key=='q':break
                    if key in ('1','2'):
                        try:
                            # Reload at each keypress so edited dummy samples take effect without restart.
                            # Store the chosen sample in the queue, preserving the selection made for this key.
                            samples=json.loads((ROOT/'dummy_samples.json').read_text())
                            queue.put_nowait((int(key),secrets.choice(samples)))
                            log('[QUEUED] device={} waiting={}'.format(key,queue.qsize()),int(key))
                        except asyncio.QueueFull:log('[REJECTED] keyboard queue full (128); slow down')
                        except Exception as e:log('[SAMPLE ERROR] '+str(e))
                # Yield during keyboard polling so networking tasks are not starved.
                await asyncio.sleep(.03)
    finally:
        a.close();await a.wait_closed()
        if active:
            # Exiting cancels the worker; queued commands are not drained automatically.
            # Finish pending demo commands before pressing q.
            active.cancel();await asyncio.gather(active,return_exceptions=True)

def init_phone():
    """New phone credentials only; never regenerate or touch existing board PSKs."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    paths=['phone_cert.pem','phone_key.pem','phone_server.json','Assets/UltraPipeline/Resources/phone_config.json']
    if any((ROOT/p).exists() for p in paths):raise ValueError('Phone credentials exist; reuse all four files')
    # Create a self-signed TLS identity. The Unity client trusts its exact SHA-256
    # certificate pin and checks validity dates; it does not accept arbitrary certificates.
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,'ultra-visualizer')])
    now=datetime.datetime.utcnow()
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1))
          .not_valid_after(now+datetime.timedelta(days=365))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('ultra-visualizer')]),False).sign(key,hashes.SHA256()))
    # Generate a separate phone authentication token. Keep the private key server-side;
    # Unity receives only the certificate fingerprint and the token.
    token=secrets.token_hex(32)
    values=[cert.public_bytes(serialization.Encoding.PEM),key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()),
            json.dumps(dict(token=token)).encode(),json.dumps(dict(pin=cert.fingerprint(hashes.SHA256()).hex(),token=token)).encode()]
    for p,data in zip(paths,values):
        path=ROOT/p;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as f:f.write(data)
        path.chmod(0o600)
    print('[PHONE CONFIG CREATED] Copy Resources config to Unity; upload PEMs and phone_server.json only to Ultra.')

# CLI roles reuse the same server logic; pc only relays ciphertext and submits control.
# header delegates per-board firmware key-header generation to secure_transport.
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('role',choices=['ultra','local','pc','init-phone','header']);ap.add_argument('--device',type=int,choices=[1,2]);args=ap.parse_args()
    try:
        if args.role=='init-phone':init_phone()
        elif args.role=='header':
            if not args.device:ap.error('header needs --device 1 or 2')
            s.header(args.device)
        else:asyncio.run(ultra(args.role=='local') if args.role in ('ultra','local') else pc())
    except KeyboardInterrupt:print('[STOPPED]')
    except Exception as e:print('[FAIL]',type(e).__name__,str(e));raise SystemExit(1)
