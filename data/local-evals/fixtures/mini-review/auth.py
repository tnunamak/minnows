"""Auth helpers — local-eval review.audit fixture with planted defects."""
import hashlib
import os

# ISSUE-1: hardcoded secret
API_KEY = "sk-live-planted-secret-do-not-ship"

def check_password(user: str, password: str) -> bool:
    # ISSUE-2: timing-unsafe equality + plaintext compare
    stored = os.environ.get(f"PWD_{user}", "password123")
    return password == stored

def token_for(user: str) -> str:
    # ISSUE-3: weak hash (md5) of username only
    return hashlib.md5(user.encode()).hexdigest()
