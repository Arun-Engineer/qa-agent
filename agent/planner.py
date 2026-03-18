# agent/planner.py
"""
Planner — generates a test plan from a spec using an LLM.

KEY FIX (v3):
  The user's scenarios are extracted from the spec and injected DIRECTLY
  into the LLM system prompt as a hard constraint table — not just as
  advisory text. This bypasses any context-chain failures and forces
  the model to produce one step per user scenario regardless of what
  recon found.
"""
import json
import re
from pathlib import Path

from agent.utils.openai_wrapper import chat_completion as _chat_completion


# ── Scenario extraction ────────────────────────────────────────────────────────

def _extract_scenarios(spec: str) -> list[str]:
    """Pull numbered or bulleted lines out of the spec."""
    scenarios = []
    for line in spec.splitlines():
        s = line.strip()
        if re.match(r"^\d+[\.\)]\s+\S", s):
            scenarios.append(s)
        elif re.match(r"^[-*•]\s+\S", s):
            scenarios.append(s)
    return scenarios


def _detect_login_wall_in_spec(enriched_spec: str) -> bool:
    """Check whether the enriched spec already flagged a login wall."""
    return "LOGIN WALL DETECTED" in enriched_spec or "login_wall_detected: true" in enriched_spec.lower()


# ── Hard-constraint injection ──────────────────────────────────────────────────

def _build_constraint_block(scenarios: list[str], login_wall: bool) -> str:
    """
    Returns a block injected at the TOP of the system prompt.
    Written in imperative language the LLM cannot ignore.
    """
    lines = [
        "=" * 60,
        "HARD CONSTRAINTS — READ BEFORE GENERATING ANY JSON",
        "=" * 60,
        "",
    ]

    if login_wall:
        lines += [
            "CONSTRAINT A — LOGIN WALL:",
            "  The recon crawler was blocked by a login page.",
            "  This means the app requires auth to reach the real features.",
            "  DO NOT generate a plan that only tests the login form.",
            "  INSTEAD:",
            "    Step 0: playwright_runner — tests/test_step0_auth_prerequisite.py",
            "            description: 'Authenticate before testing cart features'",
            "            is_prerequisite: true",
            "    Steps 1-N: one step per USER SCENARIO listed below.",
            "",
        ]

    if scenarios:
        n = len(scenarios)
        lines += [
            f"CONSTRAINT B — MANDATORY SCENARIO COVERAGE ({n} scenarios):",
            f"  The user explicitly listed {n} test scenarios.",
            f"  Your JSON 'steps' array MUST contain EXACTLY {n} entries",
            f"  (plus the auth prerequisite if CONSTRAINT A applies).",
            "  Each entry maps 1-to-1 to the scenarios below.",
            "  DO NOT collapse, merge, skip, or reorder them.",
            "",
            "  SCENARIO MAP (scenario index -> required step):",
        ]
        for i, s in enumerate(scenarios, 1):
            lines.append(f"    [{i:02d}] {s}")
        lines += [
            "",
            f"  If your steps array has fewer than {n} scenario steps = WRONG OUTPUT.",
            f"  The only acceptable step count is {n} scenario steps",
            "  (+ 1 auth step if login wall).",
            "",
        ]

    lines += [
        "CONSTRAINT C — NO LOGIN-ONLY PLANS:",
        "  A plan whose steps only test login/auth forms when the user asked",
        "  for cart/checkout/product tests is a CRITICAL FAILURE.",
        "  Treat login as infrastructure (Step 0), not as the test subject.",
        "",
        "=" * 60,
        "",
    ]
    return "\n".join(lines)


# ── Planner class ──────────────────────────────────────────────────────────────


def _extract_credentials(spec: str) -> dict:
    """
    Extract mobile/OTP/login credentials from spec text.
    These are AUTH CONFIG for the test session — NOT test data.

    Returns dict like:
      {"mobile": "8825594525", "otp": "123456", "found": True}
    """
    import re as _re
    creds = {}
    # mobile: XXXXXXXXXX or phone: XXXXXXXXXX
    m = _re.search(r"(?:mobile|phone|number)\s*[:\-]\s*(\d{10,})", spec, _re.IGNORECASE)
    if m:
        creds["mobile"] = m.group(1).strip()
    # OTP: XXXXXX
    m = _re.search(r"otp\s*[:\-]\s*(\d{4,8})", spec, _re.IGNORECASE)
    if m:
        creds["otp"] = m.group(1).strip()
    # password: XXXX
    m = _re.search(r"password\s*[:\-]\s*(\S+)", spec, _re.IGNORECASE)
    if m:
        creds["password"] = m.group(1).strip()
    creds["found"] = bool(creds)
    return creds


def _inject_creds_as_env(creds: dict):
    """Set credentials as env vars so Playwright tests can use them."""
    import os as _os
    if creds.get("mobile"):
        _os.environ["JIOMART_PHONE"]    = creds["mobile"]
        _os.environ["TEST_MOBILE"]      = creds["mobile"]
    if creds.get("otp"):
        _os.environ["JIOMART_OTP"]      = creds["otp"]
        _os.environ["TEST_OTP"]         = creds["otp"]
    if creds.get("password"):
        _os.environ["TEST_PASSWORD"]    = creds["password"]


