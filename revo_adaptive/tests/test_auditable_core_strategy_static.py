import ast
import unittest
from pathlib import Path


STRATEGY = Path(__file__).resolve().parents[1] / "strategies" / "RevoAuditableCoreStrategy.py"


class RevoAuditableCoreStrategyStaticTests(unittest.TestCase):
    def test_strategy_adapter_exists_and_uses_core(self):
        tree = ast.parse(STRATEGY.read_text())
        classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
        self.assertIn("RevoAuditableCoreStrategy", classes)
        text = STRATEGY.read_text()
        self.assertIn("decide_entry", text)
        self.assertIn("GateInput", text)
        self.assertIn("auditable_core", text)

    def test_strategy_exposes_entry_and_exit_hooks(self):
        tree = ast.parse(STRATEGY.read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RevoAuditableCoreStrategy")
        methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("populate_entry_trend", methods)
        self.assertIn("custom_exit", methods)


if __name__ == "__main__":
    unittest.main()
