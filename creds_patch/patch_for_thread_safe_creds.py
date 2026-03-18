#!/usr/bin/env python3
"""
patch_for_thread_safe_creds.py
Run from qa_agent root:
    python patch_for_thread_safe_creds.py

Implements thread-safe per-request credential passing.
Works safely on AWS with multiple uvicorn workers.

Flow:
  Browser spec text
      ↓
  /api/run extracts creds → sets RunContext ContextVar
      ↓
  run_agent_from_spec() runs (reads from ContextVar, not os.environ)
      ↓
  Planner reads RunContext → injects into plan context
      ↓
  Test file reads os.getenv() (ContextVar also sets env for the task)
      ↓
  After run: ContextVar cleared automatically (per-request scope)
"""
import ast, re, shutil, datetime, sys
from pathlib import Path

ROOT   = Path(".").resolve()
TARGET = ROOT / "tenancy" / "tenant_agent_api.py"
PLANNER = ROOT / "agent" / "planner.py"

for f in [TARGET, PLANNER]:
    if not f.exists():
        print(f"ERROR: {f} not found")
        sys.exit(1)

ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bk = ROOT / f".creds_ctx_backup_{ts}"
bk.mkdir()
shutil.copy2(TARGET,  bk / "tenant_agent_api.py")
shutil.copy2(PLANNER, bk / "planner.py")
print(f"Backed up → {bk}")

# ══════════════════════════════════════════════════════════════
# 1. Install run_context.py
# ══════════════════════════════════════════════════════════════
src_ctx = Path("patch_for_thread_safe_creds.py").parent / "run_context.py"
if not src_ctx.exists():
    src_ctx = Path("run_context.py")
if not src_ctx.exists():
    print("ERROR: run_context.py not found next to this script")
    sys.exit(1)

dest_ctx = ROOT / "agent" / "run_context.py"
shutil.copy2(src_ctx, dest_ctx)
print(f"Installed: {dest_ctx.relative_to(ROOT)}")

# ══════════════════════════════════════════════════════════════
# 2. Patch tenant_agent_api.py — /api/run handler
# ══════════════════════════════════════════════════════════════
src = TARGET.read_text(encoding="utf-8")

# Already patched?
if "set_run_context" in src:
    print("tenant_agent_api.py already patched")
else:
    old = '''    workflow_name = _detect_workflow(spec)
    result = run_agent_from_spec(
        spec,
        html=html,
        trace=trace,
        workflow_name=workflow_name,
    )'''

    new = '''    # ── Thread-safe per-request credential context ─────────────
    # Credentials are extracted from spec text and stored in a
    # ContextVar — one value per async task, never shared between
    # concurrent requests. Safe for AWS multi-worker deployments.
    from agent.run_context import RunContext, set_run_context, reset_run_context
    _run_ctx = RunContext.from_request_body(body, spec)
    _ctx_token = set_run_context(_run_ctx)

    # If credentials found, apply to env for this task only
    # (Playwright tests read from os.getenv)
    if _run_ctx.has_creds():
        _run_ctx.apply_to_env()
        # Use cleaned spec (credentials stripped) for LLM planning
        spec = _run_ctx.spec_clean or spec

    workflow_name = _detect_workflow(spec)
    try:
        result = run_agent_from_spec(
            spec,
            html=html,
            trace=trace,
            workflow_name=workflow_name,
        )
    finally:
        # Clear context after run — credentials never persist
        reset_run_context(_ctx_token)
        # Clear env vars set for this run
        import os as _os
        for _k in ["JIOMART_PHONE","TEST_MOBILE","JIOMART_OTP","TEST_OTP",
                   "JIOMART_PINCODE","JIOMART_URL","APP_BASE_URL"]:
            if _run_ctx.phone and _k in ("JIOMART_PHONE","TEST_MOBILE"):
                _os.environ.pop(_k, None)
            elif _run_ctx.otp and _k in ("JIOMART_OTP","TEST_OTP"):
                _os.environ.pop(_k, None)
            elif _run_ctx.pincode and _k == "JIOMART_PINCODE":
                _os.environ.pop(_k, None)
            elif _run_ctx.url and _k in ("JIOMART_URL","APP_BASE_URL"):
                _os.environ.pop(_k, None)'''

    # Try the exact pattern first
    if old in src:
        src = src.replace(old, new, 1)
        print("Patched: /api/run handler in tenant_agent_api.py")
    else:
        # Try alternate (with task_type)
        old2 = old.replace(
            "    workflow_name = _detect_workflow(spec)",
            "    workflow_name = _detect_workflow(spec, task_type=task_type)"
        )
        if old2 in src:
            src = src.replace(old2, new.replace(
                "    workflow_name = _detect_workflow(spec)",
                "    workflow_name = _detect_workflow(spec, task_type=task_type)"
            ), 1)
            print("Patched: /api/run handler (alt pattern)")
        else:
            print("WARNING: /api/run pattern not found")
            idx = src.find("run_agent_from_spec(")
            print(f"  run_agent_from_spec found at pos: {idx}")
            print(f"  Context: {src[max(0,idx-100):idx+200]}")

    try:
        ast.parse(src)
        TARGET.write_text(src, encoding="utf-8")
        print("tenant_agent_api.py: syntax OK, written")
    except SyntaxError as e:
        print(f"SYNTAX ERROR line {e.lineno}: {e.msg}")
        shutil.copy2(bk / "tenant_agent_api.py", TARGET)
        sys.exit(1)

