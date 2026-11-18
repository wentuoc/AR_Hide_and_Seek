import argparse
import asyncio
import hashlib
import json
import secrets
import statistics
import time
from pathlib import Path
import pipeline as p

ROOT=Path(__file__).resolve().parent
# Local assessment connects directly here; in full pipeline mode this is an SSH-forwarded control port
CONTROL_PORT=15604

# Send one request over a short-lived control connection and return its server response
# The server performs board encryption and reply verification/this client does not load board keys
# These commands test whichever endpoint is running, so physical hardware must be verified separately
async def rpc(message):
    r,w=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',CONTROL_PORT),5)
    try:
        await p.s.send(w,message)
        answer=await p.read(r,20)
        # Propagate a server-side validation, timeout or busy failure
        if answer.get('type')=='PIPELINE_FAIL':raise RuntimeError(answer['reason'])
        return answer
    finally:await p.close(w)

# Read cumulative per-device counters; this wrapper discards the server response phone-status field
async def status():return (await rpc(dict(type='STATUS')))['devices']

# Wait for both IDs to be online. Monotonic time avoids changes in the wall clock
# Polling yields to the event loop; a timeout prevents waiting forever
async def ready(timeout=90):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=await status()
        if all(state[str(d)]['online'] for d in (1,2)):return state
        await asyncio.sleep(.5)
    raise TimeoutError('Both physical boards must be connected as IDs 1 and 2')

# Configure the two independent boards concurrently. A window bounds unacknowledged IMU messages.
async def config(interval,window):
    return await asyncio.gather(*(rpc(dict(type='CONFIG',device_id=d,interval_ms=interval,window=window)) for d in (1,2)))

# Randomly select a six-axis dummy data on every probe. The Hub sends MODIFY, checks the returned ax+1 and unchanged other axes, then returns PROBE_PASS 
async def probe(d):
    samples=json.loads((ROOT/'dummy_samples.json').read_text())
    return await rpc(dict(type='PROBE',device_id=d,imu=secrets.choice(samples)))

# Save a readable JSON result with a timestamp and random suffix to distinguish repeated runs
def save_report(name,data):
    directory=ROOT/'reports';directory.mkdir(exist_ok=True)
    path=directory/(name+'-'+time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)+'.json')
    path.write_text(json.dumps(data,indent=2),encoding='utf-8')
    print('[SAVED]',path)

# Measure counter differences over one uninterrupted dual-board window.
async def measure(seconds):
    before=await ready();started=time.monotonic();rtts={1:[],2:[]}
    progress=10
    while time.monotonic()-started<seconds:
        replies=await asyncio.gather(probe(1),probe(2))
        for d,x in zip((1,2),replies):rtts[d].append(x['rtt_ms'])
        current=await status()
        for d in ('1','2'):
            # A changed generation reveals a reconnect even if the board is online again at this poll.
            if not current[d]['online'] or current[d]['generation']!=before[d]['generation']:raise RuntimeError('Board '+d+' disconnected/reconnected within window')
        elapsed=time.monotonic()-started
        if elapsed>=progress:
            print('[PROGRESS] {:.0f}s IMU1={} IMU2={}'.format(elapsed,current['1']['imu']-before['1']['imu'],current['2']['imu']-before['2']['imu']))
            progress+=10
        # Pause between probes without sleeping beyond the requested remaining window
        await asyncio.sleep(min(2,max(0,seconds-(time.monotonic()-started))))
    after=await status();elapsed=time.monotonic()-started
    rows=[]
    for d in (1,2):
        a=after[str(d)];b=before[str(d)]
        #received application bytes, not an assumed packet size
        count=a['imu']-b['imu'];nbytes=a['bytes']-b['bytes'];gaps=a['gaps']-b['gaps']
        # Require unchanged session/error counters, no application sequence gaps and at least 50 IMUs per board
        if not a['online'] or a['generation']!=b['generation'] or count<50 or gaps or a['errors']!=b['errors']:raise RuntimeError('Device {} fails uninterrupted-window checks'.format(d))
        values=sorted(rtts[d])
        # Convert bytes to decimal kilobits (8/1000) and divide by actual elapsed seconds
        # These bytes are plaintext IMU JSON plus newline; crypto/TCP/Wi-Fi overhead is excluded
        row=dict(device=d,window_s=round(elapsed,3),imu=count,imu_pps=count/elapsed,
            application_imu_kbps=nbytes*8/elapsed/1000,seq_gaps=gaps,verified_results=len(values),
            command_rtt_mean_ms=statistics.mean(values),command_rtt_p95_ms=values[min(len(values)-1,int(.95*len(values)))])
        rows.append(row);p.log('[SUMMARY] '+json.dumps(row),d)
    return rows

