"""Deterministic private-emulator keys, preserved from the successful experiment."""
import hashlib
from functools import lru_cache

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


ED25519_Q = 2**255 - 19


ED25519_D = (-121665 * pow(121666, ED25519_Q - 2, ED25519_Q)) % ED25519_Q


ED25519_I = pow(2, (ED25519_Q - 1) // 4, ED25519_Q)


ED25519_BASE_Y = (4 * pow(5, ED25519_Q - 2, ED25519_Q)) % ED25519_Q


def _ed25519_recover_x(y: int, sign: int) -> int:
    xx = (y * y - 1) * pow(ED25519_D * y * y + 1, ED25519_Q - 2, ED25519_Q)
    x = pow(xx % ED25519_Q, (ED25519_Q + 3) // 8, ED25519_Q)
    if (x * x - xx) % ED25519_Q != 0:
        x = (x * ED25519_I) % ED25519_Q
    if (x & 1) != sign:
        x = ED25519_Q - x
    return x


ED25519_BASE = (_ed25519_recover_x(ED25519_BASE_Y, 0), ED25519_BASE_Y)


def _ed25519_add(point_a: tuple[int, int], point_b: tuple[int, int]) -> tuple[int, int]:
    ax, ay = point_a
    bx, by = point_b
    denominator = pow(1 + ED25519_D * ax * bx * ay * by, ED25519_Q - 2, ED25519_Q)
    x = ((ax * by + ay * bx) * denominator) % ED25519_Q
    denominator = pow(1 - ED25519_D * ax * bx * ay * by, ED25519_Q - 2, ED25519_Q)
    y = ((ay * by + ax * bx) * denominator) % ED25519_Q
    return x, y


def _ed25519_scalarmult(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    addend = point
    while scalar:
        if scalar & 1:
            result = _ed25519_add(result, addend)
        addend = _ed25519_add(addend, addend)
        scalar >>= 1
    return result


def _ed25519_encode(point: tuple[int, int]) -> bytes:
    x, y = point
    encoded = bytearray(y.to_bytes(32, "little"))
    encoded[31] |= (x & 1) << 7
    return bytes(encoded)


@lru_cache(maxsize=None)
def _ed25519_public_from_seed(seed: bytes) -> bytes:
    digest = bytearray(hashlib.sha512(seed).digest()[:32])
    digest[0] &= 248
    digest[31] &= 63
    digest[31] |= 64
    scalar = int.from_bytes(digest, "little")
    return _ed25519_encode(_ed25519_scalarmult(ED25519_BASE, scalar))


@lru_cache(maxsize=None)
def _solana_keypair(label: str) -> tuple[int, ...]:
    # Deterministic keys keep generated output reproducible. This is only for a
    # private emulator network, not for production wallets.
    seed = hashlib.sha256(f"seedemu-d61-solana:{label}".encode("utf-8")).digest()
    public_key = _ed25519_public_from_seed(seed)
    return tuple(seed + public_key)


@lru_cache(maxsize=None)
def _base58_encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = BASE58_ALPHABET[remainder] + encoded
    leading_zeroes = len(data) - len(data.lstrip(b"\0"))
    return BASE58_ALPHABET[0] * leading_zeroes + (encoded or BASE58_ALPHABET[0])


def _solana_pubkey(label: str) -> str:
    keypair = _solana_keypair(label)
    return _base58_encode(bytes(keypair[32:]))
