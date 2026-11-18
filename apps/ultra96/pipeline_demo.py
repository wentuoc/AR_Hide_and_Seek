"""Live sensor -> mock/FPGA -> authenticated TLS phone stream."""
import argparse
import asyncio
import concurrent.futures
import json
import random
import secrets
import ssl
import time
from pathlib import Path
import pipeline
import secure_transport as transport
from live_ai import LiveHub, FPGA, load_config, console

ROOT=Path(__file__).resolve().parent
# Temporary application IDs, NOT the five-class FPGA training labels.
GESTURES=['IDLE','SELECT','SPAWN_MECHA','FINE_TUNE','PAINT_MODE','PAINT','EXIT_PAINT','SHOOT']
gesture_mapping = {0: 3, 1: 0, 2: 4, 3: 1, 4: 7} ## new ##

class DemoHub(LiveHub):
    def __init__(self,keys,config,output,audit=None,real=False,interval=1.0,mode='cycle'):
        super().__init__(keys,config,not real,output,audit)
        self.real=real;self.interval=interval;self.mode=mode
        self.received={};self.emitted={};self.next_ids={};self.events={};self.event_number=0
        self.ui_acks=0
    def sensor_open(self,d):
        super().sensor_open(d)
        self.received.pop(d,None);self.events.pop(d,None);self.emitted.pop(d,None)
        self.next_ids[d]=1
    def sensor_close(self,d):
        super().sensor_close(d)
        self.received.pop(d,None);self.events.pop(d,None)
    def calibration_status(self,p):
        super().calibration_status(p)
        if p['state']!='READY':
            self.events.pop(p['device_id'],None);self.received.pop(p['device_id'],None)
    def sensor_sample(self,p):
        if self.real:
            super().sensor_sample(p)
        elif self.calibration.get(p['device_id'])=='READY':
            # Hub already validates and authenticates this packet before calling us.
            self.latest_samples[p['device_id']]=p
        if self.calibration.get(p['device_id'])=='READY':
            self.received[p['device_id']]=time.monotonic()
    def publish_result(self,result):
        d=result['device_id']
        if self.real:
            gid=self.config['gesture_ids'][result['class_id']]
            gid=gesture_mapping[gid] ## new ## 
        else:gid=result['class_id']
        self.event_number+=1
        event=dict(result,type='GESTURE',gesture_id=gid,gesture=GESTURES[gid],
                   simulated=not self.real,event_id=self.event_number)
        self.events[d]=(time.monotonic(),event)
    def mock_tick(self,now=None):
        now=time.monotonic() if now is None else now
        for d,received in list(self.received.items()):
            p=self.latest_samples.get(d)
            if d not in self.boards or not p or self.calibration.get(d)!='READY' or now-received>0.5:continue
            last_time,last_seq=self.emitted.get(d,(-1e20,None))
            if now-last_time<self.interval or last_seq==p['sample_seq']:continue
            gid=random.randint(1,7) if self.mode=='random' else self.next_ids.get(d,1)
            self.next_ids[d]=gid%7+1
            result=dict(type='INFERENCE',device_id=d,boot_id=self.boards[d]['boot'],generation=self.generation[d],
                sample_seq_end=p['sample_seq'],ts_ms_end=p['ts_ms'],class_id=gid,
                inference_ms=0.0,input_source='live_sensor',simulated=True)
            self.emitted[d]=(now,p['sample_seq']);self.inference_counts[d]+=1;self.latest_results[d]=result
            self.result_file.write(json.dumps(result)+'\n');self.result_file.flush()
            self.publish_result(result)
            pipeline.log('[MOCK AI] device={} sample={} gesture={} {}'.format(d,p['sample_seq'],gid,GESTURES[gid]),d)
    async def mock_worker(self):
        while True:
            self.mock_tick()
            await asyncio.sleep(0.02)
    async def phone_client(self,r,w):
        task=asyncio.current_task();self.tasks.add(task)
        try:
            hello=await pipeline.read(r,5)
            if hello.get('type')!='PHONE_HELLO' or not isinstance(hello.get('token'),str) or not secrets.compare_digest(hello['token'],self.token):
                raise ValueError('Authentication failed')
            session=secrets.token_hex(16);seq=0
            # New phone sessions start with future events, not old gameplay actions.
            seen={d:e[1]['event_id'] for d,e in self.events.items()}
            while True:
                now=time.monotonic()
                for d,(created,event) in list(self.events.items()):
                    if now-created>1.5 or now-self.received.get(d,-1e20)>0.5 or seen.get(d)==event['event_id']:continue
                    packet=dict(event,v=1,session=session,seq=seq)
                    await transport.send(w,packet)
                    ack=await pipeline.read(r,5)
                    self.check_ack(ack,session,seq)
                    seen[d]=event['event_id'];seq+=1;self.ui_acks+=1
                    self.record('phone_to_host',ack)
                    pipeline.log('[UI ACK] device={} event={} gesture={}'.format(d,event['event_id'],event['gesture_id']),d)
                states={str(d):dict(online=d in self.boards,ready=self.calibration.get(d)=='READY',
                         fresh=now-self.received.get(d,-1e20)<=0.5) for d in (1,2)}
                await transport.send(w,dict(v=1,type='PING',session=session,seq=seq,devices=states))
                self.check_ack(await pipeline.read(r,5),session,seq);seq+=1
                await asyncio.sleep(0.1)
        except Exception as e:pipeline.log('[PHONE CLOSED] '+str(e))
        finally:
            self.tasks.discard(task);await pipeline.close(w)
    @staticmethod
    def check_ack(ack,session,seq):
        if ack.get('v')!=1 or ack.get('type')!='UI_ACK' or ack.get('session')!=session or type(ack.get('seq')) is not int or ack['seq']!=seq:
            raise ValueError('Invalid UI_ACK')

async def serve(args):
    config=load_config(args.config) if args.real else dict(sample_period_ms=50,tolerance_ms=15,stride=5)
    if args.real:
        ids=config.get('gesture_ids')
        if not isinstance(ids,list) or len(ids)!=5 or any(type(i) is not int or i not in range(8) for i in ids):
            raise ValueError('Set gesture_ids: five app IDs in confirmed model class order')
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.minimum_version=ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(ROOT/'phone_cert.pem'),str(ROOT/'phone_key.pem'))
    logs=ROOT/'logs';logs.mkdir(exist_ok=True);stamp=str(time.time_ns())
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=1);servers=[];tasks=[];hub=None
    with (logs/('demo-'+stamp+'.jsonl')).open('x') as output:
        try:
            hub=DemoHub(transport.load_keys(),config,output,logs/('sensor-'+stamp+'.jsonl'),args.real,args.interval,args.mode)
            hub.token=json.loads((ROOT/'phone_server.json').read_text())['token']
            if args.real:
                engine=FPGA(args.bitfile)
                tasks.append(asyncio.create_task(hub.inference_worker(engine,pool)))
            else:tasks.append(asyncio.create_task(hub.mock_worker()))
            for handler,port,kw in [(hub.board,5602,{}),(hub.control,5604,{}),(hub.phone_client,5603,dict(ssl=ctx,ssl_handshake_timeout=5))]:
                servers.append(await asyncio.start_server(handler,'127.0.0.1',port,limit=16384,**kw))
            tasks.append(asyncio.create_task(hub.report()))
            if not args.no_console:tasks.append(asyncio.create_task(console(hub)))
            print('[DEMO] mode={} board=5602 TLS phone=5603 control=5604'.format('FPGA' if args.real else 'SIMULATED AI'),flush=True)
            await asyncio.Future()
        finally:
            for server in servers:server.close();await server.wait_closed()
            if hub:tasks+=list(hub.tasks)
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            pool.shutdown(wait=True)
            if hub and hub.audit:hub.audit.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--real',action='store_true');p.add_argument('--mode',choices=['cycle','random'],default='cycle')
    p.add_argument('--interval',type=float,default=1.0);p.add_argument('--no-console',action='store_true')
    p.add_argument('--config',default=str(ROOT/'model_config.json'));p.add_argument('--bitfile',default=str(ROOT/'gesture_cnn.bit'))
    args=p.parse_args()
    if not 0.1<=args.interval<=30:p.error('interval must be 0.1..30 seconds')
    try:asyncio.run(serve(args))
    except KeyboardInterrupt:pass
if __name__=='__main__':main()
