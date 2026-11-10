"""Set this PC up for the FO3ID server: what README "Setup" does by hand. setup.exe runs it (elevated)
once the files are in place; it can run again at any time (each step skips what is done already).

    python installer/setup_pc.py --game "D:\\FO3ID\\32795" [--dry-run]

1. certs/server.pem: a self-signed certificate for the client's API host APSEODESK172
2. the hosts file: 127.0.0.1 APSEODESK172
3. gamedata/fifadb.sqlite: the player database, from the client (extract_fifadb.py)
4. the client's patches (the game closed): this server's cards, clubs and patches
   (tools/fo4_season.py install), the Kontrol screen (tools/control_presets.py) and the PS5 pad
   (buttonData.ini knows the PS4 "Wireless Controller" only; the DualSense has the same layout)
A step that fails is logged (installer/setup.log) and the next one still runs.
"""
import argparse, datetime, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOST = "APSEODESK172"
CERT = os.path.join(ROOT, "certs", "server.pem")
FIFADB = os.path.join(ROOT, "gamedata", "fifadb.sqlite")
HOSTS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers", "etc", "hosts")
LOG = os.path.join(HERE, "setup.log")
PAD = b'\tAddAlias "Wireless Controller"\r\n'  # the PS4 pad's entry in buttonData.ini


def log(msg):
    print(msg, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def make_cert(path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
            .sign(key, hashes.SHA256()))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:  # cert then key, as http_config's load_cert_chain(CERT) reads it
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))


def hosts_line(text):
    """The hosts file's text with the API host on 127.0.0.1, or None when it is there already."""
    if re.search(rf"^\s*127\.0\.0\.1\s+(?:\S+\s+)*{HOST}\s*(?:#.*)?$", text, re.M | re.I):
        return None
    return text + ("" if text.endswith("\n") or not text else "\r\n") + f"127.0.0.1 {HOST}\r\n"


def pad_alias(ini):
    """buttonData.ini with the PS5 pads next to the PS4 one, or None (there already, or unknown file)."""
    if b"DualSense" in ini or PAD not in ini:
        return None
    return ini.replace(PAD, PAD + PAD.replace(b'"W', b'"DualSense W') + PAD.replace(b'"W', b'"DualSense Edge W'), 1)


def game_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq fifazf.exe", "/NH"], capture_output=True, text=True).stdout
    return "fifazf.exe" in out.lower()


def run(*args):
    """A repo script with this Python; True when it ended well (its output goes to the log)."""
    p = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "FO3_GAME": GAME_DIR})
    for line in (p.stdout + p.stderr).strip().splitlines()[-15:]:
        log(f"  {line}")
    return p.returncode == 0


def main():
    global GAME_DIR
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game", default=os.environ.get("FO3_GAME", r"D:\FO3ID\32795"), help="the FO3 ID client's folder")
    ap.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    a = ap.parse_args()
    GAME_DIR = a.game
    log(f"setup: server {ROOT}, client {a.game}{' (dry run)' if a.dry_run else ''}")
    if not os.path.isfile(os.path.join(a.game, "fifazf.exe")):
        log(f"no fifazf.exe in {a.game}: install the FO3 ID client there, or run setup again with its folder")
        return 1
    bad = 0

    if os.path.isfile(CERT):
        log("certificate: there already")
    elif a.dry_run:
        log(f"certificate: would make {CERT}")
    else:
        make_cert(CERT)
        log(f"certificate: made {CERT}")

    try:
        with open(HOSTS, "rb") as f:
            text = f.read().decode("utf-8", "replace")
        new = hosts_line(text)
        if new is None:
            log(f"hosts: 127.0.0.1 {HOST} there already")
        elif a.dry_run:
            log(f"hosts: would add 127.0.0.1 {HOST}")
        else:
            with open(HOSTS, "ab") as f:  # append only: the rest of the file stays as it is
                f.write(new[len(text):].encode("ascii"))
            log(f"hosts: added 127.0.0.1 {HOST}")
    except OSError as e:  # not elevated, or an antivirus guards the file
        log(f"hosts: could not add 127.0.0.1 {HOST} ({e}); add it by hand as administrator")
        bad += 1

    if os.path.isfile(FIFADB):
        log("player database: there already")
    elif a.dry_run:
        log(f"player database: would extract {FIFADB}")
    elif run(os.path.join(ROOT, "extract_fifadb.py"), a.game, FIFADB):
        log("player database: extracted")
    else:
        log("player database: extraction failed")
        bad += 1

    if game_running():
        log("client patches: skipped, the game is running (play.bat installs the cards next time; run setup again for the rest)")
        return 1
    if a.dry_run:
        log("client patches: would run fo4_season.py install --if-needed, control_presets.py and the PS5 pad")
        return 0
    for step, args in (("cards, clubs and patches", ("tools/fo4_season.py", "install", "--if-needed", "--game", a.game)),
                       ("Kontrol screen", ("tools/control_presets.py", "--game", a.game))):
        ok = run(*[os.path.join(ROOT, args[0]), *args[1:]])
        log(f"client {step}: {'done' if ok else 'failed'}")
        bad += not ok
    ini = os.path.join(a.game, "buttonData.ini")
    try:
        with open(ini, "rb") as f:
            new = pad_alias(f.read())
        if new is not None:
            with open(ini, "wb") as f:
                f.write(new)
        log(f"PS5 pad: {'added' if new is not None else 'there already'}")
    except OSError as e:
        log(f"PS5 pad: {e}")
        bad += 1
    log("setup done" + (f", {bad} step(s) failed: see above" if bad else ""))
    return 1 if bad else 0


def selftest():
    assert hosts_line("127.0.0.1 localhost\r\n") == f"127.0.0.1 localhost\r\n127.0.0.1 {HOST}\r\n"
    assert hosts_line(f"# x\n127.0.0.1\t{HOST}\n") is None and hosts_line(f"127.0.0.1 a {HOST} # FO3\n") is None
    assert hosts_line(f"#127.0.0.1 {HOST}") is not None and hosts_line("") == f"127.0.0.1 {HOST}\r\n"
    ini = b'x\r\n\tAddAlias "Wireless Controller"\r\n\tAddMap y\r\n'
    out = pad_alias(ini)
    assert out.count(b"DualSense") == 2 and out.endswith(b'"DualSense Edge Wireless Controller"\r\n\tAddMap y\r\n')
    assert pad_alias(out) is None and pad_alias(b"other") is None


GAME_DIR = ""
if __name__ == "__main__":
    selftest()
    sys.exit(main())
