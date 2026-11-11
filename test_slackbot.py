import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import hashlib
import hmac
import shutil
import subprocess

import slackbot as bot


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'queue.sqlite'
        self.seed = Path(self.temp.name) / 'tas.json'
        self.seed.write_text('{"TA": "Assistant"}')
        self.enterContext(patch.object(bot, 'DB_PATH', self.path))
        self.enterContext(patch.object(bot, 'TA_DICT_PATH', self.seed))
        self.q = self.enterContext(bot.PersistentQueue())

    def command(self, action, text='', user='TA'):
        return bot.run_action(bot.Request(action, text.split(), user), self.q)

    def join(self, user, kind='inperson', consent=True):
        self.q.add_user_to_queue(user, kind, consent)

    def test_group_fairness_and_online_isolation(self):
        self.join('ONLINE', 'online')
        self.join('A')
        self.join('B', consent=False)
        self.join('C')
        self.assertIn('<@A>', self.command('next-group', '5')['text'])
        for _ in range(3):
            self.assertIn('/next-inperson', self.command('next-group', '5')['text'])
        self.assertEqual(self.q.get_users_in_queue(), ['ONLINE', 'B', 'C'])
        self.assertIn('<@B>', self.command('next-inperson')['text'])
        self.assertEqual(self.q.next_group(5), ['C'])
        self.assertEqual(self.q.next(), 'ONLINE')

    def test_group_limits_and_bad_arguments(self):
        self.join('A')
        for value in ('', '0', '6', '10000', '-2.4', '2.3', 'two', '1 2'):
            with self.subTest(value=value):
                self.assertIn('1 to 5', self.command('next-group', value)['text'])
                self.assertEqual(self.q.get_users_in_queue(), ['A'])
        for value in (0, 6, 10000, -2.4, 2.3, True):
            with self.assertRaises(ValueError):
                self.q.next_group(value)
        self.assertEqual(self.q.next_group(1), ['A'])
        self.assertIn('no one', self.command('next-group', '5')['text'])

    def test_maximum_five(self):
        for i in range(6):
            self.join(str(i))
        self.assertEqual(len(self.q.next_group(5)), 5)
        self.assertEqual(self.q.get_users_in_queue(), ['5'])

    def test_ta_permissions(self):
        self.join('A')
        for action in ('next', 'next-group', 'clearqueue', 'closequeue', 'ta'):
            with self.assertRaises(bot.RequestError):
                self.command(action, '2', 'STUDENT')
        self.assertEqual(self.q.get_users_in_queue(), ['A'])

    def test_unique_user_constraint(self):
        self.join('A')
        self.assertIsNone(self.q.add_user_to_queue('A', 'online'))
        with self.assertRaises(sqlite3.IntegrityError), self.q.conn:
            self.q.conn.execute("INSERT INTO queue(user) VALUES ('A')")

    def test_legacy_migration_preserves_oldest_and_defaults_to_no(self):
        path = Path(self.temp.name) / 'legacy.sqlite'
        with contextlib.closing(sqlite3.connect(path)) as conn, conn:
            conn.execute('CREATE TABLE queue(user TEXT NOT NULL)')
            conn.executemany('INSERT INTO queue VALUES (?)', [('A',), ('B',), ('A',)])
        with bot.PersistentQueue(path) as q:
            self.assertEqual(q.get_entries(), [('A', 'inperson'), ('B', 'inperson')])
            with self.assertRaises(bot.IndividualPassoffRequired):
                q.next_group(2)

    def test_tas_migrate_once_and_mentions_use_ids(self):
        self.assertIn('<@TA>', self.command('ta')['text'])
        self.q.update_ta('TA', '', False)
        with bot.PersistentQueue() as q:
            self.assertFalse(q.is_user_a_ta('TA'))
            q.import_tas(self.seed)
            self.assertTrue(q.is_user_a_ta('TA'))

    def test_ta_add_remove_and_mention_formats(self):
        self.command('ta', 'add <@U123|Alice>')
        self.assertTrue(self.q.is_user_a_ta('U123'))
        self.command('ta', 'rmv <@U123>')
        self.assertFalse(self.q.is_user_a_ta('U123'))

    def test_concurrent_groups_are_disjoint(self):
        for i in range(6):
            self.join(str(i))
        barrier = Barrier(2)
        def select():
            with bot.PersistentQueue() as q:
                barrier.wait(timeout=5)
                return q.next_group(3)
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(select) for _ in range(2)]
            groups = [f.result(timeout=5) for f in futures]
        self.assertCountEqual(groups, [['0', '1', '2'], ['3', '4', '5']])

    def test_concurrent_ta_updates_are_preserved(self):
        barrier = Barrier(2)
        def add(user):
            with bot.PersistentQueue() as q:
                barrier.wait(timeout=5)
                q.update_ta(user, user, True)
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(add, ['ONE', 'TWO']))
        self.assertEqual(set(self.q.get_tas()), {'TA', 'ONE', 'TWO'})

    def test_clear_cannot_delete_an_unannounced_concurrent_join(self):
        self.join('OLD')
        ready, snapshot, attempted = Event(), Event(), Event()
        original = self.q.get_entries
        def join_during_clear():
            with bot.PersistentQueue() as q:
                ready.set()
                if not snapshot.wait(5):
                    raise AssertionError('Snapshot never taken')
                attempted.set()
                q.add_user_to_queue('NEW')
        def snapshot_entries(*args):
            entries = original(*args)
            snapshot.set()
            self.assertTrue(attempted.wait(5))
            return entries
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(join_during_clear)
            self.assertTrue(ready.wait(5))
            with patch.object(self.q, 'get_entries', side_effect=snapshot_entries):
                removed = self.q.clear_queue()
            future.result(timeout=5)
        self.assertEqual(removed, [('OLD', 'inperson')])
        self.assertEqual(self.q.get_users_in_queue(), ['NEW'])

    def test_clear_and_close_use_removed_snapshot(self):
        for action in ('clearqueue', 'closequeue'):
            self.join('A')
            self.assertIn('<@A>', self.command(action)['text'])
            self.assertEqual(self.q.get_entries(), [])

    def submit(self, choice, view='V1'):
        return bot.handle_interaction({'type': 'view_submission', 'user': {'id': 'NEW'}, 'view': {
            'id': view, 'callback_id': 'inperson_consent', 'state': {'values': {
                'group_consent': {'choice': {'selected_option': {'value': choice}}}
            }}
        }}, self.q)

    def test_consent_required_and_replay_does_not_rejoin(self):
        self.assertEqual(self.submit(None)['response_action'], 'errors')
        self.assertEqual(self.q.get_entries(), [])
        self.assertEqual(self.submit('no')['response_action'], 'update')
        with self.assertRaises(bot.IndividualPassoffRequired):
            self.q.next_group(1)
        self.q.next()
        self.submit('no')
        self.assertEqual(self.q.get_entries(), [])
        self.submit('yes', 'V2')
        self.assertEqual(self.q.next_group(1), ['NEW'])

    def test_modal_open_and_failure_do_not_join(self):
        req = bot.Request('passoff-inperson', [], 'NEW', 'trigger')
        with patch.dict(os.environ, {'SLACK_BOT_TOKEN': 'fake'}), patch('urllib.request.urlopen') as api:
            api.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
            self.assertIsNone(bot.handle_passoff(req, self.q))
            view = json.loads(api.call_args.args[0].data)['view']
            self.assertNotIn('initial_option', view['blocks'][1]['element'])
            api.side_effect = TimeoutError
            self.assertIn('not joined', bot.handle_passoff(req, self.q)['text'])
        self.assertEqual(self.q.get_entries(), [])

    def test_one_json_response_and_no_error_details(self):
        out = io.StringIO()
        with patch.object(bot, 'run', side_effect=RuntimeError('private details')), \
             patch.object(bot.logging, 'exception') as log, contextlib.redirect_stdout(out):
            self.assertEqual(bot.main(['bot']), 1)
        self.assertNotIn('private details', out.getvalue())
        self.assertIn('contact a TA', json.loads(out.getvalue())['text'])
        log.assert_called_once()

    def test_stdin_entrypoint(self):
        out = io.StringIO()
        post = {'command': '/queue', 'text': '', 'user_id': 'STUDENT'}
        with patch('sys.stdin', io.StringIO(json.dumps(post))), contextlib.redirect_stdout(out):
            self.assertEqual(bot.main(['bot', '--stdin']), 0)
        self.assertIn('both queues', json.loads(out.getvalue())['text'])

    def test_context_manager_closes_on_failure(self):
        with self.assertRaises(RuntimeError):
            with bot.PersistentQueue() as q:
                connection = q.conn
                raise RuntimeError()
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')

    def test_default_paths_are_absolute(self):
        self.assertTrue(bot.BASE_DIR.is_absolute())
        self.assertEqual(bot.BASE_DIR, Path(bot.__file__).resolve().parent)

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions require Linux/macOS')
    def test_database_permissions(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


@unittest.skipUnless(shutil.which('php'), 'PHP runtime not installed')
class PhpTests(unittest.TestCase):
    def test_lint(self):
        subprocess.run(['php', '-l', str(bot.BASE_DIR / 'index.php')], check=True, capture_output=True)

    def test_signature_validation(self):
        # Load the actual endpoint functions without executing its entry point.
        source = (bot.BASE_DIR / 'index.php').read_text().removeprefix('<?php')
        source = source.rsplit('try {\n    run_python_slackbot();', 1)[0]
        body, timestamp, secret = 'command=%2Fqueue&user_id=TA', '1700000000', 'test-secret'
        signature = 'v0=' + hmac.new(secret.encode(), f'v0:{timestamp}:{body}'.encode(), hashlib.sha256).hexdigest()
        values = [(body, timestamp, signature, 1700000000, True),
                  (body + 'x', timestamp, signature, 1700000000, False),
                  (body, timestamp, signature, 1700000301, False),
                  (body, '', signature, 1700000000, False),
                  (body, timestamp, '', 1700000000, False)]
        for raw, stamp, sig, now, expected in values:
            call = 'echo json_encode(valid_slack_signature(' + ','.join(json.dumps(v) for v in [secret, stamp, sig, raw, now]) + '));'
            result = subprocess.run(['php', '-r', source + call], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), expected)


if __name__ == '__main__':
    unittest.main()
