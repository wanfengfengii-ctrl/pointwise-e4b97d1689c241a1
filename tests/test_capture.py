import sqlite3
from contextlib import closing

from archive.completeness import inspect_window_progress
from archive.intake import IntakeResult, accept_delivery
from edge.custody import load_custody
from edge.obligations import list_pending_obligations
from instrument.read_head import read_head, read_next_frame
from instrument.release import ReleasePort, read_release_result, release_frame
from station import Frame, advance, deliver, finalize, open_recording, register
from storage import Conflict, LinkUnavailable, connect
from tests.support import FRAMES, RECORDING, StationCase
from windows.frontier import read_frontier
from windows.registrations import list_registrations


class CaptureTests(StationCase):
    def pending_keys(self):
        return tuple((item.registration.window_id, item.frame_number)
                     for item in list_pending_obligations(self.edge, RECORDING))

    def test_overlap_identity(self):
        self.seed()
        self.archive.connection.execute('PRAGMA query_only = ON')
        advance(self.edge, RECORDING)

        def verify_durable_then_release(connection, recording_id, frame_number):
            self.assertEqual(frame_number, 11)
            with closing(connect(self.edge_path)) as durable:
                self.assertEqual(read_head(durable, recording_id).frame_number, 11)
                custody = load_custody(durable, recording_id, 11)
                self.assertEqual(custody.frame, FRAMES[1])
                self.assertFalse(custody.release_confirmed)
                self.assertEqual(read_frontier(durable, recording_id), 11)
                keys = [(item.registration.window_id, item.frame_number)
                        for item in list_pending_obligations(durable, recording_id)]
                self.assertEqual(keys, [('A', 10), ('A', 11), ('B', 11)])
            return release_frame(connection, recording_id, frame_number)

        advance(self.edge, RECORDING, issue_release=verify_durable_then_release)
        self.assertEqual(advance(self.edge, RECORDING).frame, FRAMES[2])
        self.assertIsNone(read_next_frame(self.edge, RECORDING))
        self.archive.connection.execute('PRAGMA query_only = OFF')
        report = deliver(self.edge, self.archive, RECORDING)
        self.assertEqual(report.accepted, (
            IntakeResult(RECORDING, 'A', 10), IntakeResult(RECORDING, 'A', 11),
            IntakeResult(RECORDING, 'B', 11), IntakeResult(RECORDING, 'B', 12),
        ))
        a = finalize(self.archive, RECORDING, 'A').artifact
        b = finalize(self.archive, RECORDING, 'B').artifact
        self.assertEqual(a.frames[1], b.frames[0])
        self.assertEqual(b.frames[0], FRAMES[1])
        self.assertEqual(self.release_positions(), (0, 1, 2))
        self.assertEqual(release_frame(self.edge, RECORDING, 11), read_release_result(self.edge, RECORDING, 11))
        self.assertEqual(self.release_positions(), (0, 1, 2))

    def test_offline_archive(self):
        self.seed()
        self.archive.connection.execute('PRAGMA query_only = ON')
        for frame, remaining in zip(FRAMES, (1, 3, 4)):
            self.assertEqual(advance(self.edge, RECORDING).frame, frame)
            report = deliver(self.edge, self.archive, RECORDING)
            self.assertIsNotNone(report.blocked)
            self.assertEqual(report.accepted, ())
            self.assertEqual(report.remaining, remaining)
        self.assertTrue(advance(self.edge, RECORDING).at_end)
        self.assertTrue(advance(self.edge, RECORDING).at_end)
        self.assertEqual(self.release_positions(), (0, 1, 2))
        self.reopen_edge()
        self.reopen_archive()
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).remaining, 0)
        with self.archive.read_snapshot() as connection:
            self.assertEqual(inspect_window_progress(connection, RECORDING, 'A').frames, FRAMES[:2])
            self.assertEqual(inspect_window_progress(connection, RECORDING, 'B').frames, FRAMES[1:])

    def test_lost_release_reply(self):
        self.seed()
        advance(self.edge, RECORDING)
        port = ReleasePort(drop_reply_once=(RECORDING, 11))
        with self.assertRaisesRegex(LinkUnavailable, 'reply was lost'):
            advance(self.edge, RECORDING, issue_release=port)
        self.assertFalse(load_custody(self.edge, RECORDING, 11).release_confirmed)
        self.assertEqual(read_release_result(self.edge, RECORDING, 11).position, 1)
        self.assertEqual(read_next_frame(self.edge, RECORDING), FRAMES[2])
        self.reopen_edge()
        self.assertEqual(advance(self.edge, RECORDING).frame, FRAMES[2])
        self.assertTrue(load_custody(self.edge, RECORDING, 11).release_confirmed)
        self.assertTrue(load_custody(self.edge, RECORDING, 12).release_confirmed)
        self.assertEqual(self.release_positions(), (0, 1, 2))
        self.assertIsNone(read_next_frame(self.edge, RECORDING))
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).remaining, 0)
        self.assertEqual(finalize(self.archive, RECORDING, 'B').artifact.frames, FRAMES[1:])

    def test_partial_ack_reopen(self):
        self.seed()
        self.archive.connection.execute('PRAGMA query_only = ON')
        self.assertEqual(self.drain_source(), (10, 11, 12))
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).remaining, 4)
        self.archive.connection.execute('PRAGMA query_only = OFF')
        self.archive.connection.execute('''
            CREATE TEMP TRIGGER refuse_after_first BEFORE INSERT ON archive_window_frames
            WHEN EXISTS (SELECT 1 FROM archive_window_frames)
            BEGIN SELECT RAISE(ABORT, 'archive write refused after first item'); END
        ''')
        report = deliver(self.edge, self.archive, RECORDING)
        self.assertEqual(report.accepted, (IntakeResult(RECORDING, 'A', 10),))
        self.assertEqual(report.remaining, 3)
        self.assertIn('archive write refused', report.blocked)
        self.reopen_edge()
        self.reopen_archive()
        self.assertEqual(self.pending_keys(), (('A', 11), ('B', 11), ('B', 12)))
        with self.archive.read_snapshot() as connection:
            self.assertEqual(inspect_window_progress(connection, RECORDING, 'A').frames, FRAMES[:1])
        resumed = deliver(self.edge, self.archive, RECORDING)
        self.assertEqual(len(resumed.accepted), 3)
        self.assertEqual(resumed.remaining, 0)
        self.assertEqual(finalize(self.archive, RECORDING, 'A').artifact.frames, FRAMES[:2])
        self.assertEqual(finalize(self.archive, RECORDING, 'B').artifact.frames, FRAMES[1:])

    def test_reinitialize_keeps_work(self):
        a, b = self.seed()
        advance(self.edge, RECORDING)
        c = register(self.edge, RECORDING, 'C', 12, 12)
        other = open_recording(self.edge, 'other', (Frame(10, -8, 'other/10'),))
        self.assertEqual(deliver(self.edge, self.archive, RECORDING, limit=1).remaining, 0)
        self.archive.connection.execute('PRAGMA query_only = ON')
        self.assertEqual(advance(self.edge, RECORDING).frame, FRAMES[1])
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).remaining, 2)
        self.reopen_edge()
        self.reopen_archive()
        self.seed()
        self.assertEqual(list_registrations(self.edge, RECORDING), (a, b, c))
        self.assertEqual(open_recording(self.edge, 'other', (Frame(10, -8, 'other/10'),)), other)
        self.assertEqual(read_head(self.edge, 'other').frame_number, 10)
        self.assertEqual(read_head(self.edge, RECORDING).frame_number, 12)
        self.assertEqual(self.pending_keys(), (('A', 11), ('B', 11)))
        with self.archive.read_snapshot() as connection:
            self.assertEqual(inspect_window_progress(connection, RECORDING, 'A').frames, FRAMES[:1])
        self.assertEqual(self.drain_source(), (12,))
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).remaining, 0)
        self.assertEqual(finalize(self.archive, RECORDING, 'C').artifact.frames, FRAMES[2:])

    def test_custody_failure_preserves_source_and_admission(self):
        self.seed()
        self.edge.execute('''
            CREATE TEMP TRIGGER reject_obligation BEFORE INSERT ON delivery_obligations
            BEGIN SELECT RAISE(ABORT, 'custody obligation write refused'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'obligation write refused'):
            advance(self.edge, RECORDING)
        self.assertIsNone(read_frontier(self.edge, RECORDING))
        self.assertIsNone(load_custody(self.edge, RECORDING, 10))
        self.assertIsNone(read_release_result(self.edge, RECORDING, 10))
        self.assertEqual(read_next_frame(self.edge, RECORDING), FRAMES[0])
        register(self.edge, RECORDING, 'still-valid', 10, 10)
        self.edge.execute('DROP TRIGGER reject_obligation')
        self.assertEqual(advance(self.edge, RECORDING).frame, FRAMES[0])
        self.assertEqual(self.pending_keys(), (('A', 10), ('still-valid', 10)))

    def test_uncovered_frame_and_wrong_release(self):
        open_recording(self.edge, RECORDING, FRAMES)
        register(self.edge, RECORDING, 'tail', 12, 12)
        with self.assertRaises(Conflict):
            release_frame(self.edge, RECORDING, 11)
        self.assertEqual(read_next_frame(self.edge, RECORDING), FRAMES[0])
        self.assertEqual(advance(self.edge, RECORDING).frame, FRAMES[0])
        self.assertEqual(load_custody(self.edge, RECORDING, 10).frame, FRAMES[0])
        self.assertEqual(self.pending_keys(), ())
        self.assertEqual(self.release_positions(), (0,))

    def test_lost_archive_reply_preserves_obligation(self):
        self.seed()
        advance(self.edge, RECORDING)

        def commit_then_disconnect(archive, material):
            accept_delivery(archive, material)
            raise LinkUnavailable('archive reply lost after commit')

        report = deliver(self.edge, self.archive, RECORDING, intake=commit_then_disconnect)
        self.assertEqual(report.accepted, ())
        self.assertEqual(report.remaining, 1)
        self.assertIn('reply lost after commit', report.blocked)
        self.reopen_edge()
        self.reopen_archive()
        with self.archive.read_snapshot() as connection:
            self.assertEqual(inspect_window_progress(connection, RECORDING, 'A').frames, FRAMES[:1])
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).accepted,
                         (IntakeResult(RECORDING, 'A', 10),))
        with self.archive.read_snapshot() as connection:
            self.assertEqual(inspect_window_progress(connection, RECORDING, 'A').frames, FRAMES[:1])