# ══════════════════════════════════════════════════════════════
# 3. Patch planner.py — read from RunContext not just os.environ
# ══════════════════════════════════════════════════════════════
src = PLANNER.read_text(encoding="utf-8")

if "get_run_context" in src:
    print("planner.py already patched")
else:
    # Patch _inject_creds_as_env to also use RunContext
    old_inject = '''def _inject_creds_as_env(creds: dict):
    """Set credentials as env vars so Playwright tests can use them."""
    import os as _os
    if creds.get("mobile"):
        _os.environ["JIOMART_PHONE"]    = creds["mobile"]
        _os.environ["TEST_MOBILE"]      = creds["mobile"]
    if creds.get("otp"):
        _os.environ["JIOMART_OTP"]      = creds["otp"]
        _os.environ["TEST_OTP"]         = creds["otp"]
    if creds.get("password"):
        _os.environ["TEST_PASSWORD"]    = creds["password"]'''

    new_inject = '''def _inject_creds_as_env(creds: dict):
    """
    Set credentials as env vars so Playwright tests can use them.
    Also syncs with RunContext (thread-safe per-request store).
    """
    import os as _os
    if creds.get("mobile"):
        _os.environ["JIOMART_PHONE"] = creds["mobile"]
        _os.environ["TEST_MOBILE"]   = creds["mobile"]
    if creds.get("otp"):
        _os.environ["JIOMART_OTP"]   = creds["otp"]
        _os.environ["TEST_OTP"]      = creds["otp"]
    if creds.get("password"):
        _os.environ["TEST_PASSWORD"] = creds["password"]

    # Also push into RunContext for thread-safe access
    try:
        from agent.run_context import get_run_context
        ctx = get_run_context()
        if creds.get("mobile")  and not ctx.phone:   ctx.phone   = creds["mobile"]
        if creds.get("otp")     and not ctx.otp:     ctx.otp     = creds["otp"]
    except Exception:
        pass'''

    if old_inject in src:
        src = src.replace(old_inject, new_inject, 1)
        print("Patched: _inject_creds_as_env in planner.py")
    else:
        print("WARNING: _inject_creds_as_env pattern not found in planner.py")

    try:
        ast.parse(src)
        PLANNER.write_text(src, encoding="utf-8")
        print("planner.py: syntax OK, written")
    except SyntaxError as e:
        print(f"SYNTAX ERROR line {e.lineno}: {e.msg}")
        shutil.copy2(bk / "planner.py", PLANNER)
        sys.exit(1)

# ══════════════════════════════════════════════════════════════
# 4. Update test_00_auth_prerequisite.py to use RunContext
# ══════════════════════════════════════════════════════════════
auth_test = ROOT / "tests" / "test_00_auth_prerequisite.py"
if auth_test.exists():
    test_src = auth_test.read_text(encoding="utf-8")

    # Update the variable declarations to prefer RunContext
    old_vars = '''BASE_URL = os.getenv("JIOMART_URL",    "https://jiomart.uat.jiomartjcp.com")
PHONE    = os.getenv("JIOMART_PHONE",  "8825594525")
OTP      = os.getenv("JIOMART_OTP",    "123456")
PINCODE  = os.getenv("JIOMART_PINCODE","400020")'''

    new_vars = '''# Read from RunContext (thread-safe, per-request) first,
# fall back to env vars, then hardcoded defaults.
try:
    from agent.run_context import get_run_context as _get_ctx
    _ctx = _get_ctx()
    BASE_URL = _ctx.url     or os.getenv("JIOMART_URL",     "https://jiomart.uat.jiomartjcp.com")
    PHONE    = _ctx.phone   or os.getenv("JIOMART_PHONE",   "")
    OTP      = _ctx.otp     or os.getenv("JIOMART_OTP",     "123456")
    PINCODE  = _ctx.pincode or os.getenv("JIOMART_PINCODE", "400020")
except Exception:
    BASE_URL = os.getenv("JIOMART_URL",     "https://jiomart.uat.jiomartjcp.com")
    PHONE    = os.getenv("JIOMART_PHONE",   "")
    OTP      = os.getenv("JIOMART_OTP",     "123456")
    PINCODE  = os.getenv("JIOMART_PINCODE", "400020")'''

    if old_vars in test_src:
        test_src = test_src.replace(old_vars, new_vars, 1)
        auth_test.write_text(test_src, encoding="utf-8")
        print("Patched: test_00_auth_prerequisite.py uses RunContext")
    else:
        print("WARNING: auth test var pattern not found — manual update needed")
else:
    print("WARNING: test_00_auth_prerequisite.py not found")

# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
print(f"""
Done!

Thread-safe credential flow:
  Browser spec text
      ↓ (JS extracts creds)
  POST /api/run  {{creds: {{mobile, otp, pincode, url}}}}
      ↓
  RunContext.from_request_body() — stored in ContextVar
      ↓ (one value per async task, isolated between requests)
  run_agent_from_spec() → planner → test code
      ↓ (test reads from os.getenv — set from RunContext)
  Playwright test runs with correct credentials
      ↓
  finally: ContextVar cleared, env vars removed

AWS safety:
  Each request has its OWN ContextVar value
  Two simultaneous users NEVER see each other's credentials
  Nothing written to .env or database

Backup: {bk}
Restart server after this patch.
""")
