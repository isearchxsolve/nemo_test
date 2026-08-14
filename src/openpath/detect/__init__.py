"""Stack & repo detection for codebase mode."""
from __future__ import annotations

from .stack_detector import Stack, detect_stack
from .server_manager import ServerManager, pick_free_port, wait_for_health
from .plan_generator import GeneratedTest, plan_for_stack
from .test_case_generator import PlanOptions, generate_test_plan, plan_for_stack as plan_for_stack_comprehensive

__all__ = [
    "Stack",
    "detect_stack",
    "ServerManager",
    "pick_free_port",
    "wait_for_health",
    "GeneratedTest",
    "plan_for_stack",
    "PlanOptions",
    "generate_test_plan",
    "plan_for_stack_comprehensive",
]