import json
import sqlite3
from contextlib import contextmanager


class ContractError(ValueError):
    pass


class Conflict(ContractError):
    pass


class LateRegistration(ContractError):
    pass


class NotFound(LookupError):
    pass


class LinkUnavailable(RuntimeError):
    pass


def require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ContractError(f'{name} must be nonempty text')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ContractError(f'{name} must be valid UTF-8 text') from error


def encode_integer(value: int) -> str:
    return format(value, 'x')


def decode_integer(value: str) -> int:
    return int(value, 16)


def pack_numbers(numbers: tuple[int, ...]) -> str:
    return json.dumps([encode_integer(number) for number in numbers], separators=(',', ':'))


def unpack_numbers(encoded: str) -> tuple[int, ...]:
    return tuple(decode_integer(number) for number in json.loads(encoded))


def connect(path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA synchronous = FULL')
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection, *, write: bool = False):
    connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
