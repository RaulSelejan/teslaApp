#!/usr/bin/env python3
"""Power House: local, read-only Tesla Fleet API dashboard.
Run with Python 3. Key generation uses OpenSSL.
"""
import argparse
import json
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.error import HTTPError, URLError

from tesla_session import KEY, KEY_PATH, Session

AUTH = 'https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token'
BASE = 'https://fleet-api.prd.eu.vn.cloud.tesla.com'
NONCE = secrets.token_urlsafe(32)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


http = build_opener(NoRedirect)
ROOT = Path(__file__).resolve().parent
SESSION_LOCK = threading.Lock()
ASSETS = {
    '/dashboard/dashboard.css': 'text/css; charset=utf-8',
    '/dashboard/dashboard.js': 'text/javascript; charset=utf-8',
    '/dashboard/model-3.webp': 'image/webp',
}


def request_json(url, data=None, token=None, json_body=False):
    headers = {'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    if token:
        headers['Authorization'] = 'Bearer ' + token
    payload = None
    if data is not None:
        payload = urlencode(data).encode()
        if json_body:
            headers['Content-Type'] = 'application/json'
            payload = json.dumps(data).encode()
    req = Request(url, data=payload,
                  headers=headers, method='POST' if data is not None else 'GET')
    try:
        with http.open(req, timeout=20) as response:
            return response.status, json.loads(response.read())
    except HTTPError as err:
        try:
            return err.code, json.loads(err.read(65536))
        except (ValueError, OSError):
            return err.code, {}


def safe_error(body, *sensitive):
    if not isinstance(body, dict):
        return 'No structured error returned.'
    msg = ' '.join(str(body.get(k, '')) if isinstance(body.get(k), str) else '' for k in ('error', 'error_description', 'message')).strip()
    for value in sensitive:
        if value:
            msg = msg.replace(value, '[redacted]')
    msg = re.sub(r'\beyJ[A-Za-z0-9_.-]+', '[redacted token]', msg)
    msg = re.sub(r'\b[A-HJ-NPR-Z0-9]{17}\b', '[redacted VIN]', msg)
    return msg[:600] or 'No structured error returned.'


def test_connection(client_id, secret):
    result = {'auth': 'Not tested', 'api': 'Not tested', 'vehicle': 'Not tested', 'cars': [], 'detail': ''}
    try:
        code, body = request_json(AUTH, {'grant_type': 'client_credentials', 'client_id': client_id,
            'client_secret': secret, 'audience': BASE, 'scope': 'openid vehicle_device_data'})
        token = body.get('access_token') if isinstance(body, dict) else None
        if code != 200 or not isinstance(token, str) or not token:
            result.update(auth=f'Failed (HTTP {code})', detail=safe_error(body, client_id, secret))
            return result
        result['auth'] = 'Successful (partner token issued)'
        code, body = request_json(BASE + '/api/1/vehicles', token=token)
        if code != 200:
            result.update(api=f'Failed (HTTP {code})', detail=safe_error(body, client_id, secret, token))
            return result
        cars = body.get('response') if isinstance(body, dict) else None
        if not isinstance(cars, list):
            result.update(api='Unexpected response', detail='Tesla returned HTTP 200 but no vehicle list.')
            return result
        result['api'] = 'Successful (vehicle list returned)'
        result['vehicle'] = f'{len(cars)} vehicle(s) found' if cars else 'No authorized vehicles found'
        for car in cars:
            if isinstance(car, dict):
                vin = str(car.get('vin') or '')
                result['cars'].append({'name': car.get('display_name') or 'Tesla',
                    'state': car.get('state') or 'Unknown', 'vin': '••••' + vin[-4:] if vin else 'Unavailable'})
        result['detail'] = ('Vehicle access confirmed. No wake-up or commands sent.' if cars else
            'Credentials work, but this partner token cannot see any vehicles. Personal user OAuth authorization may be required.')
    except (URLError, TimeoutError, OSError, ValueError):
        result['detail'] = 'Network error, timeout, or invalid response. Check your internet connection and retry.'
    return result


class Handler(BaseHTTPRequestHandler):
    timeout = 10

    def log_message(self, *_):
        pass

    def reply(self, code, content, content_type):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)

    def valid_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, b'Forbidden', 'text/plain')
        try:
            path = urlsplit(self.path)
        except ValueError:
            return self.reply(400, b'Invalid path', 'text/plain')
        if path.path == '/oauth/callback':
            try:
                with SESSION_LOCK:
                    app.callback(parse_qs(path.query, keep_blank_values=True, max_num_fields=12))
            except ValueError:
                return self.reply(400, b'Invalid or expired callback. Start sign-in again.', 'text/plain')
            self.send_response(303)
            self.send_header('Location', '/')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return
        if self.path in ASSETS:
            asset = ROOT / self.path.lstrip('/')
            return self.reply(200, asset.read_bytes(), ASSETS[self.path])
        if self.path == '/favicon.ico':
            return self.reply(204, b'', 'image/x-icon')
        if self.path != '/':
            return self.reply(404, b'Not found', 'text/plain')
        page = (ROOT / 'power_house.html').read_text(encoding='utf8')
        self.reply(200, page.replace('__NONCE__', NONCE).encode(), 'text/html; charset=utf-8')

    def do_POST(self):
        origin = f'http://127.0.0.1:{self.server.server_port}'
        if (self.path not in ('/test', '/action') or not self.valid_host() or self.headers.get('Origin') != origin or
                self.headers.get('X-Local-Nonce') != NONCE or self.headers.get('Content-Type') != 'application/json'):
            return self.reply(403, b'Forbidden', 'text/plain')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            if self.path == '/action':
                with SESSION_LOCK:
                    payload = json.dumps(app.action(data), allow_nan=False).encode()
                return self.reply(200, payload, 'application/json')
            client_id, secret = data['client_id'], data['secret']
            if not all(isinstance(v, str) and 0 < len(v) <= 4096 for v in (client_id, secret)):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            return self.reply(400, b'Invalid credentials input', 'text/plain')
        result = test_connection(client_id, secret)
        self.reply(200, json.dumps(result).encode(), 'application/json; charset=utf-8')


def new_session():
    session = Session(request_json, safe_error, http, AUTH, BASE, ROOT)
    public_key = ROOT / KEY_PATH.lstrip('/')
    if public_key.is_file():
        session.domain = 'teslaapp.raulselejan.workers.dev'
        session.public = public_key.read_text(encoding='ascii')
        session.checks[KEY] = 'Deployment key loaded. Hosted copy unchecked this session.'
    return session


app = new_session()


class PowerHouseServer(ThreadingHTTPServer):
    allow_reuse_address = False
    allow_reuse_port = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-browser', action='store_true', help='Print the local URL without opening a browser')
    options = parser.parse_args()
    try:
        server = PowerHouseServer(('127.0.0.1', 8765), Handler)
    except OSError as err:
        import errno
        if err.errno != errno.EADDRINUSE and getattr(err, 'winerror', None) != 10048:
            raise
        parser.exit(1, 'Port 8765 is in use. If Power House is already running, open http://127.0.0.1:8765/\n')
    with server:
        url = f'http://127.0.0.1:{server.server_port}/'
        print(f'Power House is running at {url}\nOpen this URL if the browser does not open. Press Ctrl+C to stop.')
        if not options.no_browser:
            threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print('\nPower House stopped.')

if __name__ == '__main__':
    main()
