import sys, contextlib, http.server, importlib.util, json, pathlib, socketserver, tempfile, threading, unittest
import codex_reset_watch as crw
FIX = pathlib.Path(__file__).parent / "fixtures"
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/api/v1/status-tba'):
            body=(FIX/'status_scheduled_tba.json').read_bytes(); code=200
        elif self.path.startswith('/api/v1/status'):
            body=(FIX/'status_upcoming.json').read_bytes(); code=200
        elif self.path.startswith('/api/v1/resets'):
            body=(FIX/'resets.json').read_bytes(); code=200
        else: body=b'{}'; code=404
        self.send_response(code); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
    def log_message(self, *_): pass

class IntegrationTests(unittest.TestCase):
    def test_client_and_normalization_against_http_server(self):
        with socketserver.TCPServer(('127.0.0.1',0), Handler) as srv, tempfile.TemporaryDirectory() as td:
            th=threading.Thread(target=srv.serve_forever, daemon=True); th.start()
            cfg=dict(crw.DEFAULT_CONFIG); cfg['api_base']=f'http://127.0.0.1:{srv.server_address[1]}'; cfg['request_retries']=1
            import os
            old=os.environ.get('CRW_LOG_DIR'); os.environ['CRW_LOG_DIR']=td
            try:
                snap=crw.APIClient(cfg, crw.Logger(cfg)).snapshot()
            finally:
                if old is None: os.environ.pop('CRW_LOG_DIR',None)
                else: os.environ['CRW_LOG_DIR']=old
                srv.shutdown(); th.join(timeout=2)
            self.assertTrue(snap.status_ok); self.assertTrue(snap.resets_ok)
            self.assertEqual(snap.latest.event_id,'evt-53')
            self.assertIsNotNone(snap.upcoming)
            self.assertEqual(snap.upcoming.chance_percent,45)
    def test_client_keeps_scheduled_tba_signal(self):
        with socketserver.TCPServer(('127.0.0.1',0), Handler) as srv, tempfile.TemporaryDirectory() as td:
            th=threading.Thread(target=srv.serve_forever, daemon=True); th.start()
            cfg=dict(crw.DEFAULT_CONFIG)
            cfg['api_base']=f'http://127.0.0.1:{srv.server_address[1]}'
            cfg['status_path']='/api/v1/status-tba'
            cfg['request_retries']=1
            import os
            old=os.environ.get('CRW_LOG_DIR'); os.environ['CRW_LOG_DIR']=td
            try:
                snap=crw.APIClient(cfg, crw.Logger(cfg)).snapshot()
            finally:
                if old is None: os.environ.pop('CRW_LOG_DIR',None)
                else: os.environ['CRW_LOG_DIR']=old
                srv.shutdown(); th.join(timeout=2)
            self.assertTrue(snap.status_ok)
            self.assertIsNotNone(snap.upcoming)
            self.assertIsNone(snap.upcoming.timestamp)
            self.assertEqual(snap.upcoming.timing_kind, 'scheduled_tba')
            self.assertEqual(snap.upcoming.title, 'Banked reset scheduled')
            self.assertEqual(snap.upcoming.source_url, 'https://x.com/thsottiaux/status/2101352781219258527')

if __name__ == '__main__': unittest.main()
