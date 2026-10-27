"""Synthetic HTTP checks for the opt-in, identity-restricted Tailscale viewer."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis_pipeline as ap
import context_graph as cg
import live_dashboard as ld
from decisions_topics import map_fingerprint
from topic_display import display_revision


ORIGIN = 'https://viewer.example-tail.ts.net'
HOST = 'viewer.example-tail.ts.net'
USER = 'viewer@example.test'
SID = '87654321-4321-4321-8321-cba987654321'


class TailscaleConfigurationTests(unittest.TestCase):
    def test_valid_configuration_and_default_local_mode(self):
        self.assertEqual(ld.validate_tailscale_access(None, None), (None, None))
        self.assertEqual(ld.validate_tailscale_access(ORIGIN, USER), (ORIGIN, USER))

    def test_half_configuration_is_rejected(self):
        for origin, user in ((ORIGIN, None), (None, USER), ('', USER), (ORIGIN, '')):
            with self.subTest(origin=origin, user=user), self.assertRaises(ValueError):
                ld.validate_tailscale_access(origin, user)

    def test_origin_must_be_one_canonical_https_tailnet_origin(self):
        invalid = (
            'http://'+HOST, 'https://'+HOST+'/', 'https://'+HOST+':443',
            'https://'+HOST+'/path', 'https://'+HOST+'?token=synthetic',
            'https://'+HOST+'#fragment', 'https://user:secret@'+HOST,
            'https://*.example-tail.ts.net', 'https://'+HOST+'.invalid',
            'https://localhost', 'https://ts.net', 'https://'+HOST+' ',
            ORIGIN+',https://other.ts.net', 'https://'+HOST+'\n',
        )
        for origin in invalid:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                ld.validate_tailscale_access(origin, USER)

    def test_identity_cannot_be_ambiguous_or_inject_headers(self):
        for user in (' ', 'alice,bob', 'alice bob', 'alice\tbob', 'alice\nbob', 'alice\rbob', '사용자'):
            with self.subTest(user=user), self.assertRaises(ValueError):
                ld.validate_tailscale_access(ORIGIN, user)

    def test_bad_cli_configuration_is_private_and_precedes_database_access(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            invalid = [
                ['--tailscale-origin', ORIGIN],
                ['--tailscale-user', USER],
                ['--tailscale-origin', 'https://SYNTHETIC-SECRET.invalid', '--tailscale-user', USER],
                ['--tailscale-origin', ORIGIN, '--tailscale-user', 'SYNTHETIC SECRET USER'],
            ]
            for options in invalid:
                with self.subTest(options=options):
                    output = io.StringIO()
                    with patch.object(cg, 'connect', side_effect=AssertionError('DB access forbidden')) as connect, \
                         patch.object(cg.sqlite3, 'connect', side_effect=AssertionError('DB access forbidden')) as sqlite, \
                         redirect_stdout(output), redirect_stderr(output):
                        result = cg.main(['--db', str(root/'absent.db'), 'serve',
                                          '--live-dir', str(root/'live'), *options])
                    self.assertEqual(result, 2)
                    self.assertNotIn('SYNTHETIC', output.getvalue())
                    connect.assert_not_called()
                    sqlite.assert_not_called()
                    self.assertFalse((root/'absent.db').exists())
                    self.assertFalse((root/'live').exists())

    def test_constructor_rejects_bad_configuration_before_creating_files(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaises(ValueError):
                ld.LiveDashboard(root/'must-not-exist', root/'absent.db', 0,
                                 tailscale_origin=ORIGIN)
            self.assertEqual(list(root.iterdir()), [])


class TailscaleHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root/'codex'
        self.source = self.home/'sessions'/f'rollout-{SID}.jsonl'
        self.source.parent.mkdir(parents=True)
        events = [{'type': 'session_meta', 'payload': {'id': SID, 'cwd': 'C:/synthetic/Example'}}]
        for second, role, text in ((0, 'user', 'SYNTHETIC PRIVATE QUESTION'),
                                   (1, 'assistant', 'SYNTHETIC PRIVATE ANSWER')):
            events.append({'type': 'response_item', 'timestamp': f'2026-10-23T00:00:0{second}Z',
                           'payload': {'type': 'message', 'role': role,
                                       'content': [{'type': 'input_text' if role == 'user' else 'output_text',
                                                    'text': text}]}})
        self.source.write_text(''.join(json.dumps(event)+'\n' for event in events), encoding='utf-8')
        self.database = self.root/'collection.db'
        self.db = cg.connect(self.database)
        self.addCleanup(self.db.close)
        cg.scan(self.db, self.home)
        self.analysis = {'nodes': [
            {'id': 'root', 'kind': 'root'},
            {'id': 'q', 'kind': 'item', 'classification': 'answered_question', 'status': 'answered',
             'title': 'Synthetic question', 'evidence': [
                 {'messageId': 'm1', 'quote': 'SYNTHETIC PRIVATE QUESTION', 'verified': True}]},
            {'id': 'a', 'kind': 'item', 'classification': 'assistant_statement', 'status': 'none',
             'title': 'Synthetic answer', 'evidence': [
                 {'messageId': 'm2', 'quote': 'SYNTHETIC PRIVATE ANSWER', 'verified': True}]}],
            'relations': [{'id': 'answer-relation', 'from': 'a', 'to': 'q', 'type': 'answers'}],
            'source': {'messageCount': 2}}
        self.map_file = self.root/'analysis.json'
        self.map_file.write_text(json.dumps(self.analysis), encoding='utf-8')
        cg.import_map(self.db, SID, self.map_file, cg.digest(cg.serialize_transcript(cg.transcript(self.db, SID))))
        self.enterContext(patch.object(ap, 'auto_analyze', side_effect=AssertionError('Models forbidden')))
        self.enterContext(patch.object(cg.subprocess, 'run', side_effect=AssertionError('External processes forbidden')))
        self.live = self.dashboard('remote', tailscale_origin=ORIGIN, tailscale_user=USER)

    def dashboard(self, name, **kwargs):
        live = ld.LiveDashboard(self.root/name, self.database, 0, **kwargs)
        self.addCleanup(live.close)
        cg.watch_tick(self.db, self.home, dashboard=live)
        live.start()
        return live

    def request(self, method='GET', path='/status.json', headers=None, body=None, live=None):
        live = live or self.live
        connection = http.client.HTTPConnection('127.0.0.1', live._server.server_port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key, value in headers if headers is not None else self.authorized():
                connection.putheader(key, value)
            if body is not None:
                connection.putheader('Content-Length', str(len(body)))
                connection.putheader('Content-Type', 'application/json')
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def authorized(self, host=HOST):
        return [('Host', host), ('Tailscale-User-Login', USER)]

    def display_edit(self):
        self.analysis['nodes'].append({'id':'saved-topic','kind':'topic','title':'Original topic','childIds':['q','a']})
        self.map_file.write_text(json.dumps(self.analysis),encoding='utf-8')
        cg.import_map(self.db,SID,self.map_file,cg.digest(cg.serialize_transcript(cg.transcript(self.db,SID))))
        return {'session':SID,'topic':'saved-topic','group':'auxiliary',
                'sourceMapSha256':map_fingerprint(self.analysis),'revision':display_revision(None)}

    def test_same_origin_explicit_display_edit_persists_without_database_or_graph_changes(self):
        request=self.display_edit();before=self.database.read_bytes()
        headers=self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','topic-display')]
        status,_,body=self.request('POST','/topic-display.json',headers,json.dumps(request).encode())
        self.assertEqual(status,200);result=json.loads(body)
        self.assertTrue(result['saved']);self.assertEqual(result['auxiliaryTopicIds'],['saved-topic'])
        self.assertNotIn(b'SYNTHETIC PRIVATE',body)
        view=self.live.timeline.read(SID)
        self.assertEqual(next(n for n in view['analysis']['structure'] if n['id']=='saved-topic')['displayGroup'],'auxiliary')
        self.assertEqual(self.database.read_bytes(),before)
        request.update(group='primary',revision=result['topicDisplay']['revision'])
        self.assertEqual(self.request('POST','/topic-display.json',headers,json.dumps(request).encode())[0],200)
        self.assertEqual(self.database.read_bytes(),before)

    def test_display_mutation_requires_identity_origin_and_non_form_header(self):
        body=json.dumps(self.display_edit()).encode()
        invalid=[self.authorized(),self.authorized()+[('Origin',ORIGIN)],
                 self.authorized()+[('Origin','https://untrusted.test'),('X-Context-Graph-Action','topic-display')],
                 [('Host',HOST),('Origin',ORIGIN),('X-Context-Graph-Action','topic-display')],
                 self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','topic-display'),('X-Context-Graph-Action','topic-display')]]
        with patch.object(ld,'set_topic_display') as update:
            for headers in invalid:self.assertEqual(self.request('POST','/topic-display.json',headers,body)[0],403)
            update.assert_not_called()
        self.assertFalse(self.database.with_suffix('.topic-display.json').exists())

    def test_stale_display_edit_returns_conflict_without_echoing_source(self):
        request=self.display_edit();request['sourceMapSha256']='0'*64
        headers=self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','topic-display')]
        status,_,body=self.request('POST','/topic-display.json',headers,json.dumps(request).encode())
        self.assertEqual(status,409);self.assertNotIn(b'SYNTHETIC PRIVATE',body)
        self.assertFalse(self.database.with_suffix('.topic-display.json').exists())

    def test_name_edit_and_reset_keep_analysis_title_and_database_bytes(self):
        request=self.display_edit();request.pop('group');request['displayName']='새 표시 이름'
        before=self.database.read_bytes()
        headers=self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','topic-display')]
        status,_,body=self.request('POST','/topic-display.json',headers,json.dumps(request).encode())
        self.assertEqual(status,200);result=json.loads(body)
        self.assertEqual(result['operation'],'name');self.assertEqual(result['displayName'],'새 표시 이름')
        node=next(n for n in self.live.timeline.read(SID)['analysis']['structure'] if n['id']=='saved-topic')
        self.assertEqual(node['title'],'Original topic');self.assertEqual(node['displayTitle'],'새 표시 이름')
        self.assertEqual(self.database.read_bytes(),before)
        request.update(displayName=None,revision=result['topicDisplay']['revision'])
        self.assertEqual(self.request('POST','/topic-display.json',headers,json.dumps(request).encode())[0],200)
        node=next(n for n in self.live.timeline.read(SID)['analysis']['structure'] if n['id']=='saved-topic')
        self.assertNotIn('displayTitle',node);self.assertEqual(self.database.read_bytes(),before)

    def test_name_and_group_cannot_be_changed_by_one_ambiguous_request(self):
        request=self.display_edit();request['displayName']='Unexpected combined edit'
        headers=self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','topic-display')]
        self.assertEqual(self.request('POST','/topic-display.json',headers,json.dumps(request).encode())[0],400)
        self.assertFalse(self.database.with_suffix('.topic-display.json').exists())

    def test_session_name_edit_preserves_original_header_title_and_source_database(self):
        initial=self.live.timeline.read(SID)['session'];before=self.database.read_bytes()
        headers=self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','session-display')]
        request={'session':SID,'displayName':'Visible session name','revision':initial['sessionDisplay']['revision']}
        status,_,body=self.request('POST','/session-display.json',headers,json.dumps(request).encode())
        self.assertEqual(status,200);result=json.loads(body)
        self.assertNotIn(b'SYNTHETIC PRIVATE',body)
        header=self.live.timeline.read(SID)['session']
        self.assertEqual(header['title'],initial['title']);self.assertEqual(header['displayTitle'],'Visible session name')
        self.assertEqual(self.database.read_bytes(),before)
        request.update(displayName=None,revision=result['sessionDisplay']['revision'])
        self.assertEqual(self.request('POST','/session-display.json',headers,json.dumps(request).encode())[0],200)
        self.assertNotIn('displayTitle',self.live.timeline.read(SID)['session'])
        self.assertEqual(self.database.read_bytes(),before)

    def test_session_name_writes_require_the_same_origin_identity_and_matching_action(self):
        request={'session':SID,'displayName':'Visible name','revision':display_revision(None)}
        invalid=[self.authorized(),self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','topic-display')],
                 self.authorized()+[('Origin','https://untrusted.test'),('X-Context-Graph-Action','session-display')],
                 [('Host',HOST),('Origin',ORIGIN),('X-Context-Graph-Action','session-display')]]
        with patch.object(ld,'set_session_display') as update:
            for headers in invalid:self.assertEqual(self.request('POST','/session-display.json',headers,json.dumps(request).encode())[0],403)
            update.assert_not_called()
        headers=self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','session-display')]
        request['title']='Original title must never be writable'
        self.assertEqual(self.request('POST','/session-display.json',headers,json.dumps(request).encode())[0],400)
        self.assertFalse(self.database.with_suffix('.topic-display.json').exists())

    def test_item_title_http_edit_preserves_original_analysis_and_requires_its_own_action(self):
        before=self.database.read_bytes()
        request={'session':SID,'item':'q','displayTitle':'Visible question title',
                 'sourceMapSha256':map_fingerprint(self.analysis),'revision':display_revision(None)}
        headers=self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','item-display')]
        status,_,body=self.request('POST','/item-display.json',headers,json.dumps(request).encode())
        self.assertEqual(status,200);result=json.loads(body)
        self.assertEqual(result['itemLabels']['q']['title'],'Synthetic question')
        self.assertEqual(result['itemLabels']['q']['displayTitle'],'Visible question title')
        self.assertNotIn(b'SYNTHETIC PRIVATE',body);self.assertEqual(self.database.read_bytes(),before)
        saved=self.database.with_suffix('.topic-display.json').read_bytes()
        invalid=self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','topic-display')]
        self.assertEqual(self.request('POST','/item-display.json',invalid,json.dumps(request).encode())[0],403)
        self.assertEqual(self.database.with_suffix('.topic-display.json').read_bytes(),saved)
        request.update(displayTitle=None,revision=result['topicDisplay']['revision'])
        self.assertEqual(self.request('POST','/item-display.json',headers,json.dumps(request).encode())[0],200)
        self.assertEqual(self.database.read_bytes(),before)

    def test_evidence_title_http_route_checks_the_stored_quote_membership(self):
        self.analysis['nodes'][1]['evidence'].append({'messageId':'m2','quote':'SYNTHETIC PRIVATE ANSWER','verified':True})
        self.map_file.write_text(json.dumps(self.analysis),encoding='utf-8')
        cg.import_map(self.db,SID,self.map_file,cg.digest(cg.serialize_transcript(cg.transcript(self.db,SID))))
        view=self.live.timeline.read(SID)
        evidence=view['analysis']['assistantEvidence']['q'][0]
        request={'session':SID,'item':'q','displayTitle':'Visible evidence title','evidenceMessage':evidence['messageId'],
            'sourceMapSha256':map_fingerprint(self.analysis),'revision':display_revision(None)}
        headers=self.authorized()+[('Origin',ORIGIN),('X-Context-Graph-Action','item-display')]
        before=self.database.read_bytes()
        status,_,body=self.request('POST','/item-display.json',headers,json.dumps(request).encode())
        self.assertEqual(status,200);self.assertNotIn(b'SYNTHETIC PRIVATE',body)
        self.assertEqual(self.live.timeline.read(SID)['analysis']['assistantEvidence'],view['analysis']['assistantEvidence'])
        self.assertEqual(self.database.read_bytes(),before)

    def test_authorized_status_and_static_views_are_available(self):
        for path in ('/', '/index.html', '/graph.html', '/timeline.html', '/status.json'):
            with self.subTest(path=path):
                status, headers, payload = self.request(path=path)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(headers['X-Frame-Options'], 'SAMEORIGIN')
                self.assertTrue(payload)

    def test_authentication_is_required_before_every_route_reads_data(self):
        paths = ('/', '/graph.html', '/map.html', '/timeline.html', '/status.json',
                 '/timeline/sessions.json', '/timeline/messages.json?session='+SID, '/unknown')
        with patch.object(self.live.timeline, 'read') as read, \
             patch.object(self.live.timeline, 'search') as search, \
             patch.object(self.live, 'status') as status_reader, \
             patch.object(ld.Path, 'read_bytes') as asset_read:
            for path in paths:
                with self.subTest(path=path):
                    status, _, body = self.request(path=path, headers=[('Host', HOST)])
                    self.assertEqual(status, 403)
                    self.assertNotIn(b'SYNTHETIC PRIVATE', body)
            for path in ('/timeline/search.json', '/analyze', '/unknown'):
                with self.subTest(method='POST', path=path):
                    status, _, _ = self.request('POST', path, [('Host', HOST)], b'{}')
                    self.assertEqual(status, 403)
            read.assert_not_called()
            search.assert_not_called()
            status_reader.assert_not_called()
            asset_read.assert_not_called()

    def test_wrong_blank_comma_joined_or_duplicate_identity_is_rejected(self):
        variants = [[], [('Tailscale-User-Login', '')], [('Tailscale-User-Login', 'other@example.test')],
                    [('Tailscale-User-Login', USER+',other@example.test')],
                    [('Tailscale-User-Login', USER), ('tailscale-user-login', USER)],
                    [('Tailscale-User-Login', USER), ('Tailscale-User-Login', 'other@example.test')]]
        for identity in variants:
            with self.subTest(identity=identity):
                self.assertEqual(self.request(headers=[('Host', HOST), *identity])[0], 403)

    def test_loopback_upstream_host_still_requires_identity(self):
        port = self.live._server.server_port
        for host in (f'127.0.0.1:{port}', f'localhost:{port}'):
            with self.subTest(host=host):
                self.assertEqual(self.request(headers=[('Host', host)])[0], 403)
                self.assertEqual(self.request(headers=self.authorized(host))[0], 200)

    def test_missing_duplicate_and_unconfigured_hosts_are_rejected(self):
        hosts = [[], [('Host', HOST), ('host', HOST)],
                 [('Host', HOST), ('Host', 'untrusted.test')],
                 [('Host', 'other.example-tail.ts.net')], [('Host', HOST+':443')],
                 [('Host', HOST+'.invalid')], [('Host', '127.0.0.1:1')]]
        for host in hosts:
            with self.subTest(host=host):
                self.assertEqual(self.request(headers=[*host, ('Tailscale-User-Login', USER)])[0], 400)

    def test_forwarded_headers_cannot_replace_host_or_identity(self):
        forwarded = [('X-Forwarded-Host', HOST), ('X-Forwarded-Proto', 'https'),
                     ('X-Forwarded-User', USER), ('Forwarded', f'host={HOST};proto=https')]
        self.assertEqual(self.request(headers=[('Host', 'untrusted.test'), *forwarded,
                                              ('Tailscale-User-Login', USER)])[0], 400)
        self.assertEqual(self.request(headers=[('Host', HOST), *forwarded])[0], 403)

    def test_authenticated_search_accepts_exact_origin_or_no_origin(self):
        body = json.dumps({'query': 'SYNTHETIC PRIVATE'}).encode()
        for origin in ([], [('Origin', ORIGIN)]):
            with self.subTest(origin=origin):
                status, _, payload = self.request('POST', '/timeline/search.json', self.authorized()+origin, body)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(payload)['total'], 2)

    def test_wrong_blank_comma_joined_or_duplicate_origin_is_rejected_before_search(self):
        port = self.live._server.server_port
        origins = [[('Origin', value)] for value in ('', 'null', 'https://untrusted.test',
                   f'http://127.0.0.1:{port}', ORIGIN+'/', ORIGIN+',https://untrusted.test')]
        origins += [[('Origin', ORIGIN), ('origin', ORIGIN)],
                    [('Origin', ORIGIN), ('Origin', 'https://untrusted.test')]]
        with patch.object(self.live.timeline, 'search') as search:
            for origin in origins:
                with self.subTest(origin=origin):
                    status, _, _ = self.request('POST', '/timeline/search.json', self.authorized()+origin, b'{}')
                    self.assertEqual(status, 403)
            search.assert_not_called()

    def test_authorized_timeline_search_and_graph_preserve_all_input_data(self):
        files = (self.database, self.source, self.map_file)
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        rows_before = [tuple(row) for row in self.db.execute('SELECT * FROM messages ORDER BY id')]
        status, _, payload = self.request(path='/timeline/messages.json?session='+SID)
        self.assertEqual(status, 200)
        view = json.loads(payload)
        self.assertEqual(view['analysis']['relations'], self.analysis['relations'])
        self.assertEqual(view['analysis']['nodes'], self.analysis['nodes'][1:])
        self.assertEqual({message['id'] for message in view['messages']},
                         {row['id'] for row in self.db.execute('SELECT id FROM messages')})
        self.assertEqual(self.request(path='/timeline/sessions.json')[0], 200)
        self.assertEqual(self.request(path='/graph.html')[0], 200)
        self.assertEqual(self.request('POST', '/timeline/search.json', self.authorized()+[('Origin', ORIGIN)],
                                      b'{"query":"SYNTHETIC"}')[0], 200)
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}, before)
        self.assertEqual([tuple(row) for row in self.db.execute('SELECT * FROM messages ORDER BY id')], rows_before)

    def test_default_local_mode_remains_localhost_only_without_identity_requirement(self):
        local = self.dashboard('local')
        host = f'127.0.0.1:{local._server.server_port}'
        self.assertEqual(self.request(headers=[('Host', host)], live=local)[0], 200)
        self.assertEqual(self.request(headers=self.authorized(), live=local)[0], 400)
        self.assertEqual(self.request('POST', '/timeline/search.json',
                        [('Host', host), ('Origin', 'http://'+host)], b'{"query":"SYNTHETIC"}', local)[0], 200)
        self.assertEqual(self.request('POST', '/timeline/search.json',
                        [('Host', host), ('Origin', ORIGIN)], b'{"query":"SYNTHETIC"}', local)[0], 403)

    def test_default_local_mode_also_rejects_duplicate_host_and_origin(self):
        local = self.dashboard('local')
        host = f'localhost:{local._server.server_port}'
        self.assertEqual(self.request(headers=[('Host', host), ('host', host)], live=local)[0], 400)
        self.assertEqual(self.request('POST', '/timeline/search.json',
                        [('Host', host), ('Origin', 'http://'+host), ('origin', 'http://'+host)],
                        b'{"query":"SYNTHETIC"}', local)[0], 403)


if __name__ == '__main__':
    unittest.main()