# Compare fixed interval/window candidates, or run one requested configuration
async def speed(args):
    candidates=[(20,1),(10,4),(5,8),(1,16),(0,32)] if args.sweep else [(args.interval,args.window)]
    stages=[]
    try:
        for interval,window in candidates:
            print('[RATE] interval_ms={} window={} duration_s={}'.format(interval,window,args.seconds))
            try:
                # Allow two seconds for the new settings to settle before collecting the measurement baseline.
                await ready();await config(interval,window);await asyncio.sleep(2)
                rows=await measure(args.seconds)
                stages.append(dict(pass_check=True,interval_ms=interval,window=window,devices=rows,total_kbps=sum(x['application_imu_kbps'] for x in rows)))
            except Exception as e:
                # Keep a failed stage in the report and continue to the next candidate.
                print('[STAGE FAIL]',str(e));stages.append(dict(pass_check=False,interval_ms=interval,window=window,error=str(e)))
        # Select only passing stages. A final PASS does not mean every sweep stage passed.
        good=[x for x in stages if x['pass_check']]
        if not good:raise RuntimeError('No rate configuration passed')
        # Choose and reapply the highest combined application throughput among tested passing settings.
        best=max(good,key=lambda x:x['total_kbps'])
        await config(best['interval_ms'],best['window'])
        print('[BEST TESTED] interval_ms={} window={} IMU_kbps={:.3f}'.format(best['interval_ms'],best['window'],best['total_kbps']))
        print('[PASS] Dual-board timed run completed. Best of tested settings, NOT theoretical network maximum.')
    # Preserve completed/failed stages even if the sweep exits early. Check the full report for failures.
    finally:save_report('speed',dict(test='dual-board speed',stages=stages))

# Verify a binary file through board RAM chunk round trips. The board need not store a filesystem file.
async def file_test(args):
    data=Path(args.file).read_bytes()
    if not 0<len(data)<=1024*1024:raise ValueError('File must contain 1 byte to 1 MiB')
    fid=secrets.token_hex(8);returned=bytearray();start=time.monotonic()
    before=await status()
    # Despite this variable name, the stored value is the connection generation, not boot_id
    boot=before[str(args.device)]['generation']
    # Use sequential 128-byte chunks; Python slicing also handles a shorter final chunk.
    # The random file ID identifies this transfer and the offset identifies each chunk position.
    for offset in range(0,len(data),128):
        piece=data[offset:offset+128]
        result=await rpc(dict(type='FILE_CHUNK',device_id=args.device,file_id=fid,offset=offset,data=piece.hex()))
        # Check reply type, transfer ID, offset and exact bytes before appending returned data.
        if result.get('type')!='FILE_RESULT' or result['file_id']!=fid or result['offset']!=offset or bytes.fromhex(result['data'])!=piece:raise ValueError('Returned chunk does not match')
        returned.extend(bytes.fromhex(result['data']))
    after=await status()
    if after[str(args.device)]['generation']!=boot:raise ValueError('Session changed during file transfer')
    # Hash the reconstructed response independently of the source; matching digests confirm whole-file equality.
    expected=hashlib.sha256(data).hexdigest();actual=hashlib.sha256(returned).hexdigest()
    if expected!=actual:raise ValueError('SHA256 mismatch')
    out=Path(args.file).with_name(Path(args.file).name+'.received-'+fid)
    # Save only the bytes received back. Exclusive creation avoids overwriting an existing result file.
    with out.open('xb') as f:f.write(returned)
    report=dict(device=args.device,bytes=len(data),chunks=(len(data)+127)//128,elapsed_s=time.monotonic()-start,sha256_original=expected,sha256_returned=actual,output=str(out),
        imu_during={d:after[d]['imu']-before[d]['imu'] for d in ('1','2')})
    print('[SHA256 original]',expected);print('[SHA256 returned]',actual)
    print('[FILE PASS]',json.dumps(report));save_report('file',report)

