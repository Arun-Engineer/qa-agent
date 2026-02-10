# tests/unit/test_generator.py
import pytest
from agent.codegen.generator import TestGenerator
from pathlib import Path

class DummyGen(TestGenerator):
    def generate_test_code(self, step):
        return "def test_dummy():\n    assert True"

def test_write_and_generate(tmp_path):
    tg = DummyGen()
    file_path = tmp_path / "test_sample.py"
    result = tg.write_test_file("def test_sample(): pass", file_path)
    assert Path(result).exists()