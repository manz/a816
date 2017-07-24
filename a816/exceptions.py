class A816Error(Exception):
    pass


class SymbolNotDefined(A816Error):
    pass


class UnableToEvaluateSize(A816Error):
    pass
