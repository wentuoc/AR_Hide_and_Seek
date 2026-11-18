"""Real encrypted sensor ingestion, optional FPGA inference; no visualizer."""
import argparse
import asyncio
import concurrent.futures
import json
import math
import time
from pathlib import Path
import numpy as np
import pipeline
import secure_transport as transport
from sensor_windows import FEATURES, Windows, values

ROOT=Path(__file__).resolve().parent

def load_config(path):
    c=json.loads(Path(path).read_text())
    if c.get('confirmed') is not True:
        raise ValueError('AI owner must confirm model_config.json before inference. Use --capture-only meanwhile.')
    if len(c.get('feature_order',[]))!=11 or set(c['feature_order'])!=set(FEATURES):
        raise ValueError('feature_order must list each of the 11 sensor features once')
    for name in ('offset','divisor'):
        a=c.get(name)
        if not isinstance(a,list) or len(a)!=11 or any(type(v) not in (int,float) or not math.isfinite(v) for v in a):
            raise ValueError('Invalid '+name)
    if any(v<=0 for v in c['divisor']): raise ValueError('divisor must be positive')
    labels=c.get('labels')
    if labels is not None and (not isinstance(labels,list) or len(labels)!=8 or any(not isinstance(x,str) or not x for x in labels) or len(set(labels))!=8):
        raise ValueError('labels must be null or eight distinct names in model class-index order')
    if 'gesture_ids' in c and c['gesture_ids'] != list(range(8)):
        raise ValueError('gesture_ids must be class indices 0 through 7')
    Windows(c['sample_period_ms'],c['tolerance_ms'],c['stride'])
    return c

def prepare(rows,c):
    x=np.asarray(rows,dtype=np.float32)
    if x.shape!=(20,11) or not np.isfinite(x).all():raise ValueError('Expected finite (20,11) window')
    x=x[:,[FEATURES.index(k) for k in c['feature_order']]]
    x=(x-np.asarray(c['offset'],dtype=np.float32))/np.asarray(c['divisor'],dtype=np.float32)
    # Fail visibly rather than silently saturating fixed-point input.
    q=np.round(x*1024)
    if not np.isfinite(q).all() or np.any(q < -32768) or np.any(q > 32767):
        raise ValueError('Normalized input exceeds ap_fixed<16,6>; verify training preprocessing')
    return x

async def control_send(writer,packet):
    raw=json.dumps(packet,separators=(',',':'),allow_nan=False).encode()+b'\n'
    if len(raw)>16384:raise ValueError('Control response too large')
    writer.write(raw)
    await asyncio.wait_for(writer.drain(),2)

class FPGA:
    def __init__(self,bitfile):
        import pynq_driver_2 as d
        from pynq import allocate
        if (d.T0, d.C0, d.NCLASS) != (20, 11, 8) or not getattr(d, "INPUT_PRENORMALIZED", False):
            raise ValueError("Use the supplied 8-class driver; live_ai owns normalization")
        self.d=d
        self.d.load_model()
        bit=Path(bitfile).resolve()
        if not bit.is_file() or not bit.with_suffix('.hwh').is_file():
            raise FileNotFoundError('Need matching .bit and .hwh: '+str(bit))
        d.BITFILE=str(bit)
        self.overlay,self.ip=d.load_overlay()
        self.input=allocate(shape=(220,),dtype=np.int16)
        self.output=allocate(shape=(d.NCLASS,),dtype=np.int16)
        d.set_buffer_address(self.ip,d.X_IN_REGS,self.input)
        d.set_buffer_address(self.ip,d.LOGITS_REGS,self.output)
    def infer(self,x):
        start=time.perf_counter()
        pred,logits=self.d.self_test(self.ip,self.input,self.output,x)
        if np.shape(logits) != (8,) or not np.isfinite(logits).all() or not 0 <= pred < 8:
            raise ValueError("Invalid 8-class FPGA output")
        # hp=self.d.read_hw_pred(self.ip)
        # if hp is not None and hp!=pred:raise RuntimeError('Hardware prediction register differs from logits argmax')
        return pred,logits.tolist(),round((time.perf_counter()-start)*1000,3)

