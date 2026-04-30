class DocumentParseError(Exception):
    pass


class ConversionError(DocumentParseError):
    pass


class EngineNotAvailableError(DocumentParseError):
    pass
