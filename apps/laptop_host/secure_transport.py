"""Wire-compatible transport extracted from the previously verified secure_ultra_test.py. Python 3.9+."""
import asyncio
import argparse
import contextlib
import json
import math
import re
import secrets
import time

# Formal IMU fields shared by validation and six-axis RESULT comparison.
AXES = ('ax','ay','az','gx','gy','gz')
# Maximum application JSON size before the newline; encrypted envelopes have a separate limit.
MAX_FRAME = 1024

# Match the firmware unsigned 32-bit fields. Exact type excludes Python booleans, which subclass int.
def uint32(value):
    return type(value) is int and 0 <= value <= 0xFFFFFFFF

# Compact UTF-8 JSON plus newline defines one application message on the TCP byte stream.
# Reject NaN/infinity when encoding, as these are not valid finite sensor readings.
def encode(packet):
    raw = json.dumps(packet, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > MAX_FRAME:
        raise ValueError('Outgoing frame too long')
    return raw + b'\n'

# Validate the decrypted IMU/RESULT schema, device identity and finite sensor values.
# This function checks field format; the Hub separately checks boot consistency, sequence order
# and RESULT.reply_to against the outstanding command. Other packet types use Hub validation.
def validate(packet, device_id):
    if not isinstance(packet, dict):
        raise ValueError('Expected JSON object')
    if type(packet.get('v')) is not int or packet['v'] != 1:
        raise ValueError('Expected v=1')
    if packet.get('type') not in ('IMU', 'RESULT'):
        raise ValueError('Expected IMU or RESULT')
    if not uint32(packet.get('device_id')) or packet['device_id'] != device_id:
        raise ValueError('Wrong device_id')
    if not isinstance(packet.get('boot_id'), str) or not re.fullmatch(r'[0-9a-f]{8}', packet['boot_id']):
        raise ValueError('Invalid boot_id')
    if not uint32(packet.get('seq')) or not uint32(packet.get('ts_ms')):
        raise ValueError('Invalid sequence/timestamp')
    # reply_to belongs to the host command sequence; RESULT.seq belongs to the board sequence.
    if packet['type'] == 'RESULT' and not uint32(packet.get('reply_to')):
        raise ValueError('Invalid reply_to')
    imu = packet.get('imu')
    if not isinstance(imu, dict):
        raise ValueError('Missing IMU object')
    for axis in AXES:
        value = imu.get(axis)
        if type(value) not in (int, float) or abs(value) > 1000000 or not math.isfinite(value):
            raise ValueError('Invalid numeric field: ' + axis)

# Compare against the expected transformed sample only after validate() succeeds.
# The tolerance accommodates float serialization; the absolute comparison does not scale with magnitude.
def result_matches(packet, expected):
    # All six axes checked; floating-point serialization needs a small tolerance.
    return all(math.isclose(packet['imu'][k], expected[k], rel_tol=0.0, abs_tol=0.002)
               for k in AXES)

# StreamReader.readline() assembles partial TCP reads up to newline and retains later frames.
# The SecureReader adapter below provides the same interface after decryption.
# Callers configure stream buffer limits and wrap this read in a timeout.
async def read_packet(reader):
    raw = await reader.readline()
    if not raw:
        raise EOFError('connection closed')
    # An EOF-terminated partial JSON value must not count as a complete protocol frame.
    if len(raw)>MAX_FRAME+1 or not raw.endswith(b'\n'):
        raise ValueError('Frame too long/incomplete')
    p=json.loads(raw)
    if not isinstance(p,dict):raise ValueError('Expected object')
    return p

# Accept either a plain stream writer or SecureWriter. The adapter determines encryption.
# drain() provides flow control with a two-second wait; it is not an application-level ACK.
async def send(writer,p):
    writer.write(encode(p))
    await asyncio.wait_for(writer.drain(),2)

# Secure transport; the pc role never loads the key file or crypto dependency.
from pathlib import Path
import hashlib
import hmac
ROOT=Path(__file__).resolve().parent

# One-time provisioning of a separate random 256-bit pre-shared key (PSK) for each board.
# Refuse to replace existing keys because firmware and host must keep matching credentials.
# The permission mode restricts access on POSIX; Windows access control is platform-dependent.
def provision():
    path=ROOT/'secure_keys.json'
    if path.exists():raise ValueError('secure_keys.json already exists; reuse it')
    keys={str(d):secrets.token_hex(32) for d in (1,2)}
    with path.open('x') as f:json.dump(keys,f)
    path.chmod(0o600)
    print('[KEYS CREATED] Two separate keys; keep secure_keys.json private')

# Write the selected device ID and PSK as a C++ header for that board firmware build.
# This replaces src/crypto_key.h, so generate and upload each board separately.
def header(device):
    key=load_keys()[device]
    path=ROOT/'src'/'crypto_key.h';path.parent.mkdir(exist_ok=True)
    path.write_text('#pragma once\n#define SECURE_DEVICE_ID '+str(device)+'\nstatic const unsigned char TEST_PSK[32] = {'+','.join('0x%02x'%x for x in key)+'};\n')
    print('[HEADER READY] device={}; upload ONLY to this board'.format(device))

# Only cryptographic endpoints load the two PSKs. The PC relay role does not call this.
# In local assessment mode the PC is an endpoint and therefore does load them.
def load_keys():
    data=json.loads((ROOT/'secure_keys.json').read_text())
    out={d:bytes.fromhex(data[str(d)]) for d in (1,2)}
    if any(len(k)!=32 for k in out.values()) or out[1]==out[2]:raise ValueError('Need two distinct 32-byte keys')
    return out

# Derive a 32-byte directional session key with HMAC-SHA256.
# Context binds protocol, device ID, board challenge, host challenge and direction.
# Both peers must use identical text and ordering; fresh challenges produce new session keys.
# HELLO challenges are public. This PSK construction does not provide forward secrecy.
def derive(key,device,board,host,direction):
    text='secure-ultra-v1|device={}|{}|{}|{}'.format(device,board,host,direction)
    return hmac.new(key,text.encode(),hashlib.sha256).digest()

# One directional cryptographic context per session. Use separate instances/keys for send and receive.
# Cipher sequence numbers are independent of the application seq carried inside the encrypted JSON.
class Cipher:
    def __init__(self,key,device,direction):
        # Import AESGCM only when creating an endpoint cipher; opaque relay operation does not need it.
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        # AAD authenticates the protocol/device/direction context without encrypting those context bytes.
        self.aes=AESGCM(key);self.aad=('secure-ultra-v1|device={}|{}'.format(device,direction)).encode()
        # seq is the next send counter; last is the highest accepted receive counter for this session.
        self.seq=0;self.last=-1
    # Encrypt one plaintext document (without its application newline).
    # The 512-byte cap matches firmware buffers; reject before the send counter wraps.
    def seal(self,raw):
        if not 0<len(raw)<=512 or self.seq>=0xffffffff:raise ValueError('Cipher size/sequence limit')
        seq=self.seq;self.seq+=1
        # Nonce layout: eight zero bytes + a four-byte big-endian cipher sequence (96 bits total).
        # Security requires a unique nonce under each key: do not reset counters while reusing a session key.
        # AESGCM.encrypt returns ciphertext followed by its 16-byte authentication tag.
        ct=self.aes.encrypt(b'\0'*8+seq.to_bytes(4,'big'),raw,self.aad)
        # Send seq, hex ciphertext and hex tag as an outer JSON line.
        # Hex makes binary data JSON-compatible; it does not provide secrecy on its own.
        return json.dumps(dict(seq=seq,ct=ct[:-16].hex(),tag=ct[-16:].hex()),separators=(',',':')).encode()+b'\n'
    # Validate and authenticate an incoming encrypted envelope before returning any plaintext.
    def open(self,obj):
        from cryptography.exceptions import InvalidTag
        if not isinstance(obj,dict) or not uint32(obj.get('seq')):raise ValueError('Bad cipher envelope')
        seq=obj['seq'];ct=bytes.fromhex(obj['ct']);tag=bytes.fromhex(obj['tag'])
        if not 0<len(ct)<=512 or len(tag)!=16:raise ValueError('Cipher size limit')
        # Reconstruct the nonce and join ciphertext/tag as expected by the AESGCM API.
        # A wrong key, altered ciphertext/tag, or mismatched AAD makes authentication fail.
        try:raw=self.aes.decrypt(b'\0'*8+seq.to_bytes(4,'big'),ct+tag,self.aad)
        except InvalidTag:raise ValueError('GCM authentication failed') from None
        # Reject repeated/older authenticated counters. Strictly increasing is required, not contiguous.
        # Only update last after successful authentication, so invalid tags cannot consume receive sequence state.
        if seq<=self.last:raise ValueError('Replay rejected')
        self.last=seq
        return raw

# Read one newline-delimited wire envelope (or initial public HELLO used by the Hub).
# Hex ciphertext can be larger than plaintext, so this layer allows 2048 bytes plus newline.
# readline handles splitting/combining by TCP; the caller supplies stream limits and timeouts.
async def outer(reader):
    raw=await reader.readline()
    if not raw:raise EOFError('connection closed')
    if len(raw)>2049 or not raw.endswith(b'\n'):raise ValueError('Cipher frame too long')
    return json.loads(raw)

# Adapter: expose decrypted application lines to the existing read_packet() interface.
class SecureReader:
    def __init__(self,reader,cipher,device):self.reader=reader;self.cipher=cipher;self.device=device
    async def readline(self):
        # First assemble the wire frame, then verify/decrypt it, then check authenticated device identity.
        raw=self.cipher.open(await outer(self.reader))
        obj=json.loads(raw)
        if not isinstance(obj,dict) or obj.get('device_id')!=self.device:raise ValueError('Authenticated identity mismatch')
        # Record original plaintext byte length plus newline for application-throughput statistics.
        # This excludes the cipher envelope, TCP/IP headers and Wi-Fi overhead.
        self.last_size=len(raw)+1
        return raw+b'\n'

# Adapter: replace application serialization output with an encrypted outer frame.
# Encryption and counter allocation happen synchronously in write(), before any drain await.
# The current Hub uses a single event loop; this object does not add a thread-safety lock.
class SecureWriter:
    def __init__(self,writer,cipher):self.writer=writer;self.cipher=cipher
    # Remove the application delimiter before encryption; seal() adds the outer wire delimiter.
    def write(self,raw):self.writer.write(self.cipher.seal(raw.rstrip(b'\n')))
    # Delegate transport flow control and lifecycle operations to the underlying TCP stream.
    async def drain(self):await self.writer.drain()
    def close(self):self.writer.close()
    async def wait_closed(self):await self.writer.wait_closed()


