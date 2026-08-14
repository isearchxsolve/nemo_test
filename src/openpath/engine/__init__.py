"""The OpenPath execution engine: deterministic + AI runners, selectors,
assertions, and Selenium runner.
"""
from __future__ import annotations

from .selectors import SelectorStrategy, SelectorResult, resolve_selector
from .assertions import Assertion, AssertionResult, evaluate_assertions
from .playwright_runner import PlaywrightRunner, Step, StepResult, SuiteResult, TestResult
from .selenium_runner import SeleniumRunner, SuiteResult as SeleniumSuiteResult
from .ai_runner import AIRunner, NLTestCompiler
from .recorder import Recorder

__all__ = [
    "SelectorStrategy",
    "SelectorResult",
    "resolve_selector",
    "Assertion",
    "AssertionResult",
    "evaluate_assertions",
    "PlaywrightRunner",
    "Step",
    "StepResult",
    "SuiteResult",
    "TestResult",
    "SeleniumRunner",
    "SeleniumSuiteResult",
    "AIRunner",
    "NLTestCompiler",
    "Recorder",
]