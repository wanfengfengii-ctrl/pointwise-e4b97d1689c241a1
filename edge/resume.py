from edge.custody import CustodyRecord, mark_release_confirmed
from instrument.read_head import read_head
from instrument.release import ReleaseResult, read_release_result, release_frame
from storage import LinkUnavailable, transaction


def resume_release(connection, custody: CustodyRecord, *, issue_release=release_frame) -> ReleaseResult:
    recording_id = custody.recording_id
    number = custody.frame.number
    expected = ReleaseResult(recording_id, number, custody.position)
    with transaction(connection):
        head = read_head(connection, recording_id)
        result = read_release_result(connection, recording_id, number)

    if result is None:
        if (head.position, head.frame_number) != (custody.position, number):
            raise LinkUnavailable('source head moved without a matching release fact')
        if issue_release(connection, recording_id, number) != expected:
            raise LinkUnavailable('release response does not match the custody record')
        with transaction(connection):
            head = read_head(connection, recording_id)
            result = read_release_result(connection, recording_id, number)

    if result != expected or head.position <= custody.position:
        raise LinkUnavailable('matching source release is not yet confirmed')
    with transaction(connection, write=True):
        mark_release_confirmed(connection, custody)
    return expected
