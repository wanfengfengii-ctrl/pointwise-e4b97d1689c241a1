import json
import sqlite3

from archive.completeness import inspect_window_progress
from archive.intake import DeliveryMaterial, IntakeResult, accept_delivery
from archive.spill import read_spilled
from edge.custody import list_custody_windows, load_custody
from edge.obligations import list_pending_obligations
from instrument.read_head import read_head
from instrument.release import ReleasePort, read_release_result
from spill_scenario import ArchiveLink, block_local_body
from station import (
    advance, advance_with_archive, deliver, export_result, finalize, get_result, register,
)
from storage import Conflict, LinkUnavailable
from tests.support import FRAMES, RECORDING, StationCase
from windows.frontier import read_frontier


class SpillCase(StationCase):
    def setUp(self):
        super().setUp()
        self.archive.close()
        self.archive = ArchiveLink(self.archive_path)
        self.addCleanup(lambda: self.archive.close())

    def pending_keys(self):
        return tuple((item.registration.window_id, item.frame_number)
                     for item in list_pending_obligations(self.edge, RECORDING))

    def observer(self):
        from station import ArchiveStore
        store = ArchiveStore(self.archive_path)
        self.addCleanup(store.close)
        return store

    def missing(self, window_id, store=None):
        store = store or self.observer()
        with store.read_snapshot() as connection:
            return inspect_window_progress(connection, RECORDING, window_id).missing

    def test_spill_takeover_and_delivery(self):
        self.seed()
        self.assertEqual(advance_with_archive(self.edge, self.archive, RECORDING).frame, FRAMES[0])
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).remaining, 0)

        # Only an explicit local body refusal switches to the archive spill.
        with block_local_body(RECORDING, 11):
            with self.assertRaises(sqlite3.OperationalError):
                advance(self.edge, RECORDING)
            self.assertEqual(read_head(self.edge, RECORDING).frame_number, 11)
            self.assertIsNone(read_release_result(self.edge, RECORDING, 11))
            step = advance_with_archive(self.edge, self.archive, RECORDING)
        self.assertEqual(step.frame, FRAMES[1])
        self.assertEqual(step.release.position, 1)
        custody = load_custody(self.edge, RECORDING, 11)
        self.assertTrue(custody.body_spilled)
        self.assertIsNone(custody.frame)
        self.assertEqual(custody.position, 1)
        self.assertEqual(list_custody_windows(self.edge, RECORDING, 11), ('A', 'B'))

        self.assertEqual(advance_with_archive(self.edge, self.archive, RECORDING).frame, FRAMES[2])
        self.assertTrue(advance_with_archive(self.edge, self.archive, RECORDING).at_end)
        self.assertEqual(self.release_positions(), (0, 1, 2))

        report = deliver(self.edge, self.archive, RECORDING)
        self.assertIsNone(report.blocked)
        self.assertEqual(report.remaining, 0)
        self.assertEqual(report.accepted, (
            IntakeResult(RECORDING, 'A', 11), IntakeResult(RECORDING, 'B', 11),
            IntakeResult(RECORDING, 'B', 12),
        ))
        self.edge.close()
        self.archive.close()
        self.reopen_archive()
        signed = {name: finalize(self.archive, RECORDING, name) for name in ('A', 'B')}
        for name, numbers, values, amplitude in (
            ('A', [10, 11], [4, 9], 5),
            ('B', [11, 12], [9, 6], 3),
        ):
            result = get_result(self.archive, signed[name].receipt.result_id)
            self.assertEqual([frame.number for frame in result.artifact.frames], numbers)
            self.assertEqual([frame.value for frame in result.artifact.frames], values)
            self.assertEqual([frame.origin for frame in result.artifact.frames],
                             [f'recorder/31/{number}' for number in numbers])
            self.assertEqual(result.artifact.peak_to_peak, amplitude)
            exported = json.loads(export_result(self.archive, result.receipt.result_id))
            self.assertEqual(exported['peak_to_peak'], amplitude)
        self.assertEqual(signed['A'].artifact.frames[1], signed['B'].artifact.frames[0])
        self.assertEqual(finalize(self.archive, RECORDING, 'A'), signed['A'])

    def test_rejected_spill_keeps_source_frame(self):
        self.seed()
        advance_with_archive(self.edge, self.archive, RECORDING)
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).remaining, 0)
        self.archive.next_fault = 'reject'
        with block_local_body(RECORDING, 11):
            with self.assertRaisesRegex(LinkUnavailable, 'not durably recorded'):
                advance_with_archive(self.edge, self.archive, RECORDING)
        self.assertEqual(read_head(self.edge, RECORDING).frame_number, 11)
        self.assertIsNone(read_release_result(self.edge, RECORDING, 11))
        self.assertIsNone(load_custody(self.edge, RECORDING, 11))
        self.assertEqual(read_frontier(self.edge, RECORDING), 10)
        self.assertEqual(self.missing('A'), (11,))
        # The window is still open for registration and retry.
        register(self.edge, RECORDING, 'C', 11, 11)
        with block_local_body(RECORDING, 11):
            self.assertEqual(advance_with_archive(self.edge, self.archive, RECORDING).frame,
                             FRAMES[1])

    def test_lost_spill_reply_waits_for_recovery(self):
        self.seed()
        advance_with_archive(self.edge, self.archive, RECORDING)
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).remaining, 0)
        self.archive.next_fault = 'drop_reply'
        with block_local_body(RECORDING, 11):
            with self.assertRaises(LinkUnavailable):
                advance_with_archive(self.edge, self.archive, RECORDING)
        self.assertFalse(self.archive.online)
        self.assertEqual(read_head(self.edge, RECORDING).frame_number, 11)
        self.assertIsNone(read_release_result(self.edge, RECORDING, 11))
        self.assertIsNone(load_custody(self.edge, RECORDING, 11))
        # A separate archive connection can still see the committed original,
        # and the window is not complete just because the spill committed.
        observer = self.observer()
        spilled = read_spilled(observer, RECORDING, 11)
        self.assertEqual(spilled.frame, FRAMES[1])
        self.assertEqual(spilled.position, 1)
        self.assertEqual(self.missing('A', observer), (11,))

        # Registration proceeds during the spill wait; the formal takeover
        # freezes every accepted window covering the frame.
        c = register(self.edge, RECORDING, 'C', 11, 11)
        self.assertEqual(c.interval.expected, (11,))

        # Reopen both sides and continue: the same original is recovered.
        self.reopen_edge()
        self.archive.close()
        self.archive = ArchiveLink(self.archive_path)
        self.addCleanup(lambda: self.archive.close())
        with block_local_body(RECORDING, 11):
            step = advance_with_archive(self.edge, self.archive, RECORDING)
        self.assertEqual(step.frame, FRAMES[1])
        self.assertEqual(list_custody_windows(self.edge, RECORDING, 11), ('A', 'B', 'C'))
        self.assertEqual(advance_with_archive(self.edge, self.archive, RECORDING).frame, FRAMES[2])
        self.assertTrue(advance_with_archive(self.edge, self.archive, RECORDING).at_end)
        self.assertEqual(self.release_positions(), (0, 1, 2))

        report = deliver(self.edge, self.archive, RECORDING)
        self.assertEqual(report.remaining, 0)
        self.edge.close()
        self.archive.close()
        self.reopen_archive()
        signed = {name: finalize(self.archive, RECORDING, name) for name in ('A', 'B', 'C')}
        self.assertEqual(signed['A'].artifact.peak_to_peak, 5)
        self.assertEqual(signed['B'].artifact.peak_to_peak, 3)
        self.assertEqual(signed['C'].artifact.peak_to_peak, 0)
        self.assertEqual(signed['C'].artifact.frames, (FRAMES[1],))
        self.assertEqual(signed['A'].artifact.frames[1].origin, 'recorder/31/11')
        self.assertEqual(signed['B'].artifact.frames[0].origin, 'recorder/31/11')
        self.assertEqual(signed['C'].artifact.frames[0].origin, 'recorder/31/11')

    def test_formal_takeover_failure_preserves_obligations(self):
        self.seed()
        advance_with_archive(self.edge, self.archive, RECORDING)
        self.edge.execute('''
            CREATE TEMP TRIGGER reject_spill_metadata BEFORE INSERT ON spilled_custody
            BEGIN SELECT RAISE(ABORT, 'formal takeover metadata refused'); END
        ''')
        with block_local_body(RECORDING, 11):
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'takeover metadata refused'):
                advance_with_archive(self.edge, self.archive, RECORDING)
        self.assertEqual(read_head(self.edge, RECORDING).frame_number, 11)
        self.assertIsNone(read_release_result(self.edge, RECORDING, 11))
        self.assertIsNone(load_custody(self.edge, RECORDING, 11))
        self.assertEqual(read_frontier(self.edge, RECORDING), 10)
        self.assertEqual(self.pending_keys(), (('A', 10),))
        # Release eligibility and per-window duties are unchanged; retry works.
        self.edge.execute('DROP TRIGGER reject_spill_metadata')
        with block_local_body(RECORDING, 11):
            self.assertEqual(advance_with_archive(self.edge, self.archive, RECORDING).frame,
                             FRAMES[1])
        self.assertEqual(self.release_positions(), (0, 1))

    def test_delivery_reconciles_spilled_original(self):
        self.seed()
        advance_with_archive(self.edge, self.archive, RECORDING)
        with block_local_body(RECORDING, 11):
            advance_with_archive(self.edge, self.archive, RECORDING)
        a = list(obligation.registration for obligation in list_pending_obligations(self.edge, RECORDING)
                 if obligation.registration.window_id == 'A' and obligation.frame_number == 11)[0]
        b = register(self.edge, RECORDING, 'B', 11, 12)
        authorized = list_custody_windows(self.edge, RECORDING, 11)
        # A window the formal takeover did not authorize cannot receipt the frame.
        with self.assertRaises(Conflict):
            accept_delivery(self.archive, DeliveryMaterial(b, FRAMES[1], 1, ('A',)))
        # Same original as the durable spill must be presented.
        with self.assertRaises(Conflict):
            accept_delivery(self.archive, DeliveryMaterial(a, FRAMES[1], 0, authorized))
        with self.assertRaises(Conflict):
            accept_delivery(self.archive, DeliveryMaterial(
                a, type(FRAMES[1])(11, 90, FRAMES[1].origin), 1, authorized))
        # The matching original is accepted and window receipt becomes durable.
        result = accept_delivery(self.archive, DeliveryMaterial(a, FRAMES[1], 1, authorized))
        self.assertEqual(result, IntakeResult(RECORDING, 'A', 11))
        with self.archive.read_snapshot() as connection:
            progress = inspect_window_progress(connection, RECORDING, 'A')
        self.assertEqual(progress.missing, (10,))
        self.assertEqual(progress.frames[-1], FRAMES[1])

    def test_spill_takeover_release_interrupted(self):
        """A failed release after the formal takeover keeps the frame pending."""
        self.seed()
        advance_with_archive(self.edge, self.archive, RECORDING)
        port = ReleasePort(hold_once=(RECORDING, 11))
        with block_local_body(RECORDING, 11):
            with self.assertRaisesRegex(LinkUnavailable, 'did not reach'):
                advance_with_archive(self.edge, self.archive, RECORDING, issue_release=port)
        custody = load_custody(self.edge, RECORDING, 11)
        self.assertTrue(custody.body_spilled)
        self.assertFalse(custody.release_confirmed)
        self.assertIsNone(read_release_result(self.edge, RECORDING, 11))
        self.assertEqual(read_head(self.edge, RECORDING).frame_number, 11)
        # Retrying first re-confirms the same durable original, releasing it
        # once, then continues with the next frame.
        with block_local_body(RECORDING, 11):
            step = advance_with_archive(self.edge, self.archive, RECORDING)
        self.assertEqual(step.frame, FRAMES[2])
        self.assertTrue(load_custody(self.edge, RECORDING, 11).release_confirmed)
        self.assertEqual(self.release_positions(), (0, 1, 2))

    def test_duplicate_delivery_keeps_first_receipt(self):
        self.seed()
        advance_with_archive(self.edge, self.archive, RECORDING)
        with block_local_body(RECORDING, 11):
            advance_with_archive(self.edge, self.archive, RECORDING)
        self.assertEqual(advance_with_archive(self.edge, self.archive, RECORDING).frame, FRAMES[2])
        first = deliver(self.edge, self.archive, RECORDING)
        self.assertEqual(first.remaining, 0)
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).accepted, ())
        with self.archive.read_snapshot() as connection:
            self.assertEqual(inspect_window_progress(connection, RECORDING, 'B').frames, FRAMES[1:])
        signed = finalize(self.archive, RECORDING, 'B')
        self.assertEqual(signed.artifact.peak_to_peak, 3)

    def test_spill_during_whole_recording(self):
        """Every frame spilled: the archive still publishes ordinary results."""
        self.seed()
        with block_local_body(RECORDING, 10), block_local_body(RECORDING, 11), \
                block_local_body(RECORDING, 12):
            numbers = []
            while True:
                step = advance_with_archive(self.edge, self.archive, RECORDING)
                if step.at_end:
                    break
                numbers.append(step.frame.number)
        self.assertEqual(tuple(numbers), (10, 11, 12))
        self.reopen_edge()
        self.archive.close()
        self.archive = ArchiveLink(self.archive_path)
        self.addCleanup(lambda: self.archive.close())
        self.assertEqual(deliver(self.edge, self.archive, RECORDING).remaining, 0)
        self.assertEqual(self.release_positions(), (0, 1, 2))
        a = finalize(self.archive, RECORDING, 'A')
        b = finalize(self.archive, RECORDING, 'B')
        self.assertEqual(a.artifact.frames, FRAMES[:2])
        self.assertEqual(b.artifact.frames, FRAMES[1:])
        self.assertEqual(a.artifact.peak_to_peak, 5)
        self.assertEqual(b.artifact.peak_to_peak, 3)