def _strip_creds_from_spec(spec: str) -> str:
    """
    Remove credential lines from spec before sending to LLM.
    Prevents LLM from treating credentials as test boundary data.
    Replaces them with a note that auth is handled as a prerequisite.
    """
    import re as _re
    # Remove lines containing credentials
    lines = spec.splitlines()
    clean_lines = []
    cred_patterns = [
        r"(?:mobile|phone)\s*[:\-]\s*\d+",
        r"otp\s*[:\-]\s*\d+",
        r"(?:use this login|login credentials|credentials if required)",
        r"password\s*[:\-]\s*\S+",
    ]
    removed = False
    for line in lines:
        is_cred = any(_re.search(p, line, _re.IGNORECASE) for p in cred_patterns)
        if is_cred:
            removed = True
        else:
            clean_lines.append(line)

    result = "\n".join(clean_lines)
    if removed:
        result += "\n\n[AUTH NOTE: Login credentials have been extracted and set as environment variables. The agent will handle authentication as Step 0 prerequisite automatically.]"
    return result


class Planner:
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        prompt_path: str = "config/prompts/generate_tests.md",
    ):
        self.model = model
        root = Path(__file__).resolve().parents[1]
        self._prompt_path = root / prompt_path
        # Load prompt fresh from disk every time so a restart isn't needed
        # (we re-read in generate_plan)

    def _load_prompt(self) -> str:
        return self._prompt_path.read_text(encoding="utf-8")

    def chat_completion(self, *, messages, **kwargs):
        kwargs.pop("messages", None)
        return _chat_completion(
            messages=messages,
            model=self.model,
            service_name="qa-agent-planner1",
            **kwargs,
        )

    def generate_plan(self, spec: str, context: dict | None = None) -> dict:
        # Extract and strip credentials before sending to LLM
        # Prevents LLM treating mobile/OTP as boundary test data
        creds = _extract_credentials(spec)
        if creds.get("found"):
            _inject_creds_as_env(creds)
            spec = _strip_creds_from_spec(spec)
            if context is None:
                context = {}
            context["auth_credentials"] = creds
            context["login_wall_detected"] = True  # credentials = auth needed

        """
        Generate a test execution plan.

        Strategy (v3 — belt-and-suspenders):
          1. Extract scenarios directly from spec text (no context chain needed)
          2. Detect login wall from enriched spec text (no context chain needed)
          3. Inject hard constraints at TOP of system prompt
          4. Inject scenario reminder into user message
          5. Post-validate output and repair if model still went off-rails
        """
        context = context or {}

        # 1. Extract ground truth directly from spec — doesn't rely on context chain
        scenarios = _extract_scenarios(spec)
        login_wall = (
            context.get("login_wall_detected", False)
            or _detect_login_wall_in_spec(spec)
        )

        # 2. Build constraint block
        constraint_block = _build_constraint_block(scenarios, login_wall)

        # 3. Reload system prompt fresh from disk
        base_prompt = self._load_prompt()
        system_prompt = constraint_block + base_prompt

        # 4. Build user message with scenario reminder
        user_msg = _build_user_message(spec, scenarios, login_wall)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_msg},
        ]

        try:
            resp = self.chat_completion(
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = (resp.choices[0].message.content or "").strip()
            plan = json.loads(content)
        except Exception as e:
            return {"error": str(e), "steps": [], "goal": "Plan generation failed"}

        # 5. Post-validate and repair
        plan = _validate_and_repair(plan, scenarios, login_wall, context)
        return plan

    def _parse_response(self, response) -> dict:
        content = response.content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON from LLM", "raw": content}


# ── User message builder ───────────────────────────────────────────────────────

def _build_user_message(spec: str, scenarios: list[str], login_wall: bool) -> str:
    parts = []

    if login_wall and scenarios:
        n = len(scenarios)
        parts.append(
            f"REMINDER: Login wall detected. Generate {n} cart/feature test steps "
            f"(+ 1 auth prerequisite as Step 0). The {n} scenarios to cover are numbered "
            f"in the spec below. Do NOT produce login-only tests."
        )
    elif scenarios:
        n = len(scenarios)
        parts.append(
            f"REMINDER: Generate exactly {n} test steps — one per numbered scenario in the spec."
        )

    parts.append(spec)
    return "\n\n".join(parts)


# ── Post-generation validation and repair ──────────────────────────────────────

def _validate_and_repair(
    plan: dict,
    scenarios: list[str],
    login_wall: bool,
    context: dict,
) -> dict:
    if not isinstance(plan, dict):
        return plan

    steps = plan.get("steps", [])
    assumptions = plan.get("assumptions", [])

    # ── Detect if LLM produced a login-only plan ──
    is_login_only = _is_login_only_plan(steps)

    if is_login_only and scenarios:
        # The model ignored the constraint — rebuild steps from scratch
        assumptions.append(
            "WARNING: LLM generated a login-only plan despite user specifying cart scenarios. "
            "Steps have been auto-generated from user scenario list as a fallback."
        )
        base_url = context.get("base_url", "https://www.jiomart.com")
        steps = _scaffold_steps_from_scenarios(scenarios, login_wall, base_url)
        plan["goal"] = plan.get("goal", "").replace("Login", "Cart").replace("login", "cart")
        if "cart" not in plan.get("goal", "").lower() and scenarios:
            plan["goal"] = "Test cart and shopping functionality per user spec"

    # ── Ensure auth prerequisite exists when login wall detected ──
    elif login_wall and scenarios:
        has_prereq = any(
            s.get("args", {}).get("is_prerequisite")
            or "prerequisite" in s.get("args", {}).get("path", "").lower()
            or "auth" in s.get("args", {}).get("path", "").lower()
            for s in steps
        )
        if not has_prereq:
            prereq = _make_prereq_step(context.get("base_url", ""))
            steps.insert(0, prereq)
            assumptions.append("Auth prerequisite step auto-inserted as Step 0 (login wall detected).")

    # ── Warn if step count is still below scenario count ──
    if scenarios:
        non_prereq = [s for s in steps if not s.get("args", {}).get("is_prerequisite")]
        if len(non_prereq) < len(scenarios):
            assumptions.append(
                f"WARNING: {len(scenarios)} scenarios requested but only "
                f"{len(non_prereq)} non-prereq steps generated. Some scenarios may be missing."
            )

    plan["steps"] = steps
    plan["assumptions"] = assumptions
    plan["user_scenario_count"] = len(scenarios)
    plan["login_wall_detected"] = login_wall
    return plan


def _is_login_only_plan(steps: list[dict]) -> bool:
    """Returns True if every step is about login and nothing else."""
    if not steps:
        return False
    login_kws = {"login", "auth", "signin", "sign_in", "credential"}
    cart_kws   = {"cart", "checkout", "product", "quantity", "coupon", "order",
                  "category", "badge", "price", "stock", "promo", "refresh"}
    login_count = 0
    cart_count  = 0
    for step in steps:
        path = step.get("args", {}).get("path", "").lower()
        desc = step.get("args", {}).get("description", "").lower()
        text = path + " " + desc
        if any(kw in text for kw in login_kws):
            login_count += 1
        if any(kw in text for kw in cart_kws):
            cart_count += 1
    return login_count > 0 and cart_count == 0


def _scaffold_steps_from_scenarios(
    scenarios: list[str],
    login_wall: bool,
    base_url: str,
) -> list[dict]:
    """
    Last-resort fallback: build steps directly from scenario text.
    Produces valid step objects the orchestrator can execute.
    """
    steps = []

    if login_wall:
        steps.append(_make_prereq_step(base_url))

    slug_re = re.compile(r"[^a-z0-9]+")
    for i, scenario in enumerate(scenarios, 1):
        # Strip leading number/bullet
        clean = re.sub(r"^\d+[\.\)]\s+|^[-*•]\s+", "", scenario).strip()
        slug = slug_re.sub("_", clean.lower())[:40].strip("_")
        steps.append({
            "tool": "playwright_runner",
            "args": {
                "path": f"tests/test_{i:02d}_{slug}.py",
                "description": clean,
                "priority": "P1",
                "severity": "high",
                "tags": ["ui", "cart", "e2e"],
                "requires_auth": login_wall,
                "is_prerequisite": False,
                "linked_scenario": scenario,
                "base_url": base_url,
            },
        })
    return steps


def _make_prereq_step(base_url: str) -> dict:
    return {
        "tool": "playwright_runner",
        "args": {
            "path": "tests/test_step0_auth_prerequisite.py",
            "description": "Authenticate — login prerequisite before cart tests",
            "priority": "P0",
            "severity": "critical",
            "tags": ["auth", "prerequisite"],
            "requires_auth": False,
            "is_prerequisite": True,
            "linked_scenario": "Login prerequisite",
            "base_url": base_url,
        },
    }


# ── CLI entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from agent.understanding_layer import enrich_spec_with_understanding

    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    args = parser.parse_args()

    enriched, ctx = enrich_spec_with_understanding(args.spec)
    context = {
        "login_wall_detected": ctx.login_wall_detected,
        "user_scenarios":      ctx.user_scenarios,
        "base_url":            ctx.base_url,
    }

    planner = Planner()
    plan = planner.generate_plan(enriched, context=context)

    print(f"Goal          : {plan.get('goal')}")
    print(f"Login wall    : {plan.get('login_wall_detected')}")
    print(f"User scenarios: {plan.get('user_scenario_count')}")
    print(f"Steps         : {len(plan.get('steps', []))}")
    print()
    for i, step in enumerate(plan.get("steps", [])):
        a = step.get("args", {})
        print(f"  [{i:02d}] {step['tool']} | {a.get('description','')}")
        print(f"        path={a.get('path','')} prereq={a.get('is_prerequisite',False)}")
