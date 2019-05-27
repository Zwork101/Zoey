from urllib.parse import urlparse
from typing import Union, Type
from os import urandom

from zoey.chain import Extension, WSConstructor
from zoey.exceptions import AlreadyClosed, HandshakeFail, NotConnected
from zoey.framing import ControlFrame, Close, ExtensionRsvs, Message, Ping, Pong, Frame, FrameOpcode
from zoey.handshake import WebsocketUpgrade, ServerResponse
from zoey.utils import ConnectionStatus

from gevent import spawn
import gevent._ssl3 as ssl
import gevent._socket3 as socket


class Client:

    WS_PORT = 80, 443
    MAX_SIZE = (2 ** 64) - 1

    def __init__(self, ws_uri: str, *extensions: Type[Extension], origin: str=None,
                 host: str=None, context: ssl.SSLContext=None):
        self.uri = urlparse(ws_uri)

        if self.uri.scheme not in ("wss", "ws"):
            raise NotImplementedError("Scheme must be either 'wss' or 'ws'.")

        self.extensions = [extension(self) for extension in extensions]
        self.overwrites = {"origin": origin, "host": host}
        self.status = ConnectionStatus.CLOSED
        self.socket = socket.socket()
        self.constructor = WSConstructor(self)
        if self.is_secure:
            context = context or ssl.create_default_context()
            self.socket = context.wrap_socket(self.socket, server_hostname=host or self.uri.hostname)

        self.close_reason = None
        self._connect_greenlet = None

    def trigger(self, name: str, *args, **kwargs):
        for extension in self.extensions:
            getattr(extension, name)(*args, **kwargs)

    def send_control(self, frame: Type[ControlFrame], **kwargs):
        self.trigger("before_control_frame", frame)
        data = frame.build(**kwargs, extensions=self.extensions)
        self.socket.send(data)

    def send_message(self, msg: Union[bytes, str]):
        is_text = isinstance(msg, str)
        if is_text:
            msg = msg.encode("utf8")
        msgs = []
        for chunk in range(0, len(msg), self.MAX_SIZE):
            msgs.append(msg[chunk: chunk + self.MAX_SIZE])

        rsvs = [False, False, False]
        for extension in self.extensions:
            for i in range(3):
                rsvs[i] = extension.should_set(i, Frame)

        for i, part in enumerate(msgs):
            frame = Frame(
                True if i == len(msgs) - 1 else False,
                ExtensionRsvs(*map(int, rsvs)),
                FrameOpcode.TEXT if is_text else FrameOpcode.BINARY,
                part,
                urandom(4) if not self.is_secure else None
            )
            self.trigger("before_frame", frame)
            self.socket.send(frame.build())

    @property
    def is_secure(self) -> bool:
        return self.uri.scheme == "wss"

    def close(self, code: int=1000, reason: str=None):
        if ConnectionStatus.CONNECTED:
            self.send_control(Close, mask=urandom(4) if not self.is_secure else None, code=code, reason=reason)
            self.status = ConnectionStatus.CLOSING
        elif ConnectionStatus.CONNECTING:
            self.socket.close()
        elif ConnectionStatus.CLOSING:
            self.socket.close()
        else:
            raise AlreadyClosed

    def connect(self):
        self.status = ConnectionStatus.CONNECTING
        self.socket.connect((self.uri.hostname, self.WS_PORT[1] if self.is_secure else self.WS_PORT[0]))
        upgrade = WebsocketUpgrade(self.uri, self.extensions, **self.overwrites)
        data = upgrade.build()
        self.socket.send(data)
        response = ServerResponse.load(self.socket)
        if response.code != 101:
            self.close()
            raise HandshakeFail("Code: {}".format(response.code))
        if not upgrade.confirm(response):
            self.close()
            raise HandshakeFail("Invalid websocket response")
        self.status = ConnectionStatus.CONNECTED
        self._connect_greenlet = spawn(self.constructor.start)
        self.trigger("on_connection")
        self.on_connection()

    def run_forever(self):
        if self.status != ConnectionStatus.CONNECTING and self.status != ConnectionStatus.CONNECTED:
            raise NotConnected("Must connect before running forever.")
        self._connect_greenlet.join()

    def on_connection(self):
        pass

    def on_message(self, msg: Message):
        pass

    def on_ping(self, ping: Ping):
        pass

    def on_pong(self, pong: Pong):
        pass

    def on_close(self, close: Close):
        self.close_reason = close
        self.status = ConnectionStatus.CLOSING
        self.close()
