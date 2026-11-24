from dataclasses import dataclass

from archive.spill import spill_frame
from edge.custody import (
    LocalBodyUnavailable, find_unconfirmed_release, persist_custody, persist_spill_metadata,
)
from edge.resume import resume_release
from instrument.frames import Frame
from instrument.read_head import read_head, read_next_frame
from instrument.release import ReleaseResult, release_frame
from storage import Conflict, LinkUnavailable, transaction
from windows.coverage import covering_windows
from windows.frontier import advance_frontier


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    frame: Frame | None
    release: ReleaseResult | None

    @property
    def at_end(self) -> bool:
        return self.frame is None


class _LocalBodyRefused(Exception):
    def __init__(self, error: LocalBodyUnavailable, frame: Frame, position: int):
        self.error = error
        self.frame = frame
        self.position = position
        super().__init__('the local frame body refused the write')


def _take_next_frame(connection, recording_id: str) -> tuple[Frame, int, tuple]:
    """Read the source frame once together with its position and covering windows."""
    head = read_head(connection, recording_id)
    frame = read_next_frame(connection, recording_id)
    if frame is None or frame.number != head.frame_number:
        raise Conflict('read frame does not match the source head')
    return frame, head.position, covering_windows(connection, recording_id, frame.number)


def _local_takeover(connection, recording_id: str, *, spill_on_refusal: bool = False):
    """Take over the next source frame using only the local body.

    Returns ``('end', None)`` at the recording tail, ``('pending', custody)``
    when an earlier release fact still has to be confirmed, or
    ``('captured', custody)`` after a successful local body write.  With
    ``spill_on_refusal`` a refused local body is reported as
    ``_LocalBodyRefused`` so the caller can spill; otherwise it propagates.
    """
    with transaction(connection, write=True):
        pending = find_unconfirmed_release(connection, recording_id)
        if pending is not None:
            return 'pending', pending
        head = read_head(connection, recording_id)
        if head.at_end:
            return 'end', None
        frame, position, windows = _take_next_frame(connection, recording_id)
        try:
            captured = persist_custody(connection, recording_id, position, frame, windows)
        except LocalBodyUnavailable as error:
            # Abort this takeover: the metadata body write rolled back and the
            # source frame stays at the head.
            if spill_on_refusal:
                raise _LocalBodyRefused(error, frame, position) from error
            raise
        advance_frontier(connection, recording_id, frame.number)
        return 'captured', captured


def advance(connection, recording_id: str, *, issue_release=release_frame) -> AdvanceResult:
    while True:
        status, captured = _local_takeover(connection, recording_id)
        # Source commands run only after the custody and admission transaction commits.
        if status == 'pending':
            resume_release(connection, captured, issue_release=issue_release)
            continue
        if status == 'end':
            return AdvanceResult(None, None)
        result = resume_release(connection, captured, issue_release=issue_release)
        return AdvanceResult(captured.frame, result)


def advance_with_archive(connection, archive, recording_id: str, *,
                         issue_release=release_frame) -> AdvanceResult:
    """Advance the source head, spilling the original body to the archive on demand.

    The ordinary local path is unchanged.  Only when the local frame body
    reports ``LocalBodyUnavailable`` is the original copied durably into the
    archive; after that the edge records its own formal takeover and delivery
    duties, then releases the source frame exactly as usual.
    """
    while True:
        try:
            status, captured = _local_takeover(connection, recording_id, spill_on_refusal=True)
        except _LocalBodyRefused as refusal:
            captured = _spill_takeover(connection, archive, recording_id,
                                       refusal.frame, refusal.position)
            result = resume_release(connection, captured, issue_release=issue_release)
            return AdvanceResult(refusal.frame, result)
        if status == 'pending':
            resume_release(connection, captured, issue_release=issue_release)
            continue
        if status == 'end':
            return AdvanceResult(None, None)
        result = resume_release(connection, captured, issue_release=issue_release)
        return AdvanceResult(captured.frame, result)


def _spill_takeover(connection, archive, recording_id: str, frame: Frame,
                    position: int):
    """Durably spill the original, then record the formal takeover on the edge.

    ``spill_frame`` raises ``LinkUnavailable`` when the original cannot be
    confirmed durable; in that case nothing on the edge has changed and the
    source frame remains pending release.
    """
    spill_frame(archive, recording_id, position, frame)
    with transaction(connection, write=True):
        head = read_head(connection, recording_id)
        current = read_next_frame(connection, recording_id)
        if (head.position, current) != (position, frame):
            raise LinkUnavailable('source head moved while the original was being spilled')
        # Registrations accepted during the spill wait are frozen now, at the
        # moment of the formal takeover.
        windows = covering_windows(connection, recording_id, frame.number)
        captured = persist_spill_metadata(connection, recording_id, position,
                                          frame.number, windows)
        advance_frontier(connection, recording_id, frame.number)
        return captured
