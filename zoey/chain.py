from collections import ChainMap
from struct import error
from typing import List, Tuple, Type, Optional, Union

from zoey.exceptions import InvalidExtension
from zoey.framing import Close, ControlFrame, Frame, FrameOpcode, Message, Ping, Pong
from zoey.utils import ConnectionStatus

from gevent import spawn, sleep


class Extension:

    def __init__(self, client):
        self.client = client
        if self.NEGOTIATE and self.NAME is None:
            raise InvalidExtension("Negotiable extensions must specify a name")

    RSV1 = False
    RSV2 = False
    RSV3 = False

    NAME = None
    NEGOTIATE = False

    def on_connection(self):
        pass

    def on_message(self, msg: Message):
        pass

    def on_ping(self, ping: Ping):
        pass

    def on_pong(self, pong: Pong):
        pass

    def on_close(self, close: Close):
        pass

    def should_set(self, rsv: int, frame: Type[Union[ControlFrame, Frame]]):
        return False

    def before_control_frame(self, frame: ControlFrame):
        pass

    def before_frame(self, frame: Frame):
        pass


class WSConstructor:

    FRAME_CAP = 20

    def __init__(self, client):
        self.frame_setup = []
        self.last_frame: Frame = None
        self.client = client

    def verify(self, frame: Frame) -> Tuple[bool, Optional[str]]:
        if self.last_frame:
            if frame.opcode == FrameOpcode.CONTINUATION and self.last_frame.opcode in (
                FrameOpcode.PING,
                FrameOpcode.PONG,
                FrameOpcode.CLOSE
            ):
                return False, "Ping, Pong, or Close frames cannot be fragmented."
            if frame.opcode == FrameOpcode.CONTINUATION and self.last_frame.final:
                return False, "Sent continuation after final frame."
        else:
            if frame.opcode == FrameOpcode.CONTINUATION:
                return False, "Sent continuation without initial frame."

        if frame.opcode not in FrameOpcode:
            return False, "Unknown opcode used."
        used_rsv = ChainMap(*[{"R1": r.RSV1, "R2": r.RSV2, "R3": r.RSV3} for r in self.client.extensions])
        if not used_rsv.get("R1") and frame.rsvs.rsv1:
            return False, "Used frame-rsv1 without proper extension"
        if not used_rsv.get("R2") and frame.rsvs.rsv2:
            return False, "Used frame-rsv2 without proper extension"
        if not used_rsv.get("R3") and frame.rsvs.rsv3:
            return False, "Used frame-rsv3 without proper extension"
        return True, None

    def recv_frame(self, frame: Frame):
        good_frame, reason = self.verify(frame)
        if not good_frame:
            return self.client.close(1002, reason)
        if frame.opcode in (FrameOpcode.TEXT, FrameOpcode.BINARY):
            self.frame_setup.append(frame)
            if frame.final:
                msg = Message(self.frame_setup)
                self.client.trigger("on_message", msg)
                self.client.on_message(msg)
        elif frame.opcode == FrameOpcode.PING:
            ping = Ping(frame)
            self.client.trigger("on_ping", ping)
            self.client.on_ping(ping)
        elif frame.opcode == FrameOpcode.PONG:
            pong = Pong(frame)
            self.client.trigger("on_pong", pong)
            self.client.on_pong(pong)
        else:
            close = Close(frame)
            self.client.trigger("on_close", close)
            self.client.on_close(close)
        self.last_frame = frame

    def collect_frames(self):
        while True:
            try:
                frame = Frame.load(self.client.socket)
            except error:
                break
            self.recv_frame(frame)

    def start(self):
        greenlet = spawn(self.collect_frames)
        while self.client.status == ConnectionStatus.CONNECTED:
            sleep(0.05)
        greenlet.kill()
