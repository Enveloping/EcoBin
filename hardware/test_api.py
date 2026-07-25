# test_api.py - HTTP debug server for delivery/clean flow testing
import json as _json, logging, os, sys, tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format='%(asctime)s [api] %(message)s')
logger = logging.getLogger('test-api')

_data_dir = os.path.join(tempfile.gettempdir(), 'ecobin-test-api')
os.makedirs(_data_dir, exist_ok=True)

from edge_store import EdgeStore
from test_mode import MockUartLink
from work_manager import WorkManager

store = EdgeStore(os.path.join(_data_dir, 'edge.db'))
store.initialize()
uart = MockUartLink(edge_boot_id=99)
wm = WorkManager(store, uart, None, None)
uart.open()
logger.info('test-api ready: %s', _data_dir)

def _json_resp(h, code, body):
    h.send_response(code)
    h.send_header('Content-Type', 'application/json')
    h.end_headers()
    h.wfile.write(_json.dumps(body).encode())

def _read_body(h):
    n = int(h.headers.get('Content-Length', 0))
    return _json.loads(h.rfile.read(n)) if n else {}

def _now():
    import time
    return str(int(time.time()))

class ApiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info('%s %s', self.command, self.path)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == '/health':
            return _json_resp(self, 200, {'status': 'ok'})
        if p == '/delivery/status':
            slot = store.get_work_slot()
            return _json_resp(self, 200, slot or {'status': 'idle'})
        if p == '/events':
            evts = store.list_pending_events(limit=50)
            result = [{'uid': e['event_uid'], 'type': e['event_type'], 'state': e['state']} for e in evts]
            return _json_resp(self, 200, result)
        if p == '/faults':
            return _json_resp(self, 200, store.list_active_faults())
        return _json_resp(self, 404, {'error': 'not found'})

    def do_POST(self):
        p = urlparse(self.path).path
        body = _read_body(self)
        try:
            if p == '/boot':
                return self._boot()
            if p.startswith('/delivery/'):
                return self._delivery(p, body)
            if p.startswith('/clean/'):
                return self._clean(p, body)
            return _json_resp(self, 404, {})
        except Exception as e:
            logger.error('error: %s', e, exc_info=True)
            return _json_resp(self, 500, {'error': str(e)})

    def _boot(self):
        info = uart.handshake()
        uart.query_state()
        return _json_resp(self, 200, {'mcu_boot': info['mcu_boot_id'], 'seq': store.get_edge_event_sequence()})

    def _delivery(self, path, body):
        if path == '/delivery/start':
            uid = body.get('session_uid') or 's-'+_now()
            r = wm.start_delivery_session(uid, body.get('port_no', 1), body.get('price', 25000), body.get('bag', 'TEST'))
            return _json_resp(self, 200 if r['success'] else 409, r)
        if path == '/delivery/authorize-open':
            slot = store.get_work_slot()
            uid = slot['work_uid'] if slot else body.get('uid', '')
            r = wm.authorize_first_open(uid)
            return _json_resp(self, 200 if r['success'] else 409, r)
        if path == '/delivery/mcu-event':
            wm.handle_mcu_event({'message_name': body['event'], 'payload': body.get('payload', {})})
            return _json_resp(self, 200, {'handled': body['event']})
        if path == '/delivery/finalize':
            slot = store.get_work_slot()
            wm.finalize_delivery(slot['work_uid'] if slot else body.get('uid', ''))
            return _json_resp(self, 200, {'finalized': True})
        return _json_resp(self, 404, {})

    def _clean(self, path, body):
        if path == '/clean/start':
            uid = body.get('operation_uid') or 'c-'+_now()
            r = wm.start_clean_operation(uid, body.get('port_no', 1), body.get('old_bag', 'OLD'), body.get('new_bag', 'NEW'))
            return _json_resp(self, 200 if r['success'] else 409, r)
        if path == '/clean/authorize-unlock':
            slot = store.get_work_slot()
            uid = slot['work_uid'] if slot else body.get('uid', '')
            r = wm.authorize_clean_unlock(uid)
            return _json_resp(self, 200 if r['success'] else 409, r)
        if path == '/clean/mcu-event':
            wm.handle_mcu_event({'message_name': body['event'], 'payload': body.get('payload', {})})
            return _json_resp(self, 200, {'handled': body['event']})
        if path == '/clean/finalize':
            slot = store.get_work_slot()
            wm.finalize_clean(slot['work_uid'] if slot else body.get('uid', ''))
            return _json_resp(self, 200, {'finalized': True})
        return _json_resp(self, 404, {})

if __name__ == '__main__':
    host = os.getenv('ECOBIN_TEST_API_HOST', '127.0.0.1')
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8080
    srv = HTTPServer((host, port), ApiHandler)
    logger.info('test-api: http://%s:%d', host, port)
    logger.info('curl http://localhost:%d/health', port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
        logger.info('test-api stopped')
