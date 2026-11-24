from dataclasses import dataclass

from archive.evidence import store_window_definition, store_window_frame
from archive.spill import verify_spilled_original
from archive.store import ArchiveStore
from instrument.frames import Frame, validate_frame
from storage import ContractError
from windows.registrations import WindowRegistration


@dataclass(frozen=True, slots=True)
class DeliveryMaterial:
    registration: WindowRegistration
    frame: Frame
    position: int | None = None
    authorized_windows: tuple[str, ...] | None = None


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
        # If this original was spilled durably, the archive reconciles it
        # before any window receipts it: same original, same source position,
        # and a window the formal takeover actually authorized.  Ordinary
        # local captures have no spill row and pass through unchanged.
        verify_spilled_original(connection, registration.recording_id, frame,
                                material.position, registration.window_id,
                                material.authorized_windows)
        store_window_definition(connection, registration)
        store_window_frame(connection, registration, frame)
    return IntakeResult(registration.recording_id, registration.window_id, frame.number)
