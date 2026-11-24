import argparse
import json
import tempfile
from contextlib import ExitStack, closing
from pathlib import Path

from archive.completeness import IncompleteWindow
from instrument.release import read_release_result
from station import (
    ArchiveStore, Frame, ReleasePort, advance, deliver, export_result, finalize,
    get_result, initialize_edge, open_recording, register,
)
from spill_demo import run_demo as spill_run_demo
from storage import LinkUnavailable


EXAMPLES = Path(__file__).resolve().parent / 'examples'


def _read_example(name):
    return json.loads((EXAMPLES / name).read_text(encoding='utf-8'))


def _window_summary(document):
    return {
        'numbers': [frame['number'] for frame in document['frames']],
        'readings': [frame['value'] for frame in document['frames']],
        'origins': [frame['origin'] for frame in document['frames']],
        'peak_to_peak': document['peak_to_peak'],
    }


def run_demo(directory):
    example = _read_example('recording.json')
    oracle = _read_example('expected.json')
    recording_id = example['recording_id']
    lost_frame = example['lost_release_reply']
    edge_path, archive_path = directory / 'edge.sqlite3', directory / 'archive.sqlite3'
    with ExitStack() as resources:
        edge = resources.enter_context(closing(initialize_edge(edge_path)))
        archive = resources.enter_context(ArchiveStore(archive_path))
        open_recording(edge, recording_id, (Frame(**data) for data in example['frames']))
        for request in example['windows']:
            register(edge, recording_id, **request)
        archive.connection.execute('PRAGMA query_only = ON')
        port = ReleasePort(drop_reply_once=(recording_id, lost_frame))
        while True:
            try:
                step = advance(edge, recording_id, issue_release=port)
            except LinkUnavailable:
                if port.drop_reply_once is not None or read_release_result(edge, recording_id, lost_frame) is None:
                    raise
                edge.close()
                edge = resources.enter_context(closing(initialize_edge(edge_path)))
                port = ReleasePort()
                continue
            if step.at_end:
                break
        release_count = edge.execute('SELECT count(*) FROM source_releases WHERE recording_id = ?',
                                     (recording_id,)).fetchone()[0]
        archive.connection.execute('PRAGMA query_only = OFF')
        report = deliver(edge, archive, recording_id)
        if report.blocked is not None or report.remaining:
            raise RuntimeError(f'archive delivery did not finish: {report}')
        a = finalize(archive, recording_id, 'A')
        archive.close()
        archive = resources.enter_context(ArchiveStore(archive_path))
        try:
            b = finalize(archive, recording_id, 'B')
        except IncompleteWindow as error:
            if lost_frame in error.missing:
                raise AssertionError('SHARED_FRAME_MISSING: B lost the shared original frame') from error
            raise
        edge.close()
        actual = {}
        for published in (a, b):
            receipt = published.receipt
            readback = get_result(archive, receipt.result_id)
            if readback != published:
                raise AssertionError('fixed readback differs from publication')
            document = json.loads(export_result(archive, receipt.result_id))
            if document['receipt'] != {'recording_id': recording_id, 'window_id': receipt.window_id,
                                       'result_id': receipt.result_id}:
                raise AssertionError('exported receipt differs from the archive receipt')
            actual[receipt.window_id] = _window_summary(document)
        if lost_frame not in actual['B']['numbers']:
            raise AssertionError('SHARED_FRAME_MISSING: B export omits the shared original frame')
        if actual != oracle['windows']:
            raise AssertionError(f'window exports disagree with hand calculation: {actual}')
        shared_a = actual['A']['origins'][actual['A']['numbers'].index(lost_frame)]
        shared_b = actual['B']['origins'][actual['B']['numbers'].index(lost_frame)]
        downstream = {'shared_origin': shared_b, 'successful_releases': release_count}
        expected_downstream = {'shared_origin': oracle['shared_origin'],
                               'successful_releases': oracle['successful_releases']}
        if shared_a != shared_b or downstream != expected_downstream:
            raise AssertionError(f'original evidence or release count differs: {downstream}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v6.a_then_b_reopen',
        'input': example,
        'expected': oracle['windows'],
        'actual': actual,
        'downstream_expected': expected_downstream,
        'downstream_actual': downstream,
        'code_paths': ['edge/capture.py', 'edge/resume.py', 'edge/delivery.py',
                       'archive/finalize.py', 'records/readback.py', 'records/export.py'],
        'oracle': 'The supplied frames give A = 9 - 4 = 5 and B = 9 - 6 = 3. '
                  'The original frame 11 has one recorded origin and there are three source frames.',
    }, ensure_ascii=False, separators=(',', ':')))


def main():
    parser = argparse.ArgumentParser(description='Run the vibration station recording example')
    parser.add_argument('--state-dir', type=Path, help='reuse two station databases in this directory')
    args = parser.parse_args()
    if args.state_dir is not None:
        args.state_dir.mkdir(parents=True, exist_ok=True)
        run_demo(args.state_dir)
        spill_run_demo(args.state_dir / 'spill')
    else:
        with tempfile.TemporaryDirectory() as directory:
            run_demo(Path(directory))
        with tempfile.TemporaryDirectory() as directory:
            spill_run_demo(Path(directory))


if __name__ == '__main__':
    main()
