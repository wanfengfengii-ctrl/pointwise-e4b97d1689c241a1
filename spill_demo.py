"""Demonstrate the archive spill: local body refused, durable spill, resume.

Runs the contract sample from ``ARCHIVE_SPILL.md`` against the real two-database
handover and reports the three windows' claimed evidence.  ``demo.py`` calls
``run_demo`` so the run manifest's single demo entry exercises the spill path.
"""
import json
import tempfile
from contextlib import ExitStack, closing
from pathlib import Path

from archive.completeness import inspect_window_progress
from archive.spill import read_spilled
from edge.custody import list_custody_windows, load_custody
from instrument.read_head import read_head
from instrument.release import read_release_result
from station import (
    ArchiveStore, Frame, advance_with_archive, deliver, export_result, finalize,
    get_result, initialize_edge, open_recording, register,
)
from spill_scenario import ArchiveLink, block_local_body
from storage import LinkUnavailable

EXAMPLES = Path(__file__).resolve().parent / 'examples'

EXPECTED = {
    'A': {'numbers': [10, 11], 'readings': [4, 9],
          'origins': ['recorder/31/10', 'recorder/31/11'], 'peak_to_peak': 5},
    'B': {'numbers': [11, 12], 'readings': [9, 6],
          'origins': ['recorder/31/11', 'recorder/31/12'], 'peak_to_peak': 3},
    'C': {'numbers': [11], 'readings': [9], 'origins': ['recorder/31/11'], 'peak_to_peak': 0},
}


def _example():
    return json.loads((EXAMPLES / 'recording.json').read_text(encoding='utf-8'))


def _window_summary(document):
    return {
        'numbers': [frame['number'] for frame in document['frames']],
        'readings': [frame['value'] for frame in document['frames']],
        'origins': [frame['origin'] for frame in document['frames']],
        'peak_to_peak': document['peak_to_peak'],
    }


def run_demo(directory):
    example = _example()
    recording_id = example['recording_id']
    directory.mkdir(parents=True, exist_ok=True)
    edge_path, archive_path = directory / 'edge.sqlite3', directory / 'archive.sqlite3'
    with ExitStack() as resources:
        live = {'edge': resources.enter_context(closing(initialize_edge(edge_path))),
                'archive': resources.enter_context(ArchiveLink(archive_path))}
        observer = resources.enter_context(ArchiveStore(archive_path))

        def reopen():
            live['edge'].close()
            live['archive'].close()
            live['edge'] = resources.enter_context(closing(initialize_edge(edge_path)))
            live['archive'] = resources.enter_context(ArchiveLink(archive_path))

        edge, archive = live['edge'], live['archive']
        open_recording(edge, recording_id, (Frame(**data) for data in example['frames']))
        for request in example['windows']:
            register(edge, recording_id, **request)

        # A state directory may already hold a completed demonstration; then
        # the work is simply claimed again instead of captured twice.
        replay = read_head(edge, recording_id).at_end
        if replay and read_spilled(observer, recording_id, 11) is None:
            raise AssertionError('a finished recording has no durable spilled frame 11')
        if not replay:
            _capture_with_spill(live, observer, recording_id, reopen)
        edge, archive = live['edge'], live['archive']
        c_window = register(edge, recording_id, 'C', 11, 11)
        if c_window.interval.expected != (11,):
            raise AssertionError('registration during the spill wait was not accepted')

        release_count = edge.execute(
            'SELECT count(*) FROM source_releases WHERE recording_id = ?', (recording_id,)
        ).fetchone()[0]
        report = deliver(edge, archive, recording_id)
        if report.blocked is not None or report.remaining:
            raise RuntimeError(f'archive delivery did not finish: {report}')

        finalized = {name: finalize(archive, recording_id, name) for name in ('A', 'B', 'C')}
        edge.close()
        archive.close()
        archive = resources.enter_context(ArchiveStore(archive_path))

        actual = {}
        for name in ('A', 'B', 'C'):
            receipt = finalized[name].receipt
            readback = get_result(archive, receipt.result_id)
            if readback != finalized[name]:
                raise AssertionError('fixed readback differs from publication')
            document = json.loads(export_result(archive, receipt.result_id))
            if document['receipt'] != {'recording_id': recording_id, 'window_id': name,
                                       'result_id': receipt.result_id}:
                raise AssertionError('exported receipt differs from the archive receipt')
            actual[name] = _window_summary(document)

        if actual != EXPECTED:
            raise AssertionError(f'window exports disagree with the hand calculation: {actual}')
    print('POINTWISE_SPILL_SCENARIO ' + json.dumps({
        'case_id': 'spill_then_resume_a_b_c',
        'input': example,
        'expected': EXPECTED,
        'actual': actual,
        'spill_peak_to_peak': {name: actual[name]['peak_to_peak'] for name in ('A', 'B', 'C')},
        'shared_origin': actual['B']['origins'][actual['B']['numbers'].index(11)],
        'successful_releases': release_count,
        'code_paths': ['edge/capture.py', 'edge/custody.py', 'archive/spill.py',
                       'edge/delivery.py', 'archive/intake.py', 'archive/finalize.py'],
        'oracle': 'A = 9 - 4 = 5, B = 9 - 6 = 3, C = 9 - 9 = 0. Frame 11 is spilled once, '
                  'released once, and each of A, B, C claims origin recorder/31/11.',
    }, ensure_ascii=False, separators=(',', ':')))


