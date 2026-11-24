from dataclasses import dataclass

from edge.custody import find_unconfirmed_release, persist_custody
from edge.resume import resume_release
from instrument.frames import Frame
from instrument.read_head import read_head, read_next_frame
from instrument.release import ReleaseResult, release_frame
from storage import Conflict, transaction
from windows.coverage import covering_windows
from windows.frontier import advance_frontier


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    frame: Frame | None
    release: ReleaseResult | None

    @property
    def at_end(self) -> bool:
        return self.frame is None


def advance(connection, recording_id: str, *, issue_release=release_frame) -> AdvanceResult:
    while True:
        with transaction(connection, write=True):
            pending = find_unconfirmed_release(connection, recording_id)
            if pending is None:
                head = read_head(connection, recording_id)
                if head.at_end:
                    return AdvanceResult(None, None)
                frame = read_next_frame(connection, recording_id)
                if frame is None or frame.number != head.frame_number:
                    raise Conflict('read frame does not match the source head')
                windows = covering_windows(connection, recording_id, frame.number)
                captured = persist_custody(connection, recording_id, head.position, frame, windows)
                advance_frontier(connection, recording_id, frame.number)
        # Source commands run only after the custody and admission transaction commits.
        if pending is not None:
            resume_release(connection, pending, issue_release=issue_release)
            continue
        result = resume_release(connection, captured, issue_release=issue_release)
        return AdvanceResult(captured.frame, result)
