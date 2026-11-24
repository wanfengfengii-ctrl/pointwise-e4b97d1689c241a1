from dataclasses import dataclass

from storage import ContractError, require_text


@dataclass(frozen=True, slots=True)
class Frame:
    number: int
    value: int
    origin: str


def validate_frame(frame: Frame) -> None:
    if not isinstance(frame, Frame):
        raise ContractError('expected an original Frame')
    if type(frame.number) is not int or type(frame.value) is not int:
        raise ContractError('frame number and reading must be integers')
    require_text(frame.origin, 'frame origin')


def same_frame(left: Frame, right: Frame) -> bool:
    return left == right
