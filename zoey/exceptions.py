class ZoeyExceptions(Exception):
    pass


class HandshakeFail(ZoeyExceptions):
    pass


class AlreadyClosed(ZoeyExceptions):
    pass


class NotConnected(ZoeyExceptions):
    pass


class InvalidExtension(ZoeyExceptions):
    pass
