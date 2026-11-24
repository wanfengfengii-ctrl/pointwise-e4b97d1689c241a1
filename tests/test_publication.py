import json
import sqlite3
from decimal import Decimal

from archive.completeness import IncompleteWindow, inspect_window_progress
from archive.evidence import load_window_definition
from archive.intake import DeliveryMaterial, accept_delivery
from instrument.release import ReleasePort
from records.receipts import find_window_receipt
from station import Frame, advance, deliver, export_result, finalize, get_result, open_recording, register
from storage import Conflict, ContractError, LinkUnavailable, NotFound
from tests.support import FRAMES, RECORDING, StationCase


class PublicationTests(StationCase):
    def fill_archive(self):
        self.seed()
        self.assertEqual(self.drain_source(), (10, 11, 12))
        report = deliver(self.edge, self.archive, RECORDING)
        self.assertIsNone(report.blocked)
        self.assertEqual(report.remaining, 0)

    def assert_unpublished(self, window_id):
        with self.archive.read_snapshot() as connection:
            self.assertIsNone(find_window_receipt(connection, RECORDING, window_id))
            row = connection.execute('SELECT 1 FROM artifacts WHERE recording_id = ? AND window_id = ?',
                                     (RECORDING, window_id)).fetchone()
            self.assertIsNone(row)

    def test_peak_to_peak_and_readback(self):
        self.fill_archive()
        a = finalize(self.archive, RECORDING, 'A')
        b = finalize(self.archive, RECORDING, 'B')
        self.assertNotEqual(a.receipt.result_id, b.receipt.result_id)
        self.edge.close()
        self.reopen_archive()
        for published, numbers, values, origins, amplitude in (
            (a, [10, 11], [4, 9], ['recorder/31/10', 'recorder/31/11'], 5),
            (b, [11, 12], [9, 6], ['recorder/31/11', 'recorder/31/12'], 3),
        ):
            result = get_result(self.archive, published.receipt.result_id)
            self.assertEqual(result, published)
            self.assertEqual([item.number for item in result.artifact.frames], numbers)
            self.assertEqual([item.value for item in result.artifact.frames], values)
            self.assertEqual([item.origin for item in result.artifact.frames], origins)
            self.assertEqual(result.artifact.peak_to_peak, amplitude)
            exported = json.loads(export_result(self.archive, result.receipt.result_id))
            self.assertEqual(exported['peak_to_peak'], amplitude)
            self.assertEqual(exported['unit'], 'device_count')
            self.assertEqual(exported['receipt']['result_id'], result.receipt.result_id)
        with self.assertRaises(NotFound):
            get_result(self.archive, 999)

    def test_a_then_b_reopen(self):
        self.seed()
        self.archive.connection.execute('PRAGMA query_only = ON')
        self.assertEqual(advance(self.edge, RECORDING).frame, FRAMES[0])
        port = ReleasePort(drop_reply_once=(RECORDING, 11))
        with self.assertRaisesRegex(LinkUnavailable, 'reply was lost'):
            advance(self.edge, RECORDING, issue_release=port)
        self.reopen_edge()
        self.assertEqual(advance(self.edge, RECORDING).frame, FRAMES[2])
        self.assertTrue(advance(self.edge, RECORDING).at_end)
        self.assertEqual(self.release_positions(), (0, 1, 2))
        self.reopen_archive()
        report = deliver(self.edge, self.archive, RECORDING)
        self.assertIsNone(report.blocked)
        a = finalize(self.archive, RECORDING, 'A')
        self.assertEqual(a.artifact.frames, FRAMES[:2])
        self.assertEqual(a.artifact.peak_to_peak, 5)
        self.reopen_archive()
        self.assertEqual(get_result(self.archive, a.receipt.result_id), a)
        try:
            b = finalize(self.archive, RECORDING, 'B')
        except IncompleteWindow as error:
            if 11 in error.missing:
                self.fail('SHARED_FRAME_MISSING: B cannot sign with original frame 11 after A')
            raise
        self.assertIn(11, [frame.number for frame in b.artifact.frames], 'SHARED_FRAME_MISSING')
        self.assertEqual(b.artifact.frames, FRAMES[1:])
        self.assertEqual(b.artifact.peak_to_peak, 3)
        self.assertEqual(a.artifact.frames[1].origin, b.artifact.frames[0].origin)
        self.edge.close()
        self.reopen_archive()
        for result in (a, b):
            self.assertEqual(get_result(self.archive, result.receipt.result_id), result)
            self.assertEqual(finalize(self.archive, RECORDING, result.receipt.window_id), result)

    def test_missing_frame(self):
        self.seed()
        advance(self.edge, RECORDING)
        advance(self.edge, RECORDING)
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).remaining, 0)
        with self.assertRaises(IncompleteWindow) as caught:
            finalize(self.archive, RECORDING, 'B')
        self.assertEqual(caught.exception.missing, (12,))
        self.assert_unpublished('B')
        self.assertEqual(advance(self.edge, RECORDING).frame, FRAMES[2])
        deliver(self.edge, self.archive, RECORDING)
        self.assertEqual(finalize(self.archive, RECORDING, 'B').artifact.frames, FRAMES[1:])

    def test_last_frame_first(self):
        _, b = self.seed()
        accept_delivery(self.archive, DeliveryMaterial(b, FRAMES[2]))
        with self.archive.read_snapshot() as connection:
            progress = inspect_window_progress(connection, RECORDING, 'B')
        self.assertFalse(progress.complete)
        self.assertEqual(progress.missing, (11,))
        self.assertEqual(progress.frames, FRAMES[2:])
        with self.assertRaises(IncompleteWindow):
            finalize(self.archive, RECORDING, 'B')
        self.assert_unpublished('B')
        accept_delivery(self.archive, DeliveryMaterial(b, FRAMES[1]))
        result = finalize(self.archive, RECORDING, 'B')
        self.assertEqual(result.artifact.frames, FRAMES[1:])
        self.assertEqual(result.artifact.peak_to_peak, 3)

    def test_evidence_replay(self):
        _, b = self.seed()
        material = DeliveryMaterial(b, FRAMES[1])
        first = accept_delivery(self.archive, material)
        self.reopen_archive()
        self.assertEqual(accept_delivery(self.archive, material), first)
        with self.archive.read_snapshot() as connection:
            self.assertEqual(inspect_window_progress(connection, RECORDING, 'B').frames, FRAMES[1:2])
        with self.assertRaises(IncompleteWindow) as caught:
            finalize(self.archive, RECORDING, 'B')
        self.assertEqual(caught.exception.missing, (12,))
        self.assert_unpublished('B')

    def test_zero_and_precision(self):
        recording = 'precision'
        large = 10 ** 4500
        frames = (Frame(0, 0, 'p/0'), Frame(1, -9, 'p/1'), Frame(2, large, 'p/2'),
                  Frame(3, large + 1, 'p/3'))
        open_recording(self.edge, recording, frames)
        for window, left, right in (('zero-negative', 0, 1), ('single', 0, 0), ('precision', 2, 3),
                                    ('wide-amplitude', 1, 3)):
            register(self.edge, recording, window, left, right)
        self.assertEqual(self.drain_source(recording), (0, 1, 2, 3))
        self.assertEqual(deliver(self.edge, self.archive, recording).remaining, 0)
        signed = {name: finalize(self.archive, recording, name)
                  for name in ('zero-negative', 'single', 'precision', 'wide-amplitude')}
        self.edge.close()
        self.reopen_archive()
        for name, original_frames, amplitude in (
            ('zero-negative', frames[:2], 9), ('single', frames[:1], 0),
            ('precision', frames[2:], 1), ('wide-amplitude', frames[1:], large + 10),
        ):
            result_id = signed[name].receipt.result_id
            result = get_result(self.archive, result_id)
            self.assertEqual(result, signed[name])
            self.assertTrue(result.artifact.frames == original_frames, name)
            self.assertTrue(result.artifact.peak_to_peak == amplitude, name)
            exported = json.loads(export_result(self.archive, result_id), parse_int=Decimal)
            self.assertTrue(exported['peak_to_peak'] == amplitude, name)
            self.assertTrue([item['value'] for item in exported['frames']] ==
                            [frame.value for frame in original_frames], name)

    def test_export_numeric_order(self):
        frames = (Frame(9, 4, 'numeric/9'), Frame(10, 9, 'numeric/10'), Frame(11, 6, 'numeric/11'))
        open_recording(self.edge, 'numeric', frames)
        register(self.edge, 'numeric', 'A', 9, 10)
        register(self.edge, 'numeric', 'B', 10, 11)
        self.drain_source('numeric')
        deliver(self.edge, self.archive, 'numeric')
        a = finalize(self.archive, 'numeric', 'A')
        self.reopen_archive()
        b = finalize(self.archive, 'numeric', 'B')
        self.edge.close()
        self.reopen_archive()
        self.assertEqual(get_result(self.archive, a.receipt.result_id).artifact.frames, frames[:2])
        self.assertEqual(get_result(self.archive, b.receipt.result_id).artifact.frames, frames[1:])
        encoded = export_result(self.archive, a.receipt.result_id)
        self.assertEqual(encoded, export_result(self.archive, a.receipt.result_id))
        self.assertEqual(json.loads(encoded)['frames'], [
            {'number': 9, 'value': 4, 'origin': 'numeric/9'},
            {'number': 10, 'value': 9, 'origin': 'numeric/10'},
        ])
        self.assertEqual(json.loads(encoded)['peak_to_peak'], 5)

    def test_independent_commit_failure(self):
        self.fill_archive()
        with self.archive.write_transaction() as connection:
            connection.execute('CREATE TABLE allowed_publications (id INTEGER PRIMARY KEY)')
            connection.execute('''
                CREATE TABLE publication_barrier (
                    id INTEGER REFERENCES allowed_publications(id) DEFERRABLE INITIALLY DEFERRED
                )
            ''')
            connection.execute('''
                CREATE TRIGGER fail_a_commit AFTER INSERT ON publication_receipts
                WHEN NEW.window_id = 'A'
                BEGIN
                    INSERT INTO publication_barrier VALUES (NEW.result_id);
                END
            ''')
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            finalize(self.archive, RECORDING, 'A')
        self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY)
        self.reopen_archive()
        self.assert_unpublished('A')
        with self.archive.read_snapshot() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM artifact_frames').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT count(*) FROM publication_barrier').fetchone()[0], 0)
            self.assertTrue(inspect_window_progress(connection, RECORDING, 'A').complete)
        b = finalize(self.archive, RECORDING, 'B')
        self.assertEqual(b.artifact.frames, FRAMES[1:])
        self.assertEqual(b.artifact.peak_to_peak, 3)
        self.assert_unpublished('A')
        with self.archive.write_transaction() as connection:
            connection.execute('DROP TRIGGER fail_a_commit')
        a = finalize(self.archive, RECORDING, 'A')
        self.assertEqual(a.artifact.frames, FRAMES[:2])
        self.assertEqual(a.artifact.peak_to_peak, 5)
        self.assertEqual(a.artifact.frames[1], b.artifact.frames[0])
        self.assertEqual(finalize(self.archive, RECORDING, 'A'), a)
        with self.archive.read_snapshot() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM artifacts').fetchone()[0], 2)
            self.assertEqual(connection.execute('SELECT count(*) FROM publication_receipts').fetchone()[0], 2)

    def test_conflicting_evidence_rolls_back(self):
        a, b = self.seed()
        accept_delivery(self.archive, DeliveryMaterial(a, FRAMES[1]))
        with self.assertRaises(Conflict):
            accept_delivery(self.archive, DeliveryMaterial(b, Frame(11, 9, 'different-origin')))
        with self.assertRaises(Conflict):
            accept_delivery(self.archive, DeliveryMaterial(b, Frame(11, 90, FRAMES[1].origin)))
        with self.assertRaises(ContractError):
            accept_delivery(self.archive, DeliveryMaterial(b, FRAMES[0]))
        with self.assertRaises(ContractError):
            accept_delivery(self.archive, DeliveryMaterial(b, Frame(11, False, FRAMES[1].origin)))
        with self.archive.read_snapshot() as connection:
            self.assertIsNone(load_window_definition(connection, RECORDING, 'B'))
            self.assertEqual(inspect_window_progress(connection, RECORDING, 'A').frames, FRAMES[1:2])
        accept_delivery(self.archive, DeliveryMaterial(b, FRAMES[1]))
        self.assert_unpublished('B')
