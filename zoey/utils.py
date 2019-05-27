from enum import Enum


class ConnectionStatus(Enum):
    CLOSED = 0
    CONNECTING = 1
    CONNECTED = 2
    CLOSING = 3
