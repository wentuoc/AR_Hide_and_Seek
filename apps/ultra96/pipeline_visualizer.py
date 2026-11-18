"""Real FPGA -> TLS -> existing Unity button callbacks. Additive integration entry point.
Reuse existing model, bitstream, keys, certificates and calibrated sensor protocol.
"""
import argparse
import asyncio
import concurrent.futures
import json
import secrets
import ssl
import time
from pathlib import Path
import pipeline
import secure_transport as transport
from live_ai import LiveHub, FPGA, load_config, console

ROOT = Path(__file__).resolve().parent
LABELS = ['click', 'gun', 'paint', 'pinch', 'resting', 'shoot', 'spawn']

class VisualizerHub(LiveHub):
    def __init__(self, keys, config, output, audit=None):
        super().__init__(keys, config, False, output, audit)
        self.sample_times = {}
        self.result_times = {}
        self.result_numbers = {}
        self.result_number = 0
        self.input_epochs = {1: 0, 2: 0}

    def sensor_open(self, device):
        super().sensor_open(device)
        self.input_epochs[device] += 1
        self.latest_samples.pop(device, None)
        self.sample_times.pop(device, None)
        self.result_times.pop(device, None)

    def sensor_close(self, device):
        super().sensor_close(device)
        self.input_epochs[device] += 1
        self.sample_times.pop(device, None)
        self.result_times.pop(device, None)

    def calibration_status(self, packet):
        device = packet['device_id']
        changed = self.calibration.get(device) != packet['state']
        super().calibration_status(packet)
        if changed:
            self.input_epochs[device] += 1
            self.sample_times.pop(device, None)
            self.result_times.pop(device, None)

    def sensor_sample(self, packet):
        device = packet['device_id']
        window = self.windows.get(device)
        resets = window.resets if window else -1
        super().sensor_sample(packet)
        if window is not None and window.resets != resets:
            self.input_epochs[device] += 1
            self.result_times.pop(device, None)
        if self.calibration.get(packet['device_id']) == 'READY':
            self.sample_times[packet['device_id']] = time.monotonic()

    def publish_result(self, result):
        device = result['device_id']
        self.result_number += 1
        self.result_numbers[device] = self.result_number
        self.result_times[device] = time.monotonic()

    def snapshot_for_phone(self, device, now=None):
        now = time.monotonic() if now is None else now
        sample = self.latest_samples.get(device)
        result = self.latest_results.get(device)
        online = device in self.boards
        ready = self.calibration.get(device) == 'READY'
        fresh = bool(online and ready and sample and
                     now - self.sample_times.get(device, -1e20) <= 0.4)
        ai_valid = bool(fresh and not self.fault and result and
                        now - self.result_times.get(device, -1e20) <= 1.5)
        # A just-completed FPGA operation can still describe an old window.
        # Bound age relative to the newest sensor sample, including uint32 wrap.
        if ai_valid:
            lag = (sample['ts_ms'] - result['ts_ms_end']) & 0xffffffff
            ai_valid = (lag <= 500 and result['generation'] == self.generation[device]
                        and result['boot_id'] == sample['boot_id']
                        and type(result['class_id']) is int and 0 <= result['class_id'] < len(LABELS))
        packet = dict(v=2, input_epoch=self.input_epochs[device], type='VISUALIZER_STATE', device_id=device,
                      online=online, ready=ready, fresh=fresh,
                      ai_valid=ai_valid, simulated=False,
                      generation=self.generation.get(device, 0),
                      event_id=self.result_numbers.get(device, 0),
                      class_id=int(result['class_id']) if ai_valid else -1,
                      gesture=LABELS[int(result['class_id'])] if ai_valid else '',
                      ai_fault=str(self.fault or '')[:120])
        if fresh:
            packet.update(boot_id=sample['boot_id'], sample_seq=sample['sample_seq'],
                          ts_ms=sample['ts_ms'], **sample['imu'])
        return packet

    async def phone_client(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            hello = await pipeline.read(reader, 5)
            if (hello.get('v') != 2 or hello.get('type') != 'VISUALIZER_HELLO' or
                not isinstance(hello.get('token'), str) or
                not secrets.compare_digest(hello['token'], self.token)):
                raise ValueError('Authentication failed')
            device = hello.get('device_id')
            if type(device) is not int or device not in (1, 2):
                raise ValueError('Subscribe with device_id 1 or 2')
            # Never act on an inference that pre-dates this phone connection.
            event_floor = self.result_numbers.get(device, 0)
            session = secrets.token_hex(16)
            seq = 0
            pipeline.log('[VISUALIZER CONNECTED] device={}'.format(device))
            while True:
                start = time.monotonic()
                packet = dict(self.snapshot_for_phone(device), session=session, seq=seq)
                if packet['event_id'] <= event_floor:
                    packet.update(ai_valid=False, class_id=-1, gesture='')
                await transport.send(writer, packet)
                ack = await pipeline.read(reader, 2)
                if (ack.get('ok') is not True or ack.get('v') != 2 or ack.get('type') != 'UI_ACK' or
                    ack.get('session') != session or type(ack.get('seq')) is not int or
                    ack['seq'] != seq):
                    raise ValueError('Invalid UI_ACK')
                seq += 1
                if seq % 100 == 0:
                    pipeline.log('[VISUALIZER ACK] device={} frames={}'.format(device, seq))
                await asyncio.sleep(max(0, 0.05 - (time.monotonic() - start)))
        except Exception as error:
            pipeline.log('[VISUALIZER CLOSED] ' + str(error))
        finally:
            self.tasks.discard(task)
            await pipeline.close(writer)

async def serve(args):
    config = load_config(args.config)
    if config.get('labels') != LABELS:
        raise ValueError('Model labels must match click/gun/paint/pinch/resting/shoot/spawn in that order')
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(ROOT/'phone_cert.pem'), str(ROOT/'phone_key.pem'))
    # PYNQ device initialization needs the main thread's event loop.
    engine = FPGA(args.bitfile)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    logs = ROOT/'logs'
    logs.mkdir(exist_ok=True)
    stamp = str(time.time_ns())
    servers, tasks = [], []
    hub = None
    with (logs/('visualizer-'+stamp+'.jsonl')).open('x') as output:
        try:
            hub = VisualizerHub(transport.load_keys(), config, output, logs/('sensor-'+stamp+'.jsonl'))
            hub.token = json.loads((ROOT/'phone_server.json').read_text())['token']
            for handler, port, options in [
                (hub.board, 5602, {}), (hub.control, 5604, {}),
                (hub.phone_client, 5603, dict(ssl=ctx, ssl_handshake_timeout=5))]:
                servers.append(await asyncio.start_server(handler, '127.0.0.1', port,
                                                          limit=16384, **options))
            tasks = [asyncio.create_task(hub.inference_worker(engine, pool)),
                     asyncio.create_task(hub.report())]
            if not args.no_console:
                tasks.append(asyncio.create_task(console(hub)))
            print('[VISUALIZER] REAL FPGA; sensors=5602 phone TLS=5603 control=5604', flush=True)
            await asyncio.Future()
        finally:
            for server in servers:
                server.close()
                await server.wait_closed()
            if hub:
                tasks += list(hub.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            pool.shutdown(wait=True)
            if hub and hub.audit:
                hub.audit.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(ROOT/'model_config.json'))
    parser.add_argument('--bitfile', default=str(ROOT/'gesture_cnn.bit'))
    parser.add_argument('--no-console', action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
