from AlgorithmImports import *


class LeanSmokeAlgorithm(QCAlgorithm):
    """No-cloud smoke test for the standalone LEAN engine container."""

    def initialize(self):
        self.set_start_date(2024, 1, 1)
        self.set_end_date(2024, 1, 2)
        self.set_cash(100000)
        self.debug("LEAN_SMOKE_INITIALIZED")

    def on_data(self, data: Slice):
        pass

    def on_end_of_algorithm(self):
        self.debug("LEAN_SMOKE_COMPLETED")
