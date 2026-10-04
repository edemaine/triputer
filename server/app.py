"""Small local-network HTTP API and static launcher, with no extra dependencies."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

from engine.registry import CATALOG
from .preview import preview

STATIC = Path(__file__).parent / 'static'


def make_server(engine, host='0.0.0.0', port=3333, demo=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, status, body, content_type='application/json'):
            data = json.dumps(body).encode() if content_type == 'application/json' else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path == '/api/state':
                    return self.send(200, dict(**engine.call('state'), catalog=CATALOG, demo=demo))
                if path.startswith('/api/preview/'):
                    app = path.rsplit('/', 1)[1]
                    if app not in CATALOG:
                        return self.send(404, {'error': 'Unknown interaction'})
                    return self.send(200, preview(app))
                assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
                if path in assets:
                    name, mime = assets[path]
                    return self.send(200, (STATIC / name).read_bytes(), mime)
                self.send(404, {'error': 'Not found'})
            except Exception as error:
                self.send(503, {'error': str(error)})

        def do_POST(self):
            # Restrict browser control requests to this origin; no cross-site forms.
            origin = self.headers.get('Origin')
            if (origin and urlsplit(origin).netloc != self.headers.get('Host')) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.send(403, {'error': 'Cross-origin requests are not allowed'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send(415, {'error': 'Expected application/json'})
            command = urlsplit(self.path).path.removeprefix('/api/')
            if urlsplit(self.path).path != '/api/' + command or command not in ('start', 'stop', 'restart', 'tap'):
                return self.send(404, {'error': 'Not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    return self.send(413, {'error': 'Invalid request size'})
                self.connection.settimeout(5)
                values = json.loads(self.rfile.read(length))
                if not isinstance(values, dict):
                    raise ValueError('Expected an object')
                if command == 'start' and not isinstance(values.get('interaction'), str):
                    raise ValueError('Choose an interaction')
                result = engine.call(command, **values)
                self.send(200, result)
            except (ValueError, TypeError) as error:
                self.send(400, {'error': str(error)})
            except Exception as error:
                self.send(503, {'error': str(error)})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
