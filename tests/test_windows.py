from instrument.read_head import read_head, read_next_frame
from instrument.recording import expected_frame_numbers
from instrument.release import read_release_result
from station import Frame, ReleasePort, advance, open_recording, register
from storage import Conflict, ContractError, LateRegistration, LinkUnavailable, NotFound
from tests.support import FRAMES, RECORDING, StationCase
from windows.registrations import list_registrations


class WindowTests(StationCase):
    def test_same_request_retry(self):
        a, b = self.seed()
        self.assertEqual(register(self.edge, RECORDING, 'A', 10, 11), a)
        self.assertEqual(a.interval.expected, (10, 11))
        with self.assertRaises(Conflict):
            register(self.edge, RECORDING, 'A', 10, 12)
        self.assertEqual(list_registrations(self.edge, RECORDING), (a, b))

    def test_recording_retry(self):
        a, _ = self.seed()
        advance(self.edge, RECORDING)
        self.reopen_edge()
        self.assertEqual(open_recording(self.edge, RECORDING, FRAMES).frame_numbers, (10, 11, 12))
        with self.assertRaises(Conflict):
            open_recording(self.edge, RECORDING, (Frame(10, 40, FRAMES[0].origin), *FRAMES[1:]))
        with self.assertRaises(Conflict):
            open_recording(self.edge, RECORDING, (FRAMES[0], FRAMES[0]))
        self.assertEqual(expected_frame_numbers(self.edge, RECORDING), (10, 11, 12))
        self.assertEqual(read_next_frame(self.edge, RECORDING), FRAMES[1])
        self.assertEqual(register(self.edge, RECORDING, 'A', 10, 11), a)

    def test_retry_after_capture(self):
        a, b = self.seed()
        self.assertEqual(advance(self.edge, RECORDING).frame, FRAMES[0])
        self.assertEqual(register(self.edge, RECORDING, 'A', 10, 11), a)
        with self.assertRaises(Conflict):
            register(self.edge, RECORDING, 'A', 10, 12)
        self.assertEqual(list_registrations(self.edge, RECORDING), (a, b))

    def test_late_registration(self):
        a, b = self.seed()
        advance(self.edge, RECORDING)
        with self.assertRaises(LateRegistration):
            register(self.edge, RECORDING, 'late', 10, 12)
        self.assertEqual(list_registrations(self.edge, RECORDING), (a, b))
        upcoming = register(self.edge, RECORDING, 'upcoming', 11, 11)
        self.assertEqual(upcoming.interval.first_frame, 11)

    def test_custody_before_release(self):
        a, b = self.seed()
        port = ReleasePort(hold_once=(RECORDING, 10))
        with self.assertRaises(LinkUnavailable):
            advance(self.edge, RECORDING, issue_release=port)
        self.assertIsNone(read_release_result(self.edge, RECORDING, 10))
        self.assertEqual(read_head(self.edge, RECORDING).frame_number, 10)
        with self.assertRaises(LateRegistration):
            register(self.edge, RECORDING, 'C', 10, 10)
        self.assertEqual(list_registrations(self.edge, RECORDING), (a, b))
        self.assertEqual(advance(self.edge, RECORDING, issue_release=port).frame, FRAMES[1])
        self.assertEqual(self.release_positions(), (0, 1))

    def test_sparse_and_invalid_intervals(self):
        open_recording(self.edge, 'sparse', (Frame(-2, 0, 's/1'), Frame(7, 4, 's/2'), Frame(13, -2, 's/3')))
        window = register(self.edge, 'sparse', 'gap', -1, 13)
        self.assertEqual(window.interval.expected, (7, 13))
        for left, right in ((-3, 7), (7, 14), (8, 7), (-1, 6), (False, 7)):
            with self.subTest(left=left, right=right), self.assertRaises(ContractError):
                register(self.edge, 'sparse', 'invalid', left, right)
        with self.assertRaises(NotFound):
            register(self.edge, 'unknown', 'A', 0, 0)
        self.assertEqual(list_registrations(self.edge, 'sparse'), (window,))

    def test_invalid_and_empty_recording(self):
        for frame in (Frame(1, None, 'x'), Frame(1, True, 'x'), Frame(1, 0, ''), Frame(True, 0, 'x')):
            with self.subTest(frame=frame), self.assertRaises(ContractError):
                open_recording(self.edge, 'invalid', (frame,))
        with self.assertRaises(NotFound):
            expected_frame_numbers(self.edge, 'invalid')
        open_recording(self.edge, 'empty', ())
        self.assertTrue(advance(self.edge, 'empty').at_end)
        self.assertEqual(self.release_positions('empty'), ())
        with self.assertRaises(ContractError):
            register(self.edge, 'empty', 'A', 0, 0)