def _capture_with_spill(live, observer, recording_id, reopen):
    """Spill frame 11 twice (refused commit, then lost reply), then resume."""
    edge, archive = live['edge'], live['archive']
    first = advance_with_archive(edge, archive, recording_id)
    if first.frame.number != 10:
        raise AssertionError('ordinary frame 10 was not captured first')
    if deliver(edge, archive, recording_id).remaining != 0:
        raise AssertionError('frame 10 was not delivered before the spill')

    # Frame 11: the local body refuses and the first spill commit is refused.
    archive.next_fault = 'reject'
    with block_local_body(recording_id, 11):
        try:
            advance_with_archive(edge, archive, recording_id)
        except LinkUnavailable:
            pass
        else:
            raise AssertionError('refused spill was treated as durable')
    if read_head(edge, recording_id).frame_number != 11:
        raise AssertionError('refused spill moved the source head')
    if read_release_result(edge, recording_id, 11) is not None:
        raise AssertionError('refused spill released the source frame')

    # Frame 11 again: the spill commits but the reply is lost.
    archive.next_fault = 'drop_reply'
    with block_local_body(recording_id, 11):
        try:
            advance_with_archive(edge, archive, recording_id)
        except LinkUnavailable:
            pass
        else:
            raise AssertionError('lost spill reply was treated as confirmed')
    if read_head(edge, recording_id).frame_number != 11:
        raise AssertionError('lost spill reply moved the source head')
    spilled = read_spilled(observer, recording_id, 11)
    if spilled is None or spilled.frame != Frame(11, 9, 'recorder/31/11'):
        raise AssertionError('the durable spill is not observable from another connection')
    with observer.read_snapshot() as connection:
        progress = inspect_window_progress(connection, recording_id, 'A')
    if progress.missing != (11,):
        raise AssertionError('a committed spill completed window A by itself')

    # Registration proceeds while the spill waits and the link is down.
    register(edge, recording_id, 'C', 11, 11)

    # Reopen both databases and continue the same takeover.
    reopen()
    edge, archive = live['edge'], live['archive']
    with block_local_body(recording_id, 11):
        resumed = advance_with_archive(edge, archive, recording_id)
    if resumed.frame.number != 11 or not load_custody(edge, recording_id, 11).body_spilled:
        raise AssertionError('resumed takeover did not use the durable spill')
    if list_custody_windows(edge, recording_id, 11) != ('A', 'B', 'C'):
        raise AssertionError('formal takeover did not freeze every covering window')
    if advance_with_archive(edge, archive, recording_id).frame.number != 12:
        raise AssertionError('capture did not continue after the spill')
    if not advance_with_archive(edge, archive, recording_id).at_end:
        raise AssertionError('capture did not reach the recording tail')


def main():
    with tempfile.TemporaryDirectory() as directory:
        run_demo(Path(directory))


if __name__ == '__main__':
    main()
