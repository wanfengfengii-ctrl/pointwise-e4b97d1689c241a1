import sqlite3
from dataclasses import dataclass

from archive.intake import DeliveryMaterial, IntakeResult, accept_delivery
from archive.spill import read_spilled
from edge.custody import list_custody_windows, load_custody
from edge.obligations import list_pending_obligations, mark_obligation_accepted
from instrument.recording import load_recording
from storage import Conflict, ContractError, LinkUnavailable, transaction


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    accepted: tuple[IntakeResult, ...]
    remaining: int
    blocked: str | None


def _material(connection, archive, recording_id: str, obligation) -> DeliveryMaterial:
    custody = load_custody(connection, recording_id, obligation.frame_number)
    if custody is None:
        raise Conflict('a pending delivery has no original frame in custody')
    registration = obligation.registration
    if not custody.body_spilled:
        return DeliveryMaterial(registration, custody.frame, custody.position)
    # The reading and origin live only in the durable archive spill.  The
    # archive must still hold the same original at the same source position,
    # and this window must have been authorized by the formal takeover.
    spilled = read_spilled(archive, recording_id, obligation.frame_number)
    if spilled is None:
        raise LinkUnavailable('spilled original is no longer available in the archive')
    if spilled.frame.number != obligation.frame_number or spilled.position != custody.position:
        raise Conflict('spilled original does not match the formal takeover record')
    authorized = list_custody_windows(connection, recording_id, obligation.frame_number)
    return DeliveryMaterial(registration, spilled.frame, custody.position, authorized)


def deliver(connection, archive, recording_id: str, *, limit=None, intake=accept_delivery) -> DeliveryReport:
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ContractError('delivery limit must be a nonnegative integer')
    load_recording(connection, recording_id)
    pending = list_pending_obligations(connection, recording_id)
    accepted = []
    blocked = None
    for obligation in pending[:limit]:
        try:
            material = _material(connection, archive, recording_id, obligation)
            result = intake(archive, material)
        except (sqlite3.Error, LinkUnavailable) as error:
            blocked = f'{type(error).__name__}: {error}'
            break
        expected = IntakeResult(recording_id, obligation.registration.window_id, obligation.frame_number)
        if result != expected:
            blocked = 'archive acknowledgment does not match the pending delivery'
            break
        with transaction(connection, write=True):
            mark_obligation_accepted(connection, obligation)
        accepted.append(result)
    remaining = len(list_pending_obligations(connection, recording_id))
    return DeliveryReport(tuple(accepted), remaining, blocked)
