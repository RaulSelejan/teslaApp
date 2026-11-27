"""Local session for the EU Fleet API. All OAuth credentials live in memory."""
import base64
import os
import re
import secrets
import shutil
import ssl
import subprocess
import time
from urllib.error import URLError
from urllib.parse import urlencode

from dashboard_data import make_snapshot, reading_summary

LOCAL_CALLBACK = 'http://127.0.0.1:8765/oauth/callback'
KEY_PATH = '/.well-known/appspecific/com.tesla.3p.public-key.pem'
SCOPE = 'openid offline_access vehicle_device_data'
VIN = re.compile(r'[A-HJ-NPR-Z0-9]{17}')
DOMAIN = re.compile(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}')
STEPS = ('Developer credentials', 'Partner authentication', 'Public key availability',
         'EU application registration', 'Personal OAuth authorization', 'Vehicle discovery',
         'Vehicle data retrieval')
CREDS, PARTNER, KEY, REG, OWNER, LIST, READ = STEPS
HINTS = {
    CREDS: 'Enter the developer application Client ID and Client Secret.',
    PARTNER: 'Check the credentials and enable the machine-to-machine grant in the dashboard.',
    KEY: 'Use the same key passphrase and publish the displayed PEM at the exact HTTPS path.',
    REG: 'Verify the hosted key, allowed origin and partner credentials for this application.',
    OWNER: 'Check the authorization-code grant, exact redirect URI and Vehicle Information permission. Start sign-in again.',
    LIST: 'Check EU registration and sign in with the owner account that sees the car in the Tesla app.',
    READ: 'Try cached data, or request live data when the vehicle is online.',
}


class Problem(Exception):
    pass


class TeslaError(Problem):
    def __init__(self, status, message):
        super().__init__(f'HTTP {status} · {message}')
        self.status = status


def openssl(args, content):
    exe = shutil.which('openssl')
    if not exe and os.name == 'nt':
        exe = os.path.join(os.environ.get('ProgramFiles', ''), 'Git', 'usr', 'bin', 'openssl.exe')
    result = subprocess.run([exe or 'openssl', *args], input=content, capture_output=True, timeout=15)
    if result.returncode:
        raise Problem('OpenSSL could not open or generate the key. Check the passphrase and key file.')
    return result.stdout


