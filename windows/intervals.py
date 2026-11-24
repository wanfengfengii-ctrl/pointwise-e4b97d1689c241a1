from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from storage import ContractError


def _validate_bounds(left: int, right: int) -> None:
    if type(left) is not int or type(right) is not int or left > right:
        raise ContractError('window requires an ordered pair of integer endpoints')


@dataclass(frozen=True, slots=True)
class WindowInterval:
    left: int
    right: int
    expected: tuple[int, ...]

    def __post_init__(self) -> None:
        _validate_bounds(self.left, self.right)
        if not isinstance(self.expected, tuple) or not self.expected:
            raise ContractError('window must contain at least one original frame')
        previous = None
        for number in self.expected:
            if type(number) is not int or not self.left <= number <= self.right:
                raise ContractError('expected frame number is outside the window')
            if previous is not None and number <= previous:
                raise ContractError('expected frame numbers must be strictly increasing')
            previous = number

    def contains(self, number: int) -> bool:
        index = bisect_left(self.expected, number)
        return index < len(self.expected) and self.expected[index] == number

    @property
    def first_frame(self) -> int:
        return self.expected[0]


def bind_interval(frame_numbers: tuple[int, ...], left: int, right: int) -> WindowInterval:
    _validate_bounds(left, right)
    if not frame_numbers or left < frame_numbers[0] or right > frame_numbers[-1]:
        raise ContractError('window is outside the recording range')
    start = bisect_left(frame_numbers, left)
    stop = bisect_right(frame_numbers, right)
    return WindowInterval(left, right, frame_numbers[start:stop])
