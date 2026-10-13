"""Zhihu GET request signing, using the ZSE v4 block cipher.

Encryption adapted from ANTiqiuSama/zhihu-cli (MIT), revision
8682ccadf2c3d35fd317ca4d6cf507b613fec381, content/utils/zse.py.
https://github.com/ANTiqiuSama/zhihu-cli
This module performs no network or file access and never logs credential inputs.

MIT License

Copyright (c) 2026 Kris

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import hashlib
import struct

ZSE93 = "101_3_3.0"

_ZK = (
    1170614578,
    1024848638,
    1413669199,
    3951632832,
    3528873006,
    2921909214,
    4151847688,
    3997739139,
    1933479194,
    3323781115,
    3888513386,
    460404854,
    3747539722,
    2403641034,
    2615871395,
    2119585428,
    2265697227,
    2035090028,
    2773447226,
    4289380121,
    4217216195,
    2200601443,
    3051914490,
    1579901135,
    1321810770,
    456816404,
    2903323407,
    4065664991,
    330002838,
    3506006750,
    363569021,
    2347096187,
)

_ZB = (
    20,
    223,
    245,
    7,
    248,
    2,
    194,
    209,
    87,
    6,
    227,
    253,
    240,
    128,
    222,
    91,
    237,
    9,
    125,
    157,
    230,
    93,
    252,
    205,
    90,
    79,
    144,
    199,
    159,
    197,
    186,
    167,
    39,
    37,
    156,
    198,
    38,
    42,
    43,
    168,
    217,
    153,
    15,
    103,
    80,
    189,
    71,
    191,
    97,
    84,
    247,
    95,
    36,
    69,
    14,
    35,
    12,
    171,
    28,
    114,
    178,
    148,
    86,
    182,
    32,
    83,
    158,
    109,
    22,
    255,
    94,
    238,
    151,
    85,
    77,
    124,
    254,
    18,
    4,
    26,
    123,
    176,
    232,
    193,
    131,
    172,
    143,
    142,
    150,
    30,
    10,
    146,
    162,
    62,
    224,
    218,
    196,
    229,
    1,
    192,
    213,
    27,
    110,
    56,
    231,
    180,
    138,
    107,
    242,
    187,
    54,
    120,
    19,
    44,
    117,
    228,
    215,
    203,
    53,
    239,
    251,
    127,
    81,
    11,
    133,
    96,
    204,
    132,
    41,
    115,
    73,
    55,
    249,
    147,
    102,
    48,
    122,
    145,
    106,
    118,
    74,
    190,
    29,
    16,
    174,
    5,
    177,
    129,
    63,
    113,
    99,
    31,
    161,
    76,
    246,
    34,
    211,
    13,
    60,
    68,
    207,
    160,
    65,
    111,
    82,
    165,
    67,
    169,
    225,
    57,
    112,
    244,
    155,
    51,
    236,
    200,
    233,
    58,
    61,
    47,
    100,
    137,
    185,
    64,
    17,
    70,
    234,
    163,
    219,
    108,
    170,
    166,
    59,
    149,
    52,
    105,
    24,
    212,
    78,
    173,
    45,
    0,
    116,
    226,
    119,
    136,
    206,
    135,
    175,
    195,
    25,
    92,
    121,
    208,
    126,
    139,
    3,
    75,
    141,
    21,
    130,
    98,
    241,
    40,
    154,
    66,
    184,
    49,
    181,
    46,
    243,
    88,
    101,
    183,
    8,
    23,
    72,
    188,
    104,
    179,
    210,
    134,
    250,
    201,
    164,
    89,
    216,
    202,
    220,
    50,
    221,
    152,
    140,
    33,
    235,
    214,
)

_ALPHABET = "6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH=5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE"
_KEY16 = b"059053f7d15e01d7"


def _rotl(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (32 - bits))) & 0xFFFFFFFF


def _encrypt_block(block: bytes) -> bytes:
    words = list(struct.unpack(">4I", block))
    for index, key in enumerate(_ZK):
        value = words[index + 1] ^ words[index + 2] ^ words[index + 3] ^ key
        substituted = int.from_bytes(bytes(_ZB[byte] for byte in value.to_bytes(4, "big")), "big")
        transformed = substituted
        for bits in (2, 10, 18, 24):
            transformed ^= _rotl(substituted, bits)
        words.append(words[index] ^ transformed)
    return struct.pack(">4I", *reversed(words[-4:]))


def _encode(ciphertext: bytes) -> str:
    ciphertext += b"\x00" * (-len(ciphertext) % 3)
    output = []
    byte_index = 0
    for position in range(len(ciphertext) - 1, -1, -3):
        value = 0
        for offset in range(3):
            mask = (58 >> (8 * (byte_index % 4))) & 0xFF
            byte_index += 1
            value |= (ciphertext[position - offset] ^ mask) << (8 * offset)
        output.extend(_ALPHABET[(value >> bits) & 63] for bits in (0, 6, 12, 18))
    return "".join(output)


def _encrypt_digest(digest: str) -> str:
    # MD5 hex uses only URI-safe ASCII characters; no URI transform is needed.
    plain = b"\x0c\x00" + digest.encode("ascii")
    padding = 16 - len(plain) % 16
    plain += bytes([padding]) * padding
    first = bytes(plain[index] ^ _KEY16[index] ^ 42 for index in range(16))
    previous = _encrypt_block(first)
    ciphertext = bytearray(previous)
    for offset in range(16, len(plain), 16):
        block = bytes(plain[offset + index] ^ previous[index] for index in range(16))
        previous = _encrypt_block(block)
        ciphertext.extend(previous)
    return _encode(bytes(ciphertext))


def sign_get_headers(path_and_query: str, d_c0: str) -> dict[str, str]:
    """Sign the exact prepared URL path/query and the outgoing device cookie.

    Preserve query encoding, order, and cookie quotes. Credential input and the
    intermediate signing source are deliberately kept out of errors and logs.
    """
    return sign_request_headers(path_and_query, d_c0)


def sign_request_headers(
    path_and_query: str, d_c0: str, body: bytes | None = None
) -> dict[str, str]:
    """Sign the exact request body as UTF-8, without reserializing its JSON."""
    if not path_and_query.startswith("/") or not d_c0:
        raise ValueError("Signing requires a request path and a device cookie")
    source = "+".join((ZSE93, path_and_query, d_c0))
    if body is not None:
        source += "+" + body.decode("utf-8")
    digest = hashlib.md5(source.encode("utf-8")).hexdigest()
    return {
        "x-zse-93": ZSE93,
        "x-zse-96": "2.0_" + _encrypt_digest(digest),
        "x-requested-with": "fetch",
    }
