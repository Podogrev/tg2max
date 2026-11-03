import struct

import msgpack

from tg2max.max_proto import _lz4_block_decompress, pack_frame, unpack_frame


def test_pack_unpack_roundtrip():
    payload = {"chatId": -123, "message": {"text": "привет 🎃", "attaches": []}}
    raw = pack_frame(0, 7, 64, payload)
    ver, cmd, seq, op, packed = struct.unpack_from(">BBHHI", raw)
    assert (ver, cmd, seq, op) == (10, 0, 7, 64)
    assert packed >> 24 == 0 and packed & 0xFFFFFF == len(raw) - 10
    f = unpack_frame(raw)
    assert f == {"cmd": 0, "seq": 7, "opcode": 64, "payload": payload}


def test_empty_payload():
    f = unpack_frame(pack_frame(1, 3, 1, None))
    assert f["payload"] is None and f["opcode"] == 1 and f["cmd"] == 1


def test_lz4_literals_and_match():
    # token 0x35: 3 literals "abc", then a 9-byte match at offset 3
    assert _lz4_block_decompress(bytes([0x35]) + b"abc" + bytes([3, 0])) == b"abcabcabcabc"


def test_lz4_long_literal_and_long_match():
    lit = b"x" * 20  # 15 + 5 extra
    block = bytes([0xFF, 5]) + lit + bytes([1, 0, 1])  # match len 15+4+1 = 20 at offset 1
    assert _lz4_block_decompress(block) == b"x" * 40


def test_compressed_frame_and_wrapped_ext():
    inner = msgpack.packb(12345)
    body = msgpack.packb({"id": msgpack.ExtType(1, inner), "t": "abcabcabcabc"}, use_bin_type=True)
    # store the body as an LZ4 block of pure literals (valid LZ4)
    n = len(body)
    lit_hdr = bytes([0xF0]) + bytes([255] * ((n - 15) // 255)) + bytes([(n - 15) % 255])
    block = lit_hdr + body
    raw = struct.pack(">BBHHI", 10, 1, 2, 64, (2 << 24) | len(block)) + block
    f = unpack_frame(raw)
    assert f["payload"] == {"id": 12345, "t": "abcabcabcabc"}
