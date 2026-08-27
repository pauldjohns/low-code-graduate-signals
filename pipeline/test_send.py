#!/usr/bin/env python3
"""
test_send.py - prove Gmail API + OAuth works end to end, safely.

Sends ONE plain-text email to yourself (the authed mailbox). No worklist, no
templates, no external recipients. Run this once after the Google Cloud setup to
confirm auth + send before we build the real sender.

  python3 pipeline/test_send.py                 # sends to the authed address
  python3 pipeline/test_send.py you@example.com # or an explicit recipient (use your own)
"""
import sys, gmail_auth

# Under the send-only scope we can't read the mailbox address from the API, so default the
# self-test recipient explicitly. Override with an arg if you want it elsewhere.
DEFAULT_TO = "sender@example.com"

def main():
    svc = gmail_auth.service()
    me = gmail_auth.whoami(svc)              # None under send-only scope — fine
    to = sys.argv[1] if len(sys.argv) > 1 else (me or DEFAULT_TO)
    print(f"authed as: {me or '(send-only scope)'}")
    print(f"sending test to: {to}")
    mid = gmail_auth.send(
        svc, to,
        subject="[outreach-engine] Gmail API test",
        body_text=("This is a send-path test from the outreach pipeline.\n\n"
                   "If you're reading this in your inbox, Gmail API + OAuth works and "
                   "we're clear to build the real sender in dry-run.\n"),
        from_addr=me,
    )
    print(f"sent OK - message id {mid}")
    print("check the inbox. If it landed in Primary (not Spam), the send path is good.")

if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