# ignore this, no need
# # The operator performs the physical power/range action; the code observes network behaviour.
# # For a manual pipeline demo, this helper does not need to run.
# async def recovery(args):
#     target=str(args.device);other=str(3-args.device);record=dict(kind=args.kind,target=args.device)
#     # Keep the other board generation fixed throughout; save target boot/session values for recovery checks.
#     initial=await ready();other_generation=initial[other]['generation'];oldboot=initial[target]['boot'];oldgen=initial[target]['generation']
#     # For each phase, require incoming IMU traffic and verified command replies.
#     # Only the devices listed for this phase must answer probes; the target stays offline in the offline phase.
#     async def observe(label,seconds,devices,keep_target_offline=False):
#         before=await status();start=time.monotonic();verified={d:0 for d in devices}
#         while time.monotonic()-start<seconds:
#             for d in devices:
#                 await probe(int(d));verified[d]+=1
#             snap=await status()
#             if not snap[other]['online'] or snap[other]['generation']!=other_generation:raise RuntimeError('Other board disconnected/reconnected')
#             if keep_target_offline and snap[target]['online']:raise RuntimeError('Target returned before PLUG IN / RETURN prompt')
#             for d in devices:
#                 if not snap[d]['online'] or snap[d]['generation']!=before[d]['generation']:raise RuntimeError('Board changed in '+label)
#             await asyncio.sleep(1)
#         snap=await status()
#         row={d:dict(imu=snap[d]['imu']-before[d]['imu'],verified_results=verified[d]) for d in devices}
#         if any(v['imu']<1 or v['verified_results']<1 for v in row.values()):raise RuntimeError('No verified traffic in '+label)
#         print('[PASS]',label,json.dumps(row));record[label]=row
#     try:
#         # First establish that both boards work before asking the operator to disconnect one.
#         await observe('baseline',10,['1','2'])
#         print('[ACTION] '+('UNPLUG power from device '+target if args.kind=='power' else 'Carry device '+target+' out of router Wi-Fi range, keeping it powered')+'. Keep device '+other+' online. WAIT for return prompt.',flush=True)
#         # Give the operator up to 120 seconds to trigger a detectable disconnect while probing the other board.
#         deadline=time.monotonic()+120
#         while True:
#             snap=await status();await probe(int(other))
#             if snap[other]['generation']!=other_generation:raise RuntimeError('Wrong/other board disconnected')
#             if not snap[target]['online']:break
#             if time.monotonic()>deadline:raise TimeoutError('Target did not disconnect within 120s')
#             await asyncio.sleep(1)
#         print('[OFFLINE] target observed offline; keep it away/off')
#         # Keep the target disconnected for ten seconds and verify the unaffected board continues working.
#         await observe('offline',10,[other],True)
#         print('[ACTION] '+('PLUG IN device '+target if args.kind=='power' else 'Bring powered device '+target+' back into Wi-Fi range')+' NOW',flush=True)
#         start=time.monotonic()
#         # Allow up to 90 seconds after the return prompt for a new target connection.
#         while time.monotonic()-start<90:
#             snap=await status();await probe(int(other))
#             if snap[other]['generation']!=other_generation:raise RuntimeError('Other board session changed')
#             if snap[target]['online'] and snap[target]['generation']>oldgen:break
#             await asyncio.sleep(1)
#         else:raise TimeoutError('Target did not recover')
#         # Power reset must change boot_id; a range-only reconnect must retain it.
#         # These are consistency checks; observing the physical action is still part of the live demo.
#         if args.kind=='power' and snap[target]['boot']==oldboot:raise ValueError('No changed boot_id: power-reset evidence missing')
#         if args.kind=='range' and snap[target]['boot']!=oldboot:raise ValueError('Board rebooted: redo out-of-range test without power reset')
#         # This duration includes the operator response time; it is not a pure network recovery latency.
#         record['seconds_since_return_prompt']=time.monotonic()-start
#         # Verify both boards exchange data again for 15 seconds before setting the report success flag.
#         await observe('recovered',15,['1','2'])
#         record['pass_check']=True
#     except Exception as e:
#         record['pass_check']=False;record['error']=str(e);raise
#     # Save partial evidence on exit too. Only a report with pass_check=true is a completed success;
#     # an interrupted run may contain just the earlier phases.
#     finally:save_report('recovery',record)

# CLI entry points: status, speed, file, recovery and make-file.
# Example: py assess.py speed --interval 0 --window 32 --seconds 60
# Place the global --control-port option before the subcommand when overriding the endpoint.
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--control-port',type=int,default=15604)
    sub=ap.add_subparsers(dest='mode',required=True)
    sub.add_parser('status')
    sp=sub.add_parser('speed');sp.add_argument('--seconds',type=int,default=60);sp.add_argument('--interval',type=int,default=20);sp.add_argument('--window',type=int,default=8);sp.add_argument('--sweep',action='store_true')
    fp=sub.add_parser('file');fp.add_argument('--device',type=int,choices=[1,2],required=True);fp.add_argument('--file',required=True)
    # rp=sub.add_parser('recovery');rp.add_argument('--device',type=int,choices=[1,2],default=1);rp.add_argument('--kind',choices=['power','range'],required=True)
    mp=sub.add_parser('make-file');mp.add_argument('--file',default='demo_payload.bin');mp.add_argument('--bytes',type=int,default=4096)
    args=ap.parse_args();CONTROL_PORT=args.control_port
    try:
        if args.mode=='make-file':
            if not 1<=args.bytes<=1024*1024:ap.error('size 1..1048576')
            # Generate a binary test payload once; refuse to overwrite an existing file.
            with open(args.file,'xb') as f:f.write(secrets.token_bytes(args.bytes))
            print('[CREATED]',args.file)
        elif args.mode=='status':print(json.dumps(asyncio.run(status()),indent=2))
        elif args.mode=='speed':
            # Enforce the minimum one-minute assessment window at the command-line boundary.
            if args.seconds<60:ap.error('Assessment window must be at least 60 seconds')
            asyncio.run(speed(args))
        elif args.mode=='file':asyncio.run(file_test(args))
        # else:asyncio.run(recovery(args))
    # An interrupted test is incomplete; it must not be reported as a completed pass.
    except KeyboardInterrupt:print('[STOPPED: test incomplete]')
    except Exception as e:print('[FAIL]',type(e).__name__,str(e));raise SystemExit(1)
