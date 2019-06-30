from collections import namedtuple
from enum import Enum
from typing import BinaryIO, List
from struct import pack, unpack, calcsize
import os


ExtensionRsvs = namedtuple("ExtensionRsvs", "rsv1 rsv2 rsv3")


def unpack_from(fmt: str, stream: BinaryIO):
    size = calcsize(fmt)
    if stream.closed:
        raise InterruptedError("Socket Closed")
    data = stream.read(size)
    return unpack(fmt, data)


class FrameOpcode(Enum):
    CONTINUATION = 0
    TEXT = 1
    BINARY = 2
    CLOSE = 8
    PING = 9
    PONG = 10
    RESERVED = None

    @classmethod
    def from_value(cls, value: int):
        for enum in cls:
            if enum.value == value:
                return enum


class Frame:

    def __init__(self, final: bool, rsvs: ExtensionRsvs, opcode: FrameOpcode, payload: bytes, mask: bytes=None):
        self.final = final
        self.rsvs = rsvs
        self.opcode = opcode
        self.payload = payload
        self.mask = mask

    def build(self) -> bytes:
        frame = bytes()
        frame += pack("!B", int(self.final) << 7 |
                      self.rsvs[0] << 6 |
                      self.rsvs[1] << 5 |
                      self.rsvs[2] << 4 |
                      self.opcode.value
                      )
        payload_length = len(self.payload)
        if payload_length <= 125:
            frame += pack("!B", payload_length | (0 if self.mask is None else 128))
        elif payload_length <= 0xFFFF:
            frame += pack("!B", 126 | (0 if self.mask is None else 128))
            frame += pack("!H", payload_length)
        else:
            frame += pack("!B", 127 | (0 if self.mask is None else 128))
            frame += pack("!Q", payload_length)
        if self.mask is not None:
            frame += self.mask
            frame += self.apply_mask(self.mask, self.payload)
        else:
            frame += self.payload
        return frame

    @classmethod
    def load(cls, stream: BinaryIO):
        code_header = unpack_from("!B", stream)[0]
        final = bool(code_header & 128)
        rsvs_raw = code_header & 64, code_header & 32, code_header & 16
        rsvs = ExtensionRsvs(*(1 if r else 0 for r in rsvs_raw))
        opcode = FrameOpcode.from_value(code_header & 0xF)
        mask_length = unpack_from("!B", stream)[0]
        has_mask = mask_length & 0x80
        payload_length = mask_length & ~0x80
        if payload_length == 126:
            payload_length = unpack_from("!B", stream)[0]
        elif payload_length == 127:
            payload_length = unpack_from("!Q", stream)[0]
        if has_mask:
            mask = stream.read(4)
            payload = cls.apply_mask(mask, stream.read(payload_length))
            return cls(final, rsvs, opcode, payload, mask)
        else:
            payload = stream.read(payload_length)
            return cls(final, rsvs, opcode, payload)

    @staticmethod
    def apply_mask(mask: bytes, content: bytes) -> bytearray:
        content_array = bytearray(content)
        for i, chunk in enumerate(content_array):
            content_array[i] = chunk ^ (mask[i % 4])
        return content_array


class Message:

    def __init__(self, chain: List[Frame]):
        self.data_type = str if chain[0].opcode == FrameOpcode.TEXT else bytes
        self.data = b"".join(c.payload for c in chain)
        if not isinstance(self.data, self.data_type):
            self.data = self.data.decode('utf-8')


class ControlFrame:

    OPCODE = FrameOpcode.RESERVED

    def __init__(self, frame: Frame):
        self.type = frame.opcode
        self.raw_data = frame.payload
        self.load()  # Makes subclassing easy

    def load(self):
        pass

    @classmethod
    def build(cls, extensions: List, mask: bool, payload: bytes=None):
        rsvs = [False, False, False]
        for i in range(3):
            for extension in extensions:
                rsvs[i] = extension.should_set(i, cls)
        rsvs = ExtensionRsvs(*map(int, rsvs))

        return Frame(
            True,
            rsvs,
            cls.OPCODE,
            payload or b"",
            None if not mask else os.urandom(4)
        ).build()


class Ping(ControlFrame):

    OPCODE = FrameOpcode.PING

    @classmethod
    def build(cls, extensions: List, mask: bool):
        return super().build(extensions, mask, None)


class Pong(ControlFrame):

    OPCODE = FrameOpcode.PONG

    @classmethod
    def build(cls, extensions: List, mask: bool):
        return super().build(extensions, mask, None)


class Close(ControlFrame):

    OPCODE = FrameOpcode.CLOSE

    def __init__(self, frame: Frame):
        self.code: int = None
        self.reason: str = None
        super().__init__(frame)

    def load(self):
        if len(self.raw_data) > 2:
            self.code, self.reason = unpack("!H", self.raw_data[:2]), self.raw_data[:2].decode("utf8")
        elif len(self.raw_data) == 2:
            self.code = unpack("!H", self.raw_data)

    @classmethod
    def build(cls, extensions: List, mask: bool, code: int=None, reason: str=None):
        payload = b""
        if code:
            payload += pack("!H", code)
            if reason:
                payload += reason.encode("utf8")
        return super().build(extensions, mask, payload)
