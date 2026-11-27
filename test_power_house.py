"""Offline tests. All Tesla HTTP responses are fixtures."""
import json
import tempfile
import threading
import unittest
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import power_house as app
from tesla_session import KEY, LIST, OWNER, READ, REG, LOCAL_CALLBACK, Session, local_public_key

CAR = {'vin': '5YJ3E1EA0KF000001', 'state': 'online', 'display_name': 'Fixture'}
TOKENS = {'access_token': 'access-fixture', 'refresh_token': 'refresh-fixture', 'expires_in': 3600}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.s = Session(self.api, app.safe_error, Mock(), app.AUTH, app.BASE, app.ROOT)
        self.s.configure({'client_id':'fixture-client','secret':'fixture-secret'})

    def start(self):
        query = parse_qs(urlsplit(self.s.signin(False)).query)
        self.assertEqual(query['scope'], ['openid offline_access vehicle_device_data'])
        self.assertEqual(query['redirect_uri'], [LOCAL_CALLBACK])
        return {'state':query['state'], 'code':['fixture-code']}

    def login(self):
        callback = self.start()
        self.api.side_effect = [(200,TOKENS), (200,{'response':[CAR]})]
        self.s.callback(callback)
        self.assertEqual(self.s.checks[OWNER], 'Authorized')
        self.assertIn('1 authorized',self.s.checks[LIST])
        self.assertEqual(self.api.call_args_list[0].args[1]['redirect_uri'], LOCAL_CALLBACK)
        public = json.dumps(self.s.action({'op':'status'}))
        for secret in [TOKENS['access_token'], TOKENS['refresh_token']] + [CAR['vin'],'fixture-secret','fixture-code']:
            self.assertNotIn(secret,public)
        return next(iter(self.s.cars))

    def test_callback_and_replay(self):
        self.login()
        with self.assertRaises(ValueError):
            self.s.callback({'state':['replay']})
        self.assertEqual(self.api.call_count,2)

    def test_reentering_credentials_preserves_consent_and_vehicle(self):
        query = self.start()
        credentials = {'op':'configure','client_id':'fixture-client','secret':'fixture-secret'}
        self.s.action(credentials)
        self.api.side_effect = [(200,TOKENS), (200,{'response':[CAR]})]
        self.s.callback(query)
        self.s.action(credentials)
        self.assertEqual(self.s.checks[OWNER], 'Authorized')
        self.assertEqual(len(self.s.cars), 1)
        self.s.action(dict(credentials, secret='changed-secret'))
        self.assertFalse(self.s.refresh)
        self.assertFalse(self.s.cars)

    def test_wrong_duplicate_expired_states(self):
        query = self.start()
        for state in [['wrong'], query['state']*2, ['é']]:
            with self.assertRaises(ValueError):
                self.s.callback({'state':state})
        with patch('tesla_session.time.monotonic',return_value=self.s.pending[1]+1):
            with self.assertRaises(ValueError):
                self.s.callback(query)
        self.api.assert_not_called()

    def test_https_callback_exchange(self):
        self.s.domain = 'powerhouse.example'
        query = parse_qs(urlsplit(self.s.signin(True)).query)
        redirect = 'https://powerhouse.example/callback.html'
        self.assertEqual(query['redirect_uri'], [redirect])
        self.api.side_effect = [(200,TOKENS), (200,{'response':[]})]
        self.s.callback({'state':query['state'], 'code':['fixture-code']})
        self.assertEqual(self.api.call_args_list[0].args[1]['redirect_uri'], redirect)
        self.assertIn('No vehicles', self.s.checks[LIST])

    def test_unauthorized_read_refreshes_and_retries_once(self):
        key = self.login()
        self.api.side_effect = [(401,{}),(200,dict(TOKENS,access_token='new-access')),
                                (200,{'response':{'vehicle_state':{'odometer':0}}})]
        self.s.action({'op':'live','car':key})
        self.assertEqual(self.api.call_args.args[2], 'new-access')
        self.assertEqual(self.s.snapshot['values']['Odometer'], '0 mi')

    def test_denial(self):
        q = self.start()
        self.s.callback(dict(state=q['state'], error=['access_denied']))
        self.assertIn('access_denied',self.s.checks[OWNER])
        self.api.assert_not_called()

    def test_refresh_and_measurements(self):
        key = self.login()
        self.s.expires = 0
        self.api.side_effect = [(200,dict(TOKENS, refresh_token='rotated')),
             (200,{'response':{'charge_state':{'charger_power':0,'timestamp':1000}}})]
        result = self.s.action({'op':'cached','car':key})
        self.assertEqual(self.s.refresh,'rotated')
        self.assertEqual(result['snapshot']['values']['Charging power'],'0 kW')
        self.assertEqual(result['snapshot']['values']['Exterior temperature'],'Unavailable')
        self.assertTrue(self.api.call_args.args[0].endswith('/vehicle_data_cached'))

    def test_sleep_and_errors(self):
        key = self.login()
        self.s.cars[key]=dict(CAR,state='asleep')
        self.s.action({'op':'live','car':key})
        self.assertIn('online',self.s.checks[READ])
        self.assertEqual(self.api.call_count,2)
        self.api.side_effect = [(401,{}),(401,{'error':'login_required'})]
        self.s.action({'op':'cached','car':key})
        self.assertIn('login_required',self.s.checks[OWNER])
        self.assertFalse(self.s.refresh)

    def test_import_existing_public_key(self):
        with tempfile.TemporaryDirectory() as temp:
            public = local_public_key(Path(temp), 'fixture-passphrase')
            self.s.root = Path(temp) / 'unused'
            result = self.s.action(dict(op='import_key', domain='powerhouse.example', pem=public))
            self.assertEqual(result['public'].strip(), public.strip())
            self.assertFalse(self.s.root.exists())
            self.api.assert_not_called()

    def test_registration(self):
        import io
        with tempfile.TemporaryDirectory() as temp:
            self.s.root=Path(temp)
            args={'op':'key','password':'fixture-passphrase','domain':'example.com'}
            self.s.action(args)
            public=self.s.public
            self.assertIn('BEGIN PUBLIC KEY',public)
            self.assertEqual(local_public_key(Path(temp),args['password']),public)
            self.assertIn(b'ENCRYPTED PRIVATE KEY',(Path(temp)/'.power-house/private.pem').read_bytes())
            self.s.action({'op':'register'})
            self.api.assert_not_called()
            self.s.http.open.return_value=io.BytesIO(public.encode())
            self.api.side_effect=[(200,TOKENS),(200,{}),(200,{'response':{'public_key':public}})]
            self.s.action({'op':'register','confirm':'example.com'})
            self.assertIn('verified',self.s.checks[REG])
            self.assertEqual(self.s.checks[KEY],'Hosted key matches')
            self.assertEqual(self.api.call_args_list[1].args[1],{'domain':'example.com'})

    def test_original_partner_test(self):
        with patch.object(app,'request_json',side_effect=[(200,TOKENS),(200,{'response':[]})]):
            out=app.test_connection('fixture','secret')
        self.assertIn('Successful',out['auth'])
        self.assertIn('No authorized',out['vehicle'])


