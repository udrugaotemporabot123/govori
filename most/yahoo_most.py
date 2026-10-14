"""Yahoo -> Gmail most za Vesninu ploču.

Čita nove mailove iz Vesninog Yahoo sandučića (IMAP, samo čitanje, ne označava ih
pročitanima) i svaki prosljeđuje na udruga.otempora+yahoo@gmail.com preko Yahoo SMTP-a.
Ploča ih ondje nađe i prikaže u tabu Yahoo. Ništa iz mailova se ne ispisuje u log.
"""
import email, email.policy, html, imaplib, json, os, re, smtplib, ssl, sys, time
from email.message import EmailMessage
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime, timedelta, timezone

USER = os.environ["YAHOO_USER"]
PASS = os.environ["YAHOO_APP_PASSWORD"]
TO = os.environ.get("YAHOO_FORWARD_TO", "udruga.otempora+yahoo@gmail.com")
STATE = os.environ.get("STATE_FILE", "stanje/yahoo.json")
FIRST_DAYS = 3          # prvi put: samo mailovi iz zadnja 3 dana
MAX_PER_RUN = 25
SKIP_FROM = {USER.lower(), "udruga.otempora@gmail.com"}


def dec(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def html_to_text(raw):
    raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", raw)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(re.sub(r"<[^>]+>", "", raw))).strip()


def part_text(part):
    try:
        return part.get_content()
    except Exception:
        return (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")


def plain_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                return part_text(part)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return html_to_text(part_text(part))
        return ""
    t = part_text(msg)
    return html_to_text(t) if msg.get_content_type() == "text/html" else t


def main():
    try:
        state = json.load(open(STATE))
    except Exception:
        state = {}
    imap = imaplib.IMAP4_SSL("imap.mail.yahoo.com", 993)
    imap.login(USER, PASS)
    imap.select("INBOX", readonly=True)
    validity = imap.response("UIDVALIDITY")[1][0]
    validity = validity.decode() if isinstance(validity, bytes) else str(validity)
    if state.get("uidvalidity") != validity:
        state = {"uidvalidity": validity, "last_uid": 0}
        since = (datetime.now(timezone.utc) - timedelta(days=FIRST_DAYS)).strftime("%d-%b-%Y")
        typ, data = imap.uid("SEARCH", None, "SINCE", since)
    else:
        typ, data = imap.uid("SEARCH", None, "UID", f"{state['last_uid'] + 1}:*")
    uids = sorted(int(x) for x in (data[0] or b"").split() if int(x) > state.get("last_uid", 0))
    print(f"novih: {len(uids)}")
    if not uids:
        imap.logout(); save(state); return
    smtp = smtplib.SMTP_SSL("smtp.mail.yahoo.com", 465, context=ssl.create_default_context())
    smtp.login(USER, PASS)
    sent = 0
    for uid in uids[:MAX_PER_RUN]:
        typ, msgdata = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        raw = next((p[1] for p in msgdata if isinstance(p, tuple)), None)
        state["last_uid"] = uid
        if not raw:
            continue
        orig = email.message_from_bytes(raw, policy=email.policy.default)
        frm = dec(orig.get("From"))
        if parseaddr(frm)[1].lower() in SKIP_FROM:
            continue
        fwd = EmailMessage()
        fwd["From"] = USER
        fwd["To"] = TO
        fwd["Subject"] = dec(orig.get("Subject")) or "(bez predmeta)"
        rt = dec(orig.get("Reply-To")) or frm
        if rt:
            fwd["Reply-To"] = rt
        fwd["X-Yahoo-Most"] = "1"
        body = (
            f"YAHOO-OD: {frm}\n"
            f"YAHOO-ZA: {dec(orig.get('To'))}\n"
            f"YAHOO-CC: {dec(orig.get('Cc'))}\n"
            f"YAHOO-DATUM: {orig.get('Date', '')}\n"
            f"YAHOO-ID: {orig.get('Message-ID', '')}\n"
            "-----\n" + (plain_text(orig) or "")[:60000]
        )
        fwd.set_content(body)
        fwd.add_attachment(raw, maintype="message", subtype="rfc822", filename="original.eml") if len(raw) < 15_000_000 else None
        smtp.send_message(fwd)
        sent += 1
        time.sleep(1)
    smtp.quit(); imap.logout()
    print(f"proslijeđeno: {sent}")
    save(state)


def save(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, "w"))


if __name__ == "__main__":
    import email.policy  # noqa
    try:
        main()
    except imaplib.IMAP4.error as e:
        print("Yahoo prijava nije uspjela (provjeri app lozinku).", file=sys.stderr); sys.exit(1)
