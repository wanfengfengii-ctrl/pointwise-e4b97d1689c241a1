import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from archive.completeness import IncompleteWindow
from edge import custody
from instrument.read_head import read_head
from instrument.release import read_release_result
from station import (
    ArchiveStore, Frame, advance, deliver, finalize, initialize_edge, open_recording, register,
)
from storage import LinkUnavailable, NotFound


@contextmanager
def block_local_body(recording_id, frame_number):
    original = custody.write_local_body

    def write(connection, current_recording, position, frame):
        if (current_recording, frame.number) == (recording_id, frame_number):
            raise custody.LocalBodyUnavailable('sample local frame body write refused')
        return original(connection, current_recording, position, frame)

    with patch.object(custody, 'write_local_body', write):
        yield


class ArchiveLink(ArchiveStore):
    def __init__(self, path):
        self.online = True
        self.next_fault = None
        super().__init__(path)

    def _require_link(self):
        if not self.online:
            raise LinkUnavailable('sample archive link is down')

    def read_snapshot(self):
        self._require_link()
        return super().read_snapshot()

    @contextmanager
    def write_transaction(self):
        self._require_link()
        fault, self.next_fault = self.next_fault, None
        with super().write_transaction() as connection:
            yield connection
            if fault == 'reject':
                raise sqlite3.OperationalError('sample archive commit refused')
        if fault == 'drop_reply':
            self.online = False
            raise LinkUnavailable('sample archive committed but reply was lost')


def seed(edge):
    source = Path(__file__).with_name('examples') / 'recording.json'
    data = json.loads(source.read_text(encoding='utf-8'))
    recording_id = data['recording_id']
    open_recording(edge, recording_id, (Frame(**frame) for frame in data['frames']))
    for window in data['windows']:
        register(edge, recording_id, **window)
    return recording_id


def check_ports(edge, archive, observer):
    recording_id = seed(edge)
    assert advance(edge, recording_id).frame.number == 10
    archive.next_fault = 'reject'
    report = deliver(edge, archive, recording_id)
    assert report.remaining == 1 and 'commit refused' in (report.blocked or '')
    try:
        finalize(observer, recording_id, 'A')
    except NotFound:
        pass
    else:
        raise AssertionError('rejected archive transaction became visible')

    archive.next_fault = 'drop_reply'
    report = deliver(edge, archive, recording_id)
    assert report.remaining == 1 and 'reply was lost' in (report.blocked or '')
    assert not archive.online
    archive.online = True
    assert deliver(edge, archive, recording_id).remaining == 0

    with block_local_body(recording_id, 11):
        try:
            advance(edge, recording_id)
        except custody.LocalBodyUnavailable:
            pass
        else:
            raise AssertionError('ordinary advance ignored the local body refusal')
        head = read_head(edge, recording_id)
        assert head.frame_number == 11 and read_release_result(edge, recording_id, 11) is None
        try:
            finalize(observer, recording_id, 'A')
        except IncompleteWindow as error:
            missing = error.missing
            assert missing == (11,)
            assert tuple(frame.number for frame in error.progress.frames) == (10,)
        else:
            raise AssertionError('A was signed without frame 11')
        registration = register(edge, recording_id, 'C', 11, 11)
        assert registration.interval.expected == (11,)
    return {'head': head.frame_number, 'A_missing': missing, 'C': registration.interval.expected}


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        archive_path = root / 'archive.sqlite3'
        with (
            closing(initialize_edge(root / 'edge.sqlite3')) as edge,
            ArchiveLink(archive_path) as archive,
            ArchiveStore(archive_path) as observer,
        ):
            print(json.dumps(check_ports(edge, archive, observer)))


if __name__ == '__main__':
    main()
