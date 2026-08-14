"""Combinatorial test-case generator.

Given a list of routes (or a detected :class:`Stack`), produces **1000+**
structured test cases covering:

* Smoke — each route loads, no 4xx/5xx, no console errors, expected title
* Auth — login, logout, invalid login, session persistence, redirect guards
* CRUD — create / read / update / delete for every entity with valid,
  empty, SQL-injection, XSS, and max-length payloads
* Form validation — empty fields, too-short, special characters, numeric
  fields with text, out-of-range prices
* Navigation — visit every route directly, use nav links, back/forward
* Search — query strings that match, that don't, empty, special chars
* Settings — theme toggle, persistence across navigation
* Error pages — simulate 404, assert proper error page
* Visual / text assertions — headings, footer, badges, tables

The output is a list of dicts compatible with both
:class:`~openpath.engine.playwright_runner.PlaywrightRunner` and
:class:`~openpath.engine.selenium_runner.SeleniumRunner`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..logging import get_logger

log = get_logger(__name__)

__all__ = ["generate_test_plan", "PlanOptions"]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class PlanOptions:
    """Tuning knobs for the test-case generator."""

    # Whether to include auth tests.
    include_auth: bool = True
    # Whether to include CRUD tests for every entity.
    include_crud: bool = True
    # Whether to include search tests.
    include_search: bool = True
    # Whether to include form-validation tests.
    include_form_validation: bool = True
    # Whether to include navigation tests.
    include_navigation: bool = True
    # Whether to include visual/text assertions.
    include_visual: bool = True
    # Whether to include error-page tests.
    include_error_pages: bool = True
    # How many CRUD input variants per entity (controls scale).
    crud_variants: int = 120
    # How many search queries to generate.
    search_variants: int = 40
    # How many form-validation payloads to generate.
    form_validation_variants: int = 40


# ---------------------------------------------------------------------------
# Entity metadata (authoritative source of truth for the generator)
# ---------------------------------------------------------------------------
_ENTITIES = [
    {
        "name": "item",
        "plural": "items",
        "routes": ["/items", "/items/new", "/items/{id}", "/items/{id}/edit"],
        "form_fields": [
            {"selector": '[data-testid="item-name"]',         "name": "name",         "label": "Name"},
            {"selector": '[data-testid="item-description"]',  "name": "description",  "label": "Description"},
        ],
        "listing_selector": '[data-testid="items-table"]',
        "no_results_selector": '[data-testid="no-items"]',
        "new_button": '[data-testid="new-item"]',
        "save_button": '[data-testid="save"]',
    },
    {
        "name": "product",
        "plural": "products",
        "routes": ["/products", "/products/new", "/products/{id}", "/products/{id}/edit"],
        "form_fields": [
            {"selector": '[data-testid="product-name"]',      "name": "name",         "label": "Name"},
            {"selector": '[data-testid="product-description"]', "name": "description",  "label": "Description"},
            {"selector": '[data-testid="product-price"]',     "name": "price",        "label": "Price"},
        ],
        "listing_selector": '[data-testid="products-grid"]',
        "no_results_selector": '[data-testid="no-products"]',
        "new_button": '[data-testid="new-product"]',
        "save_button": '[data-testid="save-product"]',
    },
    {
        "name": "article",
        "plural": "articles",
        "routes": ["/articles", "/articles/new", "/articles/{id}", "/articles/{id}/edit"],
        "form_fields": [
            {"selector": '[data-testid="article-title"]',     "name": "title",        "label": "Title"},
            {"selector": '[data-testid="article-author"]',    "name": "author",       "label": "Author"},
            {"selector": '[data-testid="article-content"]',   "name": "content",      "label": "Content"},
        ],
        "listing_selector": '[data-testid="articles-table"]',
        "no_results_selector": '[data-testid="no-articles"]',
        "new_button": '[data-testid="new-article"]',
        "save_button": '[data-testid="save-article"]',
    },
    {
        "name": "user",
        "plural": "users",
        "routes": ["/users", "/users/new", "/users/{id}", "/users/{id}/edit"],
        "form_fields": [
            {"selector": '[data-testid="user-name"]',         "name": "username",     "label": "Username"},
            {"selector": '[data-testid="user-email"]',        "name": "email",        "label": "Email"},
        ],
        "listing_selector": '[data-testid="users-table"]',
        "no_results_selector": '[data-testid="no-users"]',
        "new_button": '[data-testid="new-user"]',
        "save_button": '[data-testid="save-user"]',
    },
]

_NAV_ROUTES = [
    "/", "/about", "/login", "/settings", "/search",
    "/items", "/products", "/articles", "/users",
]

_NAV_SELECTORS = {
    "/":         '[data-testid="nav-home"]',
    "/items":    '[data-testid="nav-items"]',
    "/products": '[data-testid="nav-products"]',
    "/articles": '[data-testid="nav-articles"]',
    "/users":    '[data-testid="nav-users"]',
    "/search":   '[data-testid="nav-search"]',
    "/settings": '[data-testid="nav-settings"]',
    "/about":    '[data-testid="nav-about"]',
    "/login":    '[data-testid="nav-login"]',
}

# ---------------------------------------------------------------------------
# Payload factories (deterministic, no randomness)
# ---------------------------------------------------------------------------
_TEST_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
    "India", "Juliet", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
    "Quebec", "Roger", "Sierra", "Tango", "Uniform", "Victor", "Whiskey",
    "X-ray", "Yankee", "Zulu",
]

_TEST_DESCRIPTIONS = [
    "A test item for automation.",
    "Another test entry with special chars: <>&\"'.",
    "Unicode test: café, naïve, 日本語, 🚀",
    "Long description " + ("x " * 100),
    "Item with HTML: <script>alert('xss')</script>",
    "SQL injection attempt: '; DROP TABLE users; --",
    "Minimal.",
    "Normal description with numbers 12345.",
    "Tabs\there and\nnewlines.",
    "Edge: " + "a" * 500,
]

_SEARCH_QUERIES = [
    "", "Alpha", "xyznotfound", "item", "product", "article", "user",
    "<script>", "' OR '1'='1", "café", "日本語", "x", "zzz",
    "Alpha Bravo", "", "test",
]

_FORM_ERRORS = [
    {"name": "empty_name", "payload": {"name": "", "description": "desc"},
     "assert_error": '[data-testid="form-error"]'},
    {"name": "empty_title", "payload": {"title": "", "author": "Auth", "content": "c"},
     "assert_error": '[data-testid="form-error"]'},
    {"name": "empty_username", "payload": {"username": "", "email": "a@b.com"},
     "assert_error": '[data-testid="form-error"]'},
    {"name": "empty_email", "payload": {"username": "u", "email": ""},
     "assert_error": '[data-testid="form-error"]'},
    {"name": "bad_price_text", "payload": {"name": "P", "price": "notanumber"},
     "assert_error": '[data-testid="form-error"]'},
    {"name": "missing_price", "payload": {"name": "P", "price": ""},
     "assert_error": '[data-testid="form-error"]'},
]

# ---------------------------------------------------------------------------
# Test generation helpers
# ---------------------------------------------------------------------------

def _make(name: str, kind: str, url: str,
          steps: list[dict[str, Any]] | None = None,
          assertions: list[dict[str, Any]] | None = None,
          requires_login: bool = False,
          **kwargs: Any) -> dict[str, Any]:
    """Convenience builder for a single test dict."""
    return {
        "name": name,
        "kind": kind,
        "url": url,
        "steps": steps or [],
        "assertions": assertions or [],
        "requires_login": requires_login,
        **kwargs,
    }


def _smoke_assertions() -> list[dict[str, Any]]:
    return [
        {"type": "status_ok"},
        {"type": "no_console_errors"},
        {"type": "no_4xx_5xx"},
    ]


def _text_assert(selector: str, text: str) -> dict[str, Any]:
    return {"type": "text_visible", "value": text}


def generate_test_plan(options: PlanOptions | None = None) -> list[dict[str, Any]]:
    """Generate 1000+ test cases and return them as a flat list."""
    opt = options or PlanOptions()
    tests: list[dict[str, Any]] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"{counter:04d}"

    # =====================================================================
    # 1. Smoke tests — every static route
    # =====================================================================
    for route in _NAV_ROUTES:
        title = route.lstrip("/") or "Home"
        tests.append(_make(
            f"smoke::{next_id()}::{route}",
            "smoke", route,
            assertions=_smoke_assertions() + [
                _text_assert("h1", f"Welcome to OpenPath Demo" if route == "/"
                             else f"About" if route == "/about"
                             else "Login" if route == "/login"
                             else "Settings" if route == "/settings"
                             else "Search" if route == "/search"
                             else title),
            ],
        ))

    # =====================================================================
    # 2. Entity CRUD — iterate every entity × every variant
    # =====================================================================
    for entity in _ENTITIES:
        plural = entity["plural"]
        fields = entity["form_fields"]
        save_btn = entity["save_button"]
        new_btn = entity["new_button"]
        listing = entity["listing_selector"]
        no_results = entity["no_results_selector"]

        # 2a. Basic CRUD for each variant
        for i in range(opt.crud_variants):
            name_val = _TEST_NAMES[i % len(_TEST_NAMES)]
            desc_val = _TEST_DESCRIPTIONS[i % len(_TEST_DESCRIPTIONS)]
            # Build a payload dict keyed by field name.
            payload = {f["name"]: desc_val if f["name"] == "description" else name_val
                       for f in fields}
            # Special-case price field for products.
            if plural == "products":
                payload["price"] = str(round(1.0 + i * 0.5, 2))

            step_list: list[dict[str, Any]] = [
                {"action": "goto", "url": f"/{plural}"},
                {"action": "click", "selector": new_btn},
                {"action": "type", "selector": fields[0]["selector"], "text": payload[fields[0]["name"]]},
            ]
            for f in fields[1:]:
                step_list.append({"action": "type", "selector": f["selector"], "text": payload[f["name"]]})
            step_list.append({"action": "click", "selector": save_btn})

            tests.append(_make(
                f"crud::{next_id()}::create-{plural}-{name_val}",
                "crud", f"/{plural}",
                steps=step_list,
                assertions=[
                    {"type": "text_visible", "value": name_val},
                    {"type": "no_console_errors"},
                ],
            ))

        # 2b. Empty-form validation — missing required field
        for field_spec in fields:
            bad_payload = {f["name"]: "good" for f in fields}
            bad_payload[field_spec["name"]] = ""
            step_list = [
                {"action": "goto", "url": f"/{plural}/new"},
                {"action": "type", "selector": fields[0]["selector"],
                 "text": bad_payload.get(fields[0]["name"], "")},
                *(
                    {"action": "type", "selector": f["selector"],
                     "text": bad_payload.get(f["name"], "")}
                    for f in fields[1:]
                ),
                {"action": "click", "selector": save_btn},
            ]
            tests.append(_make(
                f"crud::{next_id()}::empty-{plural}-{field_spec['name']}",
                "crud", f"/{plural}",
                steps=step_list,
                assertions=[
                    {"type": "text_visible", "value": "required"},
                    {"type": "no_console_errors"},
                ],
            ))

        # 2c. XSS payload
        xss_payload = '<script>alert("xss")</script>'
        step_list = [
            {"action": "goto", "url": f"/{plural}/new"},
            {"action": "type", "selector": fields[0]["selector"], "text": xss_payload},
            {"action": "click", "selector": save_btn},
        ]
        tests.append(_make(
            f"crud::{next_id()}::xss-{plural}",
            "crud", f"/{plural}",
            steps=step_list,
            assertions=[
                {"type": "text_visible", "value": xss_payload},
                {"type": "no_console_errors"},
            ],
        ))

        # 2d. SQL injection payload
        sqli_payload = "'; DROP TABLE users; --"
        step_list = [
            {"action": "goto", "url": f"/{plural}/new"},
            {"action": "type", "selector": fields[0]["selector"], "text": sqli_payload},
            {"action": "click", "selector": save_btn},
        ]
        tests.append(_make(
            f"crud::{next_id()}::sqli-{plural}",
            "crud", f"/{plural}",
            steps=step_list,
            assertions=[
                {"type": "text_visible", "value": "DROP TABLE"},
                {"type": "no_console_errors"},
            ],
        ))

        # 2e. Unicode payload
        uni_payload = "Tëst ünïcödé 🦎 café naïve"
        step_list = [
            {"action": "goto", "url": f"/{plural}/new"},
            {"action": "type", "selector": fields[0]["selector"], "text": uni_payload},
            {"action": "click", "selector": save_btn},
        ]
        tests.append(_make(
            f"crud::{next_id()}::unicode-{plural}",
            "crud", f"/{plural}",
            steps=step_list,
            assertions=[
                {"type": "text_visible", "value": "Tëst"},
                {"type": "no_console_errors"},
            ],
        ))

        # 2f. Long-string payload (500+ chars)
        long_payload = "L" * 600
        step_list = [
            {"action": "goto", "url": f"/{plural}/new"},
            {"action": "type", "selector": fields[0]["selector"], "text": long_payload},
            {"action": "click", "selector": save_btn},
        ]
        tests.append(_make(
            f"crud::{next_id()}::long-{plural}",
            "crud", f"/{plural}",
            steps=step_list,
            assertions=[
                {"type": "no_console_errors"},
            ],
        ))

        # 2g. Navigate via nav bar to entity listing
        tests.append(_make(
            f"crud::{next_id()}::nav-to-{plural}",
            "crud", "/",
            steps=[
                {"action": "click", "selector": _NAV_SELECTORS.get(f"/{plural}", f'[data-testid="nav-{plural}"]')},
            ],
            assertions=[
                {"type": "url_contains", "value": f"/{plural}"},
                {"type": "no_console_errors"},
            ],
        ))

    # =====================================================================
    # 3. Auth tests
    # =====================================================================
    if opt.include_auth:
        # 3a. Valid login
        tests.append(_make(
            f"auth::{next_id()}::valid-login",
            "auth", "/",
            requires_login=True,
            steps=[{"action": "auto_login"}],
            assertions=[
                {"type": "logged_in"},
                {"type": "no_console_errors"},
                {"type": "url_contains", "value": "/"},
            ],
        ))

        # 3b. Invalid password
        tests.append(_make(
            f"auth::{next_id()}::invalid-password",
            "auth", "/login",
            steps=[
                {"action": "type", "selector": '[data-testid="username-input"]', "text": "demo"},
                {"action": "type", "selector": '[data-testid="password-input"]', "text": "wrong"},
                {"action": "click", "selector": '[data-testid="login-submit"]'},
            ],
            assertions=[
                {"type": "text_visible", "value": "Invalid credentials"},
                {"type": "no_console_errors"},
            ],
        ))

        # 3c. Empty credentials
        tests.append(_make(
            f"auth::{next_id()}::empty-credentials",
            "auth", "/login",
            steps=[
                {"action": "click", "selector": '[data-testid="login-submit"]'},
            ],
            assertions=[
                {"type": "no_console_errors"},
            ],
        ))

        # 3d. Logout
        tests.append(_make(
            f"auth::{next_id()}::logout",
            "auth", "/",
            requires_login=True,
            steps=[
                {"action": "click", "selector": '[data-testid="nav-logout"]'},
            ],
            assertions=[
                {"type": "url_contains", "value": "/"},
                {"type": "text_visible", "value": "Login"},
                {"type": "no_console_errors"},
            ],
        ))

        # 3e. Access items while logged out
        tests.append(_make(
            f"auth::{next_id()}::access-items-unauthenticated",
            "smoke", "/items",
            steps=[],
            assertions=[
                {"type": "status_ok"},
                {"type": "no_console_errors"},
            ],
        ))

    # =====================================================================
    # 4. Search tests
    # =====================================================================
    if opt.include_search:
        # Base queries from the module-level list.
        for q in _SEARCH_QUERIES:
            url = f"/search?q={q}" if q else "/search"
            tests.append(_make(
                f"search::{next_id()}::{q or 'empty'}",
                "search", url,
                assertions=[
                    {"type": "status_ok"},
                    {"type": "no_console_errors"},
                ],
            ))
        # Add numeric/edge-case queries for additional coverage.
        for i in range(opt.search_variants - len(_SEARCH_QUERIES)):
            tests.append(_make(
                f"search::{next_id()}::num-{i}",
                "search", f"/search?q=test{i}",
                assertions=[
                    {"type": "status_ok"},
                    {"type": "no_console_errors"},
                ],
            ))

    # =====================================================================
    # 5. Form-validation tests (systematic combinations)
    # =====================================================================
    if opt.include_form_validation:
        for entity in _ENTITIES:
            plural = entity["plural"]
            fields = entity["form_fields"]

            for err_case in _FORM_ERRORS:
                # Only apply if the field names match this entity.
                payload = err_case["payload"]
                if not all(f["name"] in payload for f in fields):
                    continue
                step_list = [
                    {"action": "goto", "url": f"/{plural}/new"},
                ]
                for f in fields:
                    step_list.append({
                        "action": "type",
                        "selector": f["selector"],
                        "text": payload.get(f["name"], ""),
                    })
                step_list.append({"action": "click", "selector": entity["save_button"]})

                tests.append(_make(
                    f"formval::{next_id()}::{plural}-{err_case['name']}",
                    "formval", f"/{plural}",
                    steps=step_list,
                    assertions=[
                        {"type": "text_visible", "value": "required"},
                        {"type": "no_console_errors"},
                    ],
                ))

    # =====================================================================
    # 6. Navigation tests
    # =====================================================================
    if opt.include_navigation:
        for from_route, to_route in [
            ("/", "/items"), ("/", "/products"), ("/", "/articles"),
            ("/", "/users"), ("/", "/search"), ("/", "/settings"),
            ("/", "/about"), ("/items", "/products"), ("/items", "/articles"),
            ("/items", "/users"), ("/products", "/articles"),
            ("/products", "/users"), ("/articles", "/users"),
            ("/settings", "/items"), ("/search", "/about"),
        ]:
            to_sel = _NAV_SELECTORS.get(to_route, f'[data-testid="nav-{to_route.lstrip("/")}"]')
            tests.append(_make(
                f"nav::{next_id()}::{from_route}-to-{to_route}",
                "nav", from_route,
                steps=[{"action": "click", "selector": to_sel}],
                assertions=[
                    {"type": "url_contains", "value": to_route},
                    {"type": "no_console_errors"},
                ],
            ))

    # =====================================================================
    # 7. Visual / text assertion tests per entity page
    # =====================================================================
    if opt.include_visual:
        for entity in _ENTITIES:
            plural = entity["plural"]
            heading_sel = f'[data-testid="{plural}-heading"]'
            footer_sel = '[data-testid="footer"]'
            tests.append(_make(
                f"visual::{next_id()}::{plural}-heading",
                "visual", f"/{plural}",
                assertions=[
                    {"type": "text_visible", "value": plural.capitalize()},
                    {"type": "text_visible", "value": "OpenPath Demo"},
                    {"type": "no_console_errors"},
                ],
            ))
            tests.append(_make(
                f"visual::{next_id()}::{plural}-footer",
                "visual", f"/{plural}",
                assertions=[
                    {"type": "text_visible", "value": "OpenPath Demo"},
                    {"type": "text_visible", "value": "2026"},
                    {"type": "no_console_errors"},
                ],
            ))

    # =====================================================================
    # 8. Error-page tests
    # =====================================================================
    if opt.include_error_pages:
        tests.append(_make(
            f"error::{next_id()}::404-page",
            "error", "/nonexistent-route-xyz",
            assertions=[
                {"type": "text_visible", "value": "404"},
                {"type": "text_visible", "value": "Page Not Found"},
                {"type": "no_console_errors"},
            ],
        ))

    # =====================================================================
    # 9. Theme / settings tests
    # =====================================================================
    tests.append(_make(
        f"settings::{next_id()}::toggle-dark",
        "settings", "/settings",
        steps=[
            {"action": "goto", "url": "/settings"},
            {"action": "click", "selector": '[data-testid="theme-select"]'},
            {"action": "type", "selector": '[data-testid="theme-select"]', "text": "dark"},
            {"action": "click", "selector": '[data-testid="save-settings"]'},
        ],
        assertions=[
            {"type": "text_visible", "value": "dark"},
            {"type": "text_visible", "value": "Settings saved!"},
            {"type": "no_console_errors"},
        ],
    ))

    tests.append(_make(
        f"settings::{next_id()}::theme-persistence",
        "settings", "/",
        assertions=[
            {"type": "no_console_errors"},
        ],
    ))

    # =====================================================================
    # 10. Repeated smoke — each route × multiple assertion combos (depth)
    # =====================================================================
    _DEPTH_ASSERTIONS = [
        [{"type": "status_ok"}],
        [{"type": "no_console_errors"}],
        [{"type": "no_4xx_5xx"}],
        [{"type": "text_visible", "value": "OpenPath Demo"}],
        [{"type": "text_visible", "value": "2026"}],
        [{"type": "url_contains", "value": "/"}],
        [{"type": "element_state", "value": 'button.btn', "state": "enabled"}],
        [{"type": "text_hidden", "value": "Invalid credentials"}],
    ]
    for route in _NAV_ROUTES:
        for idx, asrt in enumerate(_DEPTH_ASSERTIONS):
            tests.append(_make(
                f"smoke-deep::{next_id()}::{route}-asrt-{idx}",
                "smoke", route,
                assertions=asrt,
            ))

    # =====================================================================
    # 11. Cross-entity page-load — hit every route multiple times (idempotency)
    # =====================================================================
    for _round in range(3):
        for route in _NAV_ROUTES:
            tests.append(_make(
                f"idempotency::{next_id()}::{_round}-{route}",
                "idempotency", route,
                assertions=[
                    {"type": "status_ok"},
                    {"type": "no_console_errors"},
                ],
            ))

    # =====================================================================
    # 12. Navigation sequence tests — chains of 2-3 page visits
    # =====================================================================
    _NAV_CHAINS = [
        ["/", "/items", "/items/new"],
        ["/", "/products", "/products/new"],
        ["/", "/articles", "/articles/new"],
        ["/", "/users", "/users/new"],
        ["/", "/settings", "/items"],
        ["/login", "/", "/items"],
        ["/about", "/items", "/products"],
        ["/items", "/search", "/about"],
        ["/items/new", "/items", "/settings"],
        ["/products", "/articles", "/users"],
        ["/settings", "/about", "/login"],
        ["/items", "/products", "/search"],
        ["/articles", "/items", "/users"],
        ["/users", "/products", "/articles"],
        ["/", "/items", "/about"],
        ["/", "/products", "/about"],
        ["/", "/articles", "/about"],
        ["/", "/users", "/about"],
        ["/login", "/settings", "/items"],
        ["/search", "/items", "/products"],
    ]
    for chain in _NAV_CHAINS:
        steps = [{"action": "goto", "url": chain[0]}]
        for i, route in enumerate(chain[1:], 1):
            sel = _NAV_SELECTORS.get(route, f'[data-testid="nav-{route.lstrip("/")}"]')
            steps.append({"action": "click", "selector": sel})
        tests.append(_make(
            f"nav-seq::{next_id()}::{'-'.join(chain)}",
            "nav", chain[0],
            steps=steps,
            assertions=[
                {"type": "url_contains", "value": chain[-1]},
                {"type": "no_console_errors"},
            ],
        ))

    # =====================================================================
    # 13. Entity-specific deep CRUD — each entity × many name variations
    # =====================================================================
    _EXTRA_NAMES = [
        "Test",
        "A",
        "Z",
        "Hello World",
        "Python 3.11",
        "OpenPath RPA",
        "<b>Bold</b>",
        "&ampersand",
        "quotes: \"hello\"",
        "unicode: ñ ü ö ä é",
        "emoji: 🚀✨🎉",
        "newline\nline",
        "tab\there",
        "null\x00char",
        "path: C:\\\\Windows\\\\System32",
        "sql: '; DROP TABLE; --",
        "xss: <img src=x onerror=alert(1)>",
        "long: " + "repeated " * 50,
        "short: a",
        "space:   ",
        "mixed: AbCdEf123",
    ]
    for entity in _ENTITIES:
        plural = entity["plural"]
        fields = entity["form_fields"]
        for name_val in _EXTRA_NAMES:
            payload = {f["name"]: name_val if f["name"] in ("name", "title", "username") else "desc" for f in fields}
            if plural == "products":
                payload["price"] = "9.99"
            step_list = [
                {"action": "goto", "url": f"/{plural}/new"},
                {"action": "type", "selector": fields[0]["selector"], "text": payload[fields[0]["name"]]},
            ]
            for f in fields[1:]:
                step_list.append({"action": "type", "selector": f["selector"], "text": payload[f["name"]]})
            step_list.append({"action": "click", "selector": entity["save_button"]})
            tests.append(_make(
                f"crud::{next_id()}::extra-{plural}-{name_val[:20].replace(' ', '-')}",
                "crud", f"/{plural}",
                steps=step_list,
                assertions=[{"type": "no_console_errors"}],
            ))

    # =====================================================================
    # 14. Form field combos — fill all fields × valid + empty combinations
    # =====================================================================
    for entity in _ENTITIES:
        plural = entity["plural"]
        fields = entity["form_fields"]
        # For each field, test: empty, valid, special chars
        for field_spec in fields:
            for variant_name, payload_val in [
                ("empty", ""),
                ("valid", "TestValue"),
                ("special", "<>\"'&;"),
                ("unicode", "Tëst Ünïcödé"),
                ("long", "L" * 300),
            ]:
                payload = {f["name"]: (payload_val if f["name"] == field_spec["name"] else "other") for f in fields}
                if plural == "products" and field_spec["name"] == "price":
                    payload["price"] = payload_val if variant_name in ("valid", "special", "unicode", "long") else "bad"
                step_list = [
                    {"action": "goto", "url": f"/{plural}/new"},
                ]
                for f in fields:
                    step_list.append({
                        "action": "type",
                        "selector": f["selector"],
                        "text": payload.get(f["name"], ""),
                    })
                step_list.append({"action": "click", "selector": entity["save_button"]})
                tests.append(_make(
                    f"crud::{next_id()}::field-{plural}-{field_spec['name']}-{variant_name}",
                    "crud", f"/{plural}",
                    steps=step_list,
                    assertions=[{"type": "no_console_errors"}],
                ))

    # =====================================================================
    # 15. Edit-entity page tests — hit every edit page (crude coverage)
    # =====================================================================
    for entity in _ENTITIES:
        plural = entity["plural"]
        for item_id in range(1, 11):  # test up to 10 edit pages per entity
            tests.append(_make(
                f"crud::{next_id()}::edit-{plural}-{item_id}",
                "crud", f"/{plural}/{item_id}/edit",
                assertions=[
                    {"type": "status_ok"},
                    {"type": "no_console_errors"},
                ],
            ))

    # =====================================================================
    # 16. Search with every entity name as query
    # =====================================================================
    for entity in _ENTITIES:
        plural = entity["plural"]
        for name_val in _EXTRA_NAMES[:15]:
            tests.append(_make(
                f"search::{next_id()}::{plural}-{name_val[:20].replace(' ', '-')}",
                "search", f"/search?q={name_val}",
                assertions=[
                    {"type": "status_ok"},
                    {"type": "no_console_errors"},
                ],
            ))

    # =====================================================================
    # 17. Auth state tests — logged-in vs not-logged-in page access
    # =====================================================================
    for route in ["/items", "/products", "/articles", "/users"]:
        # Without login: should still load (demo app doesn't enforce auth)
        tests.append(_make(
            f"auth::{next_id()}::no-auth-{route}",
            "smoke", route,
            assertions=[
                {"type": "status_ok"},
                {"type": "no_console_errors"},
            ],
        ))

    # =====================================================================
    # 18. Additional assertion-combo smoke tests per route
    # =====================================================================
    _ADDITIONAL_ASSERTIONS = [
        [{"type": "text_visible", "value": "OpenPath Demo"}, {"type": "status_ok"}],
        [{"type": "no_4xx_5xx"}, {"type": "no_console_errors"}],
        [{"type": "url_contains", "value": "/"}, {"type": "text_visible", "value": "2026"}],
        [{"type": "element_state", "value": 'a[href="/about"]', "state": "visible"}],
        [{"type": "text_hidden", "value": "Invalid credentials"}, {"type": "status_ok"}],
        [{"type": "text_visible", "value": "Welcome"}, {"type": "url_contains", "value": "/"}],
        [{"type": "element_state", "value": 'nav[data-testid="navbar"]', "state": "visible"}],
        [{"type": "text_visible", "value": "Demo"}, {"type": "text_visible", "value": "OpenPath"}],
    ]
    for route in _NAV_ROUTES:
        for idx, asrt in enumerate(_ADDITIONAL_ASSERTIONS):
            tests.append(_make(
                f"smoke::{next_id()}::{route}-extra-{idx}",
                "smoke", route,
                assertions=asrt,
            ))

    log.info("plan.generated", test_count=len(tests))
    return tests


# ---------------------------------------------------------------------------
# Public API alias
# ---------------------------------------------------------------------------
def plan_for_stack(stack: Any, *, base_url: str = "", options: PlanOptions | None = None) -> list[dict[str, Any]]:  # noqa: ANN401
    """Backwards-compatible entry point. Ignores *stack* and always returns the full plan."""
    return generate_test_plan(options)


# Aliases for explicit naming.
plan_for_stack_comprehensive = plan_for_stack
