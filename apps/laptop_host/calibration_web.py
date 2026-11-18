"""Local calibration dashboard. Control goes through SSH to Ultra96, then AES-GCM to glove."""
import argparse
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def request(packet, port=15604):
    with socket.create_connection(('127.0.0.1',port),timeout=7) as sock:
        sock.sendall(json.dumps(packet).encode()+b'\n')
        with sock.makefile('rb') as stream:
            line=stream.readline(16385)
        if not line or len(line)>16384:raise ValueError('Invalid control reply')
        return json.loads(line)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--control-port',type=int,default=15604)
    args=p.parse_args()
    page=Path(__file__).with_name('calibration.html').read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def reply(self,code,body,kind='application/json'):
            data=body if isinstance(body,bytes) else json.dumps(body).encode()
            self.send_response(code);self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):
            if self.path=='/':return self.reply(200,page,'text/html; charset=utf-8')
            if self.path!='/status':return self.reply(404,{'error':'Not found'})
            try:self.reply(200,request({'type':'STATUS'},args.control_port))
            except Exception as e:self.reply(503,{'error':str(e)})
        def do_POST(self):
            if self.headers.get('Origin')!=f'http://127.0.0.1:{args.port}':return self.reply(403,{'error':'Origin rejected'})
            if self.path!='/calibrate':return self.reply(404,{'error':'Not found'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=128:raise ValueError('Invalid request size')
                body=json.loads(self.rfile.read(size))
                d=body.get('device_id');action=body.get('action')
                if type(d) is not int or d not in (1,2) or action not in ('c','g'):raise ValueError('Invalid device/action')
                result=request(dict(type='CALIBRATE',device_id=d,action=action),args.control_port)
                self.reply(200,result)
            except Exception as e:self.reply(503,{'error':str(e)})
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Open http://127.0.0.1:{args.port} (keep relay and SSH running)',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
