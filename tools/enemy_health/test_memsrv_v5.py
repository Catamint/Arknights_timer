"""memsrv v5 动态守卫事务的主机端协议测试。"""

import struct

import pytest

from tools.enemy_health.memcore import TcpChannel


class _FakeSocket:
    def __init__(self, response: bytes):
        self._response = bytearray(response)
        self.sent = bytearray()

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, size: int) -> bytes:
        if not self._response:
            return b''
        chunk = bytes(self._response[:size])
        del self._response[:size]
        return chunk

    def setsockopt(self, *_args) -> None:
        pass

    def settimeout(self, _timeout) -> None:
        pass

    def shutdown(self, _how) -> None:
        pass

    def close(self) -> None:
        pass


def _channel_with_response(response: bytes) -> TcpChannel:
    channel = TcpChannel(object())
    channel.sock = _FakeSocket(response)
    channel.mode = 'srv'
    channel.srv_version = TcpChannel.PROTOCOL_VERSION
    return channel


def _guarded_response(attempts: int, start: int, end: int,
                      payloads: list[bytes]) -> bytes:
    return (
        struct.pack('<QQQ', attempts, start, end)
        + struct.pack('<Q', len(payloads))
        + struct.pack(f'<{len(payloads)}q', *(len(data) for data in payloads))
        + b''.join(payloads)
    )


def test_v5_banner_and_protocol_version_are_exclusive():
    assert TcpChannel.PROTOCOL_VERSION == 5
    assert TcpChannel.BANNER == b'AKMSRV5\n'
    assert not hasattr(TcpChannel, 'BANNER_V4')


def test_client_rejects_v4_handshake(monkeypatch):
    old_server = _FakeSocket(b'AKMSRV4\n')
    monkeypatch.setattr(
        'tools.enemy_health.memcore.socket.create_connection',
        lambda *_args, **_kwargs: old_server)
    channel = TcpChannel(object())

    with pytest.raises(IOError, match='仅支持 memsrv v5'):
        channel._connect_once()

    assert channel.sock is None
    assert channel.srv_version == 0


def test_array_operations_encode_offsets_and_selected_pointer_result():
    operations = [
        ('direct', 0x12340000, 0x80),
        ('array_ptr', 0, 0x28, 0x18),
        ('deref', 1, 0, 0x10, 0x30),
        ('array_deref', 0, 0x28, 0x18, 0x10, 0x30),
    ]
    encoded, sizes = TcpChannel._encode_operations(operations)

    assert sizes == [0x80, 8, 0x30, 0x30]
    kind, ref, value, packed_offsets, size = struct.unpack(
        '<IIqqQ', encoded[1])
    assert (kind, ref, value, size) == (3, 0, 0, 8)
    assert packed_offsets & 0xFFFFFFFF == 0x28
    assert (packed_offsets >> 32) & 0xFFFFFFFF == 0x18

    kind, ref, value, packed_offsets, size = struct.unpack(
        '<IIqqQ', encoded[3])
    assert (kind, ref, value, size) == (2, 0, 0x10, 0x30)
    assert packed_offsets & 0xFFFFFFFF == 0x28
    assert (packed_offsets >> 32) & 0xFFFFFFFF == 0x18


def test_guarded_transaction_sends_one_shot_header_and_returns_complete_guard():
    checkpoint_ptr = struct.pack('<Q', 0x77770000)
    response = _guarded_response(2, 9123, 9123, [b'CURS', checkpoint_ptr])
    channel = _channel_with_response(response)
    operations = [
        ('direct', 0x100000, 4),
        ('array_ptr', 0, 0, 0),
    ]

    results, guard = channel.guarded_transaction_read(
        operations, 0xABC000, guard_size=4, max_attempts=8)

    assert results == [b'CURS', checkpoint_ptr]
    assert guard == {
        'attempts': 2,
        'start': 9123,
        'end': 9123,
        'complete': True,
    }
    magic, count, guard_addr, guard_size, max_attempts = struct.unpack_from(
        '<QQQII', channel.sock.sent, 0)
    assert magic == TcpChannel.GUARDED_TXN_MAGIC
    assert (count, guard_addr, guard_size, max_attempts) == (
        2, 0xABC000, 4, 8)
    assert channel.frame_stats()['batches'] == 1
    assert channel.frame_stats()['requests'] == 2


def test_guarded_transaction_marks_exhausted_retry_as_incomplete():
    response = _guarded_response(8, 100, 101, [b'DATA'])
    channel = _channel_with_response(response)

    results, guard = channel.guarded_transaction_read(
        [('direct', 0x100000, 4)], 0xABC000, max_attempts=8)

    assert results == [b'DATA']
    assert guard == {
        'attempts': 8,
        'start': 100,
        'end': 101,
        'complete': False,
    }


@pytest.mark.parametrize('operation', [
    ('array_ptr', 1, 0x28, 0x18),
    ('array_ptr', 0, -1, 0x18),
    ('array_ptr', 0, 0x28, -1),
    ('array_deref', 0, 0x28, 0x18, 0, 0),
    ('direct', 0x1000, TcpChannel.MAX_REQUEST_SIZE + 1),
])
def test_operation_encoder_rejects_invalid_reference_offsets_and_sizes(operation):
    with pytest.raises(ValueError):
        TcpChannel._encode_operations([('direct', 0x1000, 0x40), operation])


@pytest.mark.parametrize(('guard_addr', 'guard_size', 'attempts'), [
    (0, 4, 1),
    (0x1000, 2, 1),
    (0x1000, 4, 0),
    (0x1000, 4, TcpChannel.MAX_GUARD_ATTEMPTS + 1),
])
def test_guarded_transaction_rejects_invalid_guard_bounds(
        guard_addr, guard_size, attempts):
    channel = _channel_with_response(b'')
    with pytest.raises(ValueError):
        channel.guarded_transaction_read(
            [('direct', 0x2000, 4)], guard_addr,
            guard_size=guard_size, max_attempts=attempts)
