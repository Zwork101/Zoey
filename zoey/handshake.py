from base64 import b64encode
from hashlib import sha1
from typing import List, Dict
from urllib.parse import ParseResult
import os

from zoey.chain import Extension


from http_parser.http import HttpStream, HTTP_RESPONSE
from http_parser.reader import SocketReader


class ServerResponse:

    def __init__(self, code: int, headers: Dict[str, str]):
        self.code = code
        self.headers = headers

    @classmethod
    def load(cls, socket):
        stream = SocketReader(socket)
        http = HttpStream(stream, kind=HTTP_RESPONSE)
        return cls(http.status_code(), http.headers())


class WebsocketUpgrade:

    WS_VERSION = '13'
    WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, wss_uri: ParseResult, extensions: List[Extension], host: str=None, origin: str=None):
        self.method = 'GET'
        self.path = wss_uri.path.rstrip("/") or '/'
        self.protocol = "HTTP/1.1"
        self.key = b64encode(os.urandom(16)).decode()
        self.secure = wss_uri.scheme.endswith("ss")
        self.headers = {
            "Host": host or wss_uri.hostname,
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-Websocket-Key": self.key,
            "Origin": origin or ("https://" if self.secure else "http://") + wss_uri.hostname,
            "Sec-WebSocket-Version": self.WS_VERSION
        }
        self.extensions = extensions
        if extensions:
            self.headers["Sec-WebSocket-Extensions"] = ", ".\
                join(extension.NAME for extension in extensions if extension.NEGOTIATE)

    @property
    def expecting_key(self) -> str:
        sha1_hash = sha1()
        sha1_hash.update(self.key.encode() + self.WS_GUID)
        return b64encode(sha1_hash.digest()).decode()

    def build(self):
        msg = " ".join([self.method, self.path, self.protocol]).encode("utf8") + b"\r\n"
        msg += "\r\n".join(name + ": " + value for name, value in self.headers.items()).encode("utf8") + b"\r\n\r\n"
        return msg

    def confirm(self, response: ServerResponse):
        try:
            assert response.headers["Upgrade"].lower() == "websocket"
            assert response.headers["Connection"].title() == "Upgrade"
            assert response.headers["Sec-Websocket-Accept"] == self.expecting_key
        except (AssertionError, KeyError):
            return False
        return True
