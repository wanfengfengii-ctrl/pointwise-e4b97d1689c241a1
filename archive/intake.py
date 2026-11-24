from dataclasses import dataclass

from archive.evidence import store_window_definition, store_window_frame
from archive.store import ArchiveStore
from instrument.frames import Frame, validate_frame
from storage import ContractError
from windows.registrations import WindowRegistration


@dataclass(frozen=True, slots=True)
class DeliveryMaterial:
    registration: WindowRegistration
    frame: Frame


@dataclass(frozen=True, slots=True)
class IntakeResult:
    recording_id: str
    window_id: str
    frame_number: int


def accept_delivery(store: ArchiveStore, material: DeliveryMaterial) -> IntakeResult:
    registration = material.registration
    if not isinstance(registration, WindowRegistration):
        raise ContractError('delivery requires a frozen registration')
    frame = material.frame
    validate_frame(frame)
    if not registration.interval.contains(frame.number):
        raise ContractError('delivered frame is outside the expected window evidence')
    with store.write_transaction() as connection:
        store_window_definition(connection, registration)
        store_window_frame(connection, registration, frame)
    return IntakeResult(registration.recording_id, registration.window_id, frame.number)
