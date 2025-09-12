class A816Error(Exception):
    pass


class SymbolNotDefined(A816Error):
    pass


class ExternalSymbolReference(A816Error):
    def __init__(self, symbol_name: str):
        self.symbol_name = symbol_name
        super().__init__(f"External symbol reference: {symbol_name}")


class ExternalExpressionReference(A816Error):
    def __init__(self, expression_str: str, symbols: set[str]) -> None:
        self.expression_str = expression_str
        self.external_symbols = symbols
        super().__init__(f"Expression contains external symbols: {expression_str}")


class UnableToEvaluateSize(A816Error):
    pass