class LiveHub(pipeline.Hub):
    def __init__(self,keys,config,capture,result_file,audit_file):
        super().__init__(keys,'',audit_file)
        self.config=config;self.capture=capture
        self.windows={}; self.pending_windows={}
        self.ready_devices=asyncio.Queue(maxsize=2)
        self.queued=set();self.replaced={1:0,2:0}
        self.result_file=result_file;self.fault=None
        self.calibration={};self.latest_samples={}
        self.inference_counts={1:0,2:0};self.latest_results={}
    def sensor_open(self,d):
        self.calibration[d]="WAIT_FLEX"
        self.latest_results.pop(d,None)
        self.windows[d]=Windows(self.config['sample_period_ms'],self.config['tolerance_ms'],self.config['stride'])
        self.pending_windows.pop(d,None)
    def sensor_close(self,d):
        self.calibration.pop(d,None);self.latest_samples.pop(d,None)
        self.windows.pop(d,None);self.pending_windows.pop(d,None)
        self.latest_results.pop(d,None)
    def calibration_status(self,p):
        d=p['device_id'];state=p['state'];old=self.calibration.get(d)
        self.calibration[d]=state
        if state!=old:
            self.windows[d]=Windows(self.config['sample_period_ms'],self.config['tolerance_ms'],self.config['stride'])
            self.pending_windows.pop(d,None);self.latest_results.pop(d,None);self.latest_samples.pop(d,None)
            pipeline.log('[CAL] device={} {}'.format(d,state),d)
    async def calibrate(self,d,action):
        if type(d) is not int or d not in (1,2) or action not in ('c','g'):raise ValueError('Use device 1/2 and c/g')
        result,_=await self.transact(d,'CALIBRATE',dict(action=action),dict(_type='CAL_RESULT'))
        self.calibration_status(result)
        return result
    def sensor_sample(self,p):
        if self.calibration.get(p['device_id'])!='READY':return
        self.latest_samples[p['device_id']]=p
        d=p['device_id'];w=self.windows[d]
        before=w.resets
        rows=w.add(p)
        if w.resets!=before:
            self.pending_windows.pop(d,None)
            self.latest_results.pop(d,None)
            pipeline.log('[WINDOW RESET] device={} sample={} resets={}'.format(d,p['sample_seq'],w.resets),d)
        if rows is None:return
        if self.capture:
            pipeline.log('[WINDOW READY] device={} shape=20x11 sample={} capture_only=true'.format(d,p['sample_seq']),d)
            return
        if self.fault:return
        job=(self.generation[d],w.resets,p['sample_seq'],p['ts_ms'],rows,w)
        if d in self.pending_windows:self.replaced[d]+=1
        self.pending_windows[d]=job
        if d not in self.queued:
            self.queued.add(d);self.ready_devices.put_nowait(d)
    async def control(self,r,w):
        # Assessment speed/config/keyboard operations must not alter a live model's cadence.
        try:
            p=await pipeline.read(r)
            if p.get('type')=='STATUS':
                await control_send(w,dict(type='STATUS',devices=self.snapshot(),capture_only=self.capture,
                    calibration=self.calibration,latest_samples=self.latest_samples,ai_fault=self.fault,replaced_windows=self.replaced,inferences=self.inference_counts,
                    windows={str(d):dict(samples=len(win.rows),resets=win.resets) for d,win in self.windows.items()},
                    latest_class_ids={str(d):r['class_id'] for d,r in self.latest_results.items()}))
            elif p.get('type')=='CALIBRATE':
                await control_send(w,await self.calibrate(p.get('device_id'),p.get('action')))
            else: await control_send(w,dict(type='ERROR',error='Use STATUS or CALIBRATE'))
        except Exception as e:
            await control_send(w,dict(type='ERROR',error=str(e)))
        finally:await pipeline.close(w)
    def publish_result(self,result):
        pass

    async def inference_worker(self,engine,pool):
        loop=asyncio.get_running_loop()
        while True:
            d=await self.ready_devices.get();self.queued.discard(d)
            job=self.pending_windows.pop(d,None)
            if job is None or self.fault:continue
            gen,resets,seq,ts,rows,source_window=job
            try:
                x=prepare(rows,self.config)
                pred,logits,ms=await loop.run_in_executor(pool,engine.infer,x)
                w=self.windows.get(d)
                if d not in self.boards or self.generation[d]!=gen or w is None or w.resets!=resets or w is not source_window:
                    pipeline.log('[STALE RESULT DROPPED] device={}'.format(d),d);continue
                result=dict(type='INFERENCE',device_id=d,boot_id=self.boards[d]['boot'],generation=gen,
                            sample_seq_end=seq,ts_ms_end=ts,class_id=pred,logits=logits,inference_ms=ms)
                result['input_source']='live_sensor'
                result['preprocessing_confirmed']=self.config.get('confirmed') is True
                if self.config.get('labels') is not None:result['gesture']=self.config['labels'][pred]
                line=json.dumps(result,allow_nan=False)
                self.result_file.write(line+'\n');self.result_file.flush()
                self.inference_counts[d]+=1;self.latest_results[d]=result
                pipeline.log('[AI] '+line,d)
                self.publish_result(result)
            except Exception as e:
                # A timed-out FPGA may still be writing its buffers. Never submit another job.
                self.fault='{}: {}'.format(type(e).__name__,e)
                self.pending_windows.clear()
                pipeline.log('[AI STOPPED] '+self.fault+'; fix cause and restart service')

