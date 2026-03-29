"""Tests for LLM backend infrastructure."""

import unittest
from src.domain.port.llm_service import LLMResponse
from src.infrastructure.llm.ollama_backend import NullLLMService, parse_llm_json


class TestNullLLMService(unittest.TestCase):
    def test_not_available(self):
        svc = NullLLMService()
        self.assertFalse(svc.available)

    def test_query_returns_error(self):
        svc = NullLLMService()
        r = svc.query("test")
        self.assertFalse(r.ok)


class TestLLMResponse(unittest.TestCase):
    def test_ok(self):
        r = LLMResponse(text="hello", model="t", backend="t")
        self.assertTrue(r.ok)

    def test_not_ok_empty(self):
        r = LLMResponse(text="", model="t", backend="t")
        self.assertFalse(r.ok)

    def test_not_ok_error(self):
        r = LLMResponse(text="data", model="t", backend="t", error="fail")
        self.assertFalse(r.ok)


class TestParseJson(unittest.TestCase):
    def test_clean_json(self):
        r = LLMResponse(text='{"matched": ["a.py"]}', model="t", backend="t")
        data = parse_llm_json(r)
        self.assertEqual(data, {"matched": ["a.py"]})

    def test_code_fence(self):
        r = LLMResponse(text='```json\n{"foo": 1}\n```', model="t", backend="t")
        data = parse_llm_json(r)
        self.assertEqual(data, {"foo": 1})

    def test_preamble(self):
        r = LLMResponse(text='Here is the result:\n{"bar": 2}', model="t", backend="t")
        data = parse_llm_json(r)
        self.assertEqual(data, {"bar": 2})

    def test_empty(self):
        r = LLMResponse(text="", model="t", backend="t", error="fail")
        self.assertIsNone(parse_llm_json(r))
