import tempfile
import unittest
from pathlib import Path

from instrument.read_head import read_head
from station import ArchiveStore, Frame, advance, initialize_edge, open_recording, register


RECORDING = 'shift-31'
FRAMES = (
    Frame(10, 4, 'recorder/31/10'),
    Frame(11, 9, 'recorder/31/11'),
    Frame(12, 6, 'recorder/31/12'),
)


class StationCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.edge_path = Path(directory.name) / 'edge.sqlite3'
        self.archive_path = Path(directory.name) / 'archive.sqlite3'
        self.edge = initialize_edge(self.edge_path)
        self.addCleanup(lambda: self.edge.close())
        self.archive = ArchiveStore(self.archive_path)
        self.addCleanup(lambda: self.archive.close())

    def reopen_edge(self):
        self.edge.close()
        self.edge = initialize_edge(self.edge_path)

    def reopen_archive(self):
        self.archive.close()
        self.archive = ArchiveStore(self.archive_path)

    def seed(self):
        open_recording(self.edge, RECORDING, FRAMES)
        a = register(self.edge, RECORDING, 'A', 10, 11)
        b = register(self.edge, RECORDING, 'B', 11, 12)
        return a, b

    def drain_source(self, recording_id=RECORDING):
        numbers = []
        while not read_head(self.edge, recording_id).at_end:
            result = advance(self.edge, recording_id)
            self.assertFalse(result.at_end)
            numbers.append(result.frame.number)
        self.assertTrue(advance(self.edge, recording_id).at_end)
        return tuple(numbers)

    def release_positions(self, recording_id=RECORDING):
        rows = self.edge.execute('''
            SELECT position FROM source_releases WHERE recording_id = ? ORDER BY position
        ''', (recording_id,))
        return tuple(row['position'] for row in rows)