async def console(hub):
    import sys
    if not sys.stdin.isatty():return
    loop=asyncio.get_running_loop();lines=asyncio.Queue()
    def readable():
        line=sys.stdin.readline()
        if line:lines.put_nowait(line.strip())
        else:loop.remove_reader(sys.stdin)
    loop.add_reader(sys.stdin,readable)
    print('Wireless calibration: c [device] = flex; g [device] = gyro; status. Default device 1.',flush=True)
    try:
        while True:
            parts=(await lines.get()).split()
            try:
                if parts and parts[0] in ('c','g') and len(parts)<=2:
                    print(await hub.calibrate(int(parts[1]) if len(parts)==2 else 1,parts[0]),flush=True)
                elif parts==['status']:print(hub.snapshot(),hub.calibration,flush=True)
                else:print('Use c, g, c 2, g 2, or status',flush=True)
            except Exception as e:print('[CAL ERROR]',e,flush=True)
    finally:loop.remove_reader(sys.stdin)

async def serve(args):
    config=(dict(sample_period_ms=50,tolerance_ms=15,stride=5) if args.capture_only else load_config(args.config))
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
    engine=None;hub=None;tasks=[];servers=[]
    logs=ROOT/'logs';logs.mkdir(exist_ok=True)
    stamp=time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns())
    with (logs/('inference-'+stamp+'.jsonl')).open('x') as output:
        try:
            if not args.capture_only:
                engine=FPGA(args.bitfile)
            hub=LiveHub(transport.load_keys(),config,args.capture_only,output,logs/('sensor-'+stamp+'.jsonl'))
            servers.append(await asyncio.start_server(hub.board,'127.0.0.1',5602,limit=2050))
            servers.append(await asyncio.start_server(hub.control,'127.0.0.1',5604,limit=2050))
            tasks.append(asyncio.create_task(hub.report()))
            if not args.no_console:tasks.append(asyncio.create_task(console(hub)))
            if engine:tasks.append(asyncio.create_task(hub.inference_worker(engine,pool)))
            print('[LIVE] board=:5602 control=:5604 capture_only={} no phone listener'.format(args.capture_only),flush=True)
            await asyncio.Future()
        finally:
            for server in servers:server.close();await server.wait_closed()
            if hub:tasks+=list(hub.tasks)
            for t in tasks:t.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            pool.shutdown(wait=True)
            if hub and hub.audit:hub.audit.close()

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--no-console',action='store_true')
    p.add_argument('--capture-only',action='store_true')
    p.add_argument('--config',default=str(ROOT/'model_config.json'))
    p.add_argument('--bitfile',default=str(ROOT/'gesture_cnn.bit'))
    args=p.parse_args()
    try:asyncio.run(serve(args))
    except KeyboardInterrupt:pass
if __name__=='__main__':main()