def local_public_key(root, password):
    if not isinstance(password, str) or len(password) < 12 or any(c in password for c in '\r\n\0'):
        raise Problem('Enter a single-line local key passphrase of at least 12 characters.')
    directory = root / '.power-house'
    directory.mkdir(mode=0o700, exist_ok=True)
    key = directory / 'private.pem'
    pw = password.encode() + b'\n'
    if not key.exists():
        encrypted = openssl(['genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                             '-aes-256-cbc', '-pass', 'stdin'], pw)
        with os.fdopen(os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
            output.write(encrypted)
    return openssl(['pkey', '-in', str(key), '-passin', 'stdin', '-pubout'], pw).decode('ascii')


def import_public_key(pem):
    if not isinstance(pem, str) or not re.fullmatch(
        r'-----BEGIN PUBLIC KEY-----\s+[A-Za-z0-9+/=\s]+-----END PUBLIC KEY-----\s*', pem
    ):
        raise Problem('Choose the public-key.pem file exported by OpenSSL.')
    # X.509 SubjectPublicKeyInfo for id-ecPublicKey and prime256v1.
    der = public_bytes(pem)
    prefix = bytes.fromhex('3059301306072a8648ce3d020106082a8648ce3d03010703420004')
    if len(der) != 91 or not der.startswith(prefix):
        raise Problem('Expected an uncompressed prime256v1 public key.')
    openssl(['pkey', '-pubin', '-pubcheck', '-noout'], pem.encode('ascii'))
    return pem.strip() + '\n'


def public_bytes(pem):
    if not isinstance(pem, str) or not pem.strip().startswith('-----BEGIN PUBLIC KEY-----'):
        raise Problem('The response did not contain a PEM public key.')
    return base64.b64decode(''.join(pem.strip().splitlines()[1:-1]), validate=True)


class Session:
    def __init__(self, request, redact, http, auth, base, root):
        self.request, self.redact, self.http = request, redact, http
        self.auth, self.base, self.root = auth, base, root
        self.checks = dict.fromkeys(STEPS, 'Unchecked')
        self.client = self.secret = self.domain = self.public = ''
        self.partner = self.access = self.refresh = ''
        self.pending = None
        self.expires = 0
        self.cars = {}
        self.snapshot = None
        self.latest = {}
        self.history = {}

    def call(self, url, data=None, token=None, json_body=False):
        status, body = self.request(url, data, token, json_body)
        if not 200 <= status < 300:
            sensitive = [self.secret, self.partner, self.access, self.refresh, token]
            if data:
                sensitive.extend(data.get(k) for k in ('client_secret', 'code', 'refresh_token'))
            raise TeslaError(status, self.redact(body, *sensitive))
        if not isinstance(body, dict):
            raise Problem('Tesla returned an unexpected response format.')
        return body

    def forget_owner(self):
        self.access = self.refresh = ''
        self.pending = None
        self.cars.clear()
        self.snapshot = None
        self.latest.clear()
        self.history.clear()
        self.checks.update(dict.fromkeys((OWNER, LIST, READ), 'Unchecked'))

    def configure(self, d):
        cid, secret = d.get('client_id'), d.get('secret')
        if not all(isinstance(v, str) and 0 < len(v.strip()) <= 4096 for v in (cid, secret)):
            raise Problem('Both credential fields are required.')
        if (cid.strip(), secret.strip()) == (self.client, self.secret):
            return
        self.forget_owner()
        self.client, self.secret = cid.strip(), secret.strip()
        self.partner = ''
        self.checks[PARTNER] = self.checks[REG] = 'Unchecked'
        self.checks[CREDS] = 'Entered for this session'

    def partner_token(self):
        if not self.client:
            raise Problem('Enter developer credentials first.')
        response = self.call(self.auth, dict(grant_type='client_credentials', client_id=self.client,
                             client_secret=self.secret, audience=self.base, scope='openid vehicle_device_data'))
        self.partner = response.get('access_token')
        if not isinstance(self.partner, str) or not self.partner:
            raise Problem('Tesla did not issue a partner token.')
        self.checks[PARTNER] = 'Partner token issued'
        self.checks[CREDS] = 'Accepted by Tesla'
        return self.partner

    def hosted(self):
        if not self.domain or not self.public:
            raise Problem('Load the local public key and set its domain first.')
        url = 'https://' + self.domain + KEY_PATH
        try:
            with self.http.open(url, timeout=15) as r:
                hosted = r.read(8193)
        except URLError as err:
            if os.name != 'nt' or not isinstance(err.reason, ssl.SSLCertVerificationError):
                raise
            curl = os.path.join(os.environ['SystemRoot'], 'System32', 'curl.exe')
            result = subprocess.run(
                [curl, '--disable', '--silent', '--show-error', '--fail', '--max-filesize', '8192',
                 '--max-time', '15', '--write-out', '\\n%{http_code}', url],
                capture_output=True, timeout=17)
            if result.returncode:
                raise Problem(result.stderr.decode(errors='replace').strip() or 'Windows HTTPS check failed.')
            hosted, _, status = result.stdout.rpartition(b'\n')
            if status != b'200':
                raise Problem(f'Public key endpoint returned HTTP {status.decode(errors="replace")}.')
        if len(hosted) > 8192 or public_bytes(hosted.decode('ascii')) != public_bytes(self.public):
            raise Problem('Hosted key differs from the local public key.')
        self.checks[KEY] = 'Hosted key matches'

    def registration(self, create):
        if not self.domain or not self.public:
            raise Problem('Load the domain and local key first.')
        try:
            token = self.partner_token()
        except (Problem, OSError, ValueError) as err:
            self.failure(PARTNER, err)
            raise
        path = self.base + '/api/1/partner_accounts'
        if create:
            try:
                self.hosted()
            except (Problem, OSError, ValueError) as err:
                self.failure(KEY, err)
                raise
            self.call(path, {'domain': self.domain}, token, True)
            self.checks[REG] = 'Registration submitted'
        info = self.call(path + '/public_key?' + urlencode({'domain': self.domain}), token=token).get('response')
        value = info.get('public_key') if isinstance(info, dict) else None
        der = public_bytes(self.public)
        if isinstance(value, str) and value.startswith('-----BEGIN'):
            match = public_bytes(value) == der
        else:
            match = isinstance(value, str) and value.lower() in (der.hex(), der[-65:].hex())
        if not match:
            raise Problem('EU lookup did not return the expected public key.')
        self.checks[REG] = 'Registered key verified in Europe'

    def signin(self, hosted):
        if not self.client or (hosted and not self.domain):
            raise Problem('Enter credentials and configure the domain for HTTPS sign-in.')
        self.forget_owner()
        redirect = 'https://' + self.domain + '/callback.html' if hosted else LOCAL_CALLBACK
        state = secrets.token_urlsafe(32)
        self.pending = (state, time.monotonic() + 600, redirect)
        self.checks[OWNER] = 'Waiting for Tesla consent'
        return 'https://auth.tesla.com/oauth2/v3/authorize?' + urlencode(dict(client_id=self.client,
            redirect_uri=redirect, scope=SCOPE, response_type='code', state=state,
            prompt_missing_scopes='true', require_requested_scopes='true'))

    def tokens(self, data):
        body = self.call(self.auth, data)
        access, refresh, ttl = (body.get(k) for k in ('access_token','refresh_token','expires_in'))
        if not all(isinstance(v, str) and v for v in (access, refresh)) or not isinstance(ttl, (int,float)) or ttl <= 0:
            raise Problem('Tesla returned incomplete token information. Check offline access and sign in again.')
        self.access, self.refresh = access, refresh
        self.expires = time.monotonic() + ttl
        self.checks[OWNER] = 'Authorized'

    def callback(self, query):
        values = query.get('state', [])
        if (not self.pending or len(values) != 1 or not values[0].isascii() or
            not secrets.compare_digest(values[0], self.pending[0]) or time.monotonic() >= self.pending[1]):
            raise ValueError('Invalid state')
        redirect = self.pending[2]
        self.pending = None
        try:
            if 'error' in query:
                raise Problem(self.redact({k: v[0] for k,v in query.items() if k in ('error','error_description')},
                                          self.secret, *query.get('code', [])))
            codes = query.get('code', [])
            if len(codes) != 1 or not codes[0]:
                raise Problem('Tesla returned no unique authorization code.')
            self.tokens(dict(grant_type='authorization_code', client_id=self.client, client_secret=self.secret,
                             audience=self.base, redirect_uri=redirect, code=codes[0]))
        except (Problem, OSError, ValueError) as err:
            self.failure(OWNER, err)
            return
        self.action({'op':'discover'})

    def renew(self):
        try:
            self.tokens(dict(grant_type='refresh_token', client_id=self.client, refresh_token=self.refresh))
        except (Problem, OSError, ValueError) as err:
            if isinstance(err, TeslaError) and err.status in (400, 401):
                self.forget_owner()
            self.failure(OWNER, err)
            raise

    def read(self, path):
        if not self.refresh:
            raise Problem('Sign in with Tesla first.')
        if time.monotonic() + 60 >= self.expires:
            self.renew()
        try:
            body = self.call(self.base + path, token=self.access)
        except TeslaError as err:
            if err.status != 401:
                raise
            self.renew()
            body = self.call(self.base + path, token=self.access)
        return body.get('response')

    def discover(self):
        old_keys = {car['vin']: key for key, car in self.cars.items()}
        cars = self.read('/api/1/vehicles')
        if not isinstance(cars, list):
            raise Problem('Tesla returned no vehicle list.')
        discovered = {}
        for car in cars:
            if not isinstance(car, dict) or not VIN.fullmatch(str(car.get('vin', ''))):
                raise Problem('Tesla returned a vehicle without a valid VIN.')
            key = old_keys.get(car['vin']) or secrets.token_urlsafe(12)
            discovered[key] = car
        self.cars = discovered
        self.latest = {key: snap for key, snap in self.latest.items() if key in discovered}
        self.history = {key: reads for key, reads in self.history.items() if key in discovered}
        if self.snapshot and self.snapshot['car'] not in discovered:
            self.snapshot = None
            self.checks[READ] = 'Unchecked'
        self.checks[LIST] = f'{len(cars)} authorized vehicle(s)' if cars else 'No vehicles returned. ' + HINTS[LIST]

    def vehicle(self, key, cached):
        car = self.cars.get(key)
        if not car:
            raise Problem('Discover vehicles and select one first.')
        if not cached and car.get('state') != 'online':
            raise Problem('Last discovery did not report this vehicle online.')
        suffix = 'vehicle_data_cached' if cached else 'vehicle_data?endpoints=charge_state%3Bclimate_state%3Bvehicle_state%3Bvehicle_config%3Bgui_settings'
        data = self.read('/api/1/vehicles/' + car['vin'] + '/' + suffix)
        if not isinstance(data, dict) or not data:
            raise Problem('Tesla returned no vehicle data.')
        snapshot = make_snapshot(data, key, cached, time.time_ns() // 1_000_000)
        self.latest[key] = self.snapshot = snapshot
        history = self.history.setdefault(key, [])
        history.append(reading_summary(snapshot))
        del history[:-32]
        self.checks[READ] = 'Cached Tesla snapshot received' if cached else 'Live Tesla snapshot received'

    def failure(self, step, err):
        detail = str(err) if isinstance(err, (Problem, OSError)) else 'Invalid response encoding or format.'
        hint = HINTS[step]
        if isinstance(err, TeslaError):
            hint = {402:'Review billing and the spending limit in the Tesla dashboard.',
                    408:'Vehicle is unavailable. Read cached data or wait until it is online.',
                    429:'Tesla rate limit reached. Wait before trying again.'}.get(err.status, hint)
        self.checks[step] = f'Failed · {detail} {hint}'

    def action(self, d):
        op = d.get('op')
        steps = dict(configure=CREDS, partner=PARTNER, key=KEY, import_key=KEY, hosted=KEY, register=REG,
                     verify=REG, signin=OWNER, discover=LIST, cached=READ, live=READ)
        extra = {}
        try:
            if op == 'configure':
                self.configure(d)
            elif op == 'partner':
                self.partner_token()
            elif op in ('key', 'import_key'):
                domain = str(d.get('domain','')).strip().lower()
                if not DOMAIN.fullmatch(domain):
                    raise Problem('Enter the public hostname without a scheme, port or path.')
                if op == 'import_key':
                    public = import_public_key(d.get('pem'))
                else:
                    public = local_public_key(self.root, d.get('password'))
                if (domain, public) != (self.domain, self.public):
                    self.pending = None
                    self.checks[REG] = 'Unchecked'
                self.domain, self.public = domain, public
                self.checks[KEY] = 'Local key ready. Check the hosted copy.'
            elif op == 'hosted':
                self.hosted()
            elif op in ('verify', 'register'):
                if op == 'register' and (not self.domain or d.get('confirm') != self.domain):
                    raise Problem('Confirm the exact domain to submit registration.')
                self.registration(op == 'register')
            elif op == 'signin':
                extra['authorize'] = self.signin(d.get('hosted') is True)
            elif op == 'discover':
                self.discover()
            elif op in ('live','cached'):
                self.vehicle(d.get('car'), op == 'cached')
            elif op != 'status':
                raise ValueError('Unknown operation')
        except (Problem, OSError, ValueError, subprocess.SubprocessError) as err:
            if op not in steps:
                raise
            self.failure(steps[op], err)
        if self.pending and time.monotonic() >= self.pending[1]:
            self.pending = None
            self.checks[OWNER] = 'Sign-in expired. Start sign-in again.'
        cars = {k: dict(name=v.get('display_name') or 'Unnamed Tesla', state=v.get('state') or 'Unavailable',
                        vin='••••' + v['vin'][-4:]) for k,v in self.cars.items()}
        selected = d.get('car') or (self.snapshot['car'] if self.snapshot else None)
        return dict(checks=self.checks, cars=cars, snapshot=self.latest.get(selected),
                    history=self.history.get(selected, []), public=self.public,
                    domain=self.domain, key_path=KEY_PATH, **extra)