class LocalServerTests(unittest.TestCase):
    def test_fresh_start_has_production_callback_without_credentials(self):
        session = app.new_session()
        self.assertEqual(session.public.strip(), (app.ROOT / app.KEY_PATH[1:]).read_text().strip())
        self.assertFalse(session.client or session.secret or session.refresh)
        session.configure({'client_id':'fixture-client','secret':'fixture-secret'})
        url = session.action({'op':'signin','hosted':True})['authorize']
        self.assertEqual(parse_qs(urlsplit(url).query)['redirect_uri'],
                         ['https://teslaapp.raulselejan.workers.dev/callback.html'])

    def test_http_boundary(self):
        with app.HTTPServer(('127.0.0.1',0),app.Handler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                port=server.server_port
                def request(method,path,body=None,headers=None):
                    with closing(HTTPConnection('127.0.0.1',port)) as conn:
                        conn.request(method,path,body,headers or {})
                        response=conn.getresponse()
                        return response.status,response.read()
                self.assertIn(b'Sign in with Tesla',request('GET','/')[1])
                self.assertEqual(request('GET','/',headers={'Host':'example.com'})[0],403)
                self.assertEqual(request('GET','/oauth/callback?state=invalid')[0],400)
                self.assertEqual(request('POST','/action','{}')[0],403)
                headers={'Origin':f'http://127.0.0.1:{port}','X-Local-Nonce':app.NONCE,'Content-Type':'application/json'}
                self.assertIn('checks',json.loads(request('POST','/action','{"op":"status"}',headers)[1]))
            finally:
                server.shutdown()
                thread.join()
