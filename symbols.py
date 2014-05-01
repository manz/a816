class SymbolResolver(object):
    def __init__(self):
        self.symbol_map = {}

    def add_symbol(self, symbol, value):
        self.symbol_map[symbol] = value

    def value_for(self, symbol):
        return self.symbol_map.get(symbol, None)
