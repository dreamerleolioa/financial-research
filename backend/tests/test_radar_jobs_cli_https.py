"""Real CLI/HTTPS transport against an isolated local mock, no production access."""
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer


def test_cli_round_trip_records_exact_date_and_enforces_lane_deadlines(tmp_path):
    cert,key=tmp_path/'cert.pem',tmp_path/'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),
        '-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'],
        capture_output=True,check=True,timeout=15)
    paths=[];advances=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,payload,status=200):
            body=json.dumps(payload).encode();self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def check(self):
            assert self.headers['Authorization']=='Bearer local-mock-token'
            paths.append(self.path)
        def do_GET(self):
            self.check()
            if self.path.endswith('/capabilities'):
                self.reply({'version':'radar-durable-v1','enabled':True});return
            assert self.path.endswith('/readiness?market=TW&run_date=2026-11-06')
            self.reply({'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06',
                'scoring':{'ready':True},'publication':{'status':'completed','output_complete':True,'input_manifest':{'matches_current_inputs':True}},
                'validation':{'signal_close':{'ready':True},'next_open':{'ready':True}}})
        def do_POST(self):
            self.check();body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            result={'job_id':7,'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06'}
            if self.path.endswith('/jobs'):
                assert body=={'market':'TW','run_date':'2026-11-06'};self.reply(result,202);return
            assert self.path.endswith('/jobs/7/advance') and body=={}
            advances.append(self.path)
            self.reply(result|{'advanced_count':1 if len(advances)==1 else 0,'runnable_now':False,'data_ready':len(advances)>1})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    output=tmp_path/'evidence'
    env=dict(os.environ,ZEABUR_BACKEND_URL=f'https://localhost:{server.server_port}',DAILY_RADAR_INTERNAL_TOKEN='local-mock-token',
        SSL_CERT_FILE=str(cert),NO_PROXY='localhost,127.0.0.1',HTTP_PROXY='',HTTPS_PROXY='',ALL_PROXY='')
    script=Path(__file__).parents[2]/'.github/scripts/run_daily_radar_jobs.py'
    try:
        result=subprocess.run([sys.executable,str(script),'--run-date','2026-11-06','--output-dir',str(output),
            '--require-lanes','publication,signal_close,next_open','--max-seconds','60'],env=env,capture_output=True,text=True,timeout=15)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    assert result.returncode==0,(result.stdout,result.stderr)
    evidence=json.loads((output/'jobs.json').read_text())
    assert evidence['readiness_deadline_passed'] is True and evidence['advanced_requests']==2
    assert len(advances)==2 and len(paths)==5
    assert 'local-mock-token' not in (output/'jobs.json').read_text()+result.stdout+result.stderr
