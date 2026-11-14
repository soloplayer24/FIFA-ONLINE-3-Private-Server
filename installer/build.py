"""Build dist/FO3ID-Server-Setup-<version>.exe (installer/fo3id.iss): the repo's files, the launcher
and a Python of its own, so a PC needs nothing installed but the game.

    python installer/build.py [--iscc PATH]

Fetches once (into installer/cache, and pip's cache): the Python embeddable zip from python.org and
the packages the server imports from PyPI. Needs Inno Setup 6 (ISCC.exe) and the .NET 4 csc.exe
every Windows has. No game data goes in (gamedata/, certs/ and backup/ are made on each PC).
"""
import argparse, os, shutil, subprocess, sys, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE, BUILD = os.path.join(HERE, "cache"), os.path.join(HERE, "build")
STAGE = os.path.join(BUILD, "app")
PY = "3.12.10"  # the version the server is tested with
PY_ZIP = f"python-{PY}-embed-amd64.zip"
PY_URL = f"https://www.python.org/ftp/python/{PY}/{PY_ZIP}"
# what the server and its tools import (cryptography: TLS + the client's DBs; the rest: pictures and 3D faces)
PACKAGES = ["cryptography==48.0.0", "numpy==2.4.6", "pillow==12.2.0", "opencv-python-headless"]
SKIP = ("analysis/", "reports/", "docs/", "captures/", "test_", "run_tests.py", ".git")  # not needed to play
CSC = r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
ISCC = (r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"))  # its "for me only" install


def repo_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [f for f in out.decode("utf-8").split("\0") if f and not f.startswith(SKIP)
            and not f.startswith("installer/") or f == "installer/setup_pc.py"]


def python_pth():
    """python312._pth: the stdlib, the packages, the server's folder and its tools (a ._pth leaves the
    script's own folder off sys.path; the stdlib first, so tools/re/abc.py never shadows abc)."""
    return "\n".join(["python312.zip", ".", r"Lib\site-packages", "..", r"..\tools", "import site", ""])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--iscc", default=next((p for p in ISCC if os.path.isfile(p)), None))
    a = ap.parse_args()
    if not a.iscc:
        raise SystemExit("Inno Setup 6 not found (https://jrsoftware.org/isdl.php), or pass --iscc")
    version = subprocess.run(["git", "describe", "--always", "--dirty"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
    shutil.rmtree(BUILD, ignore_errors=True)
    for f in repo_files():
        os.makedirs(os.path.dirname(os.path.join(STAGE, f)), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, f), os.path.join(STAGE, f))
    os.makedirs(os.path.join(STAGE, "certs"), exist_ok=True)
    print(f"{len(repo_files())} repo files", flush=True)
    subprocess.run([CSC, "/nologo", "/target:winexe", f"/out:{os.path.join(STAGE, 'launcher', 'fifalauncher.exe')}",
                    os.path.join(ROOT, "launcher", "fifalauncher.cs")], check=True)
    os.makedirs(CACHE, exist_ok=True)
    zpath = os.path.join(CACHE, PY_ZIP)
    if not os.path.isfile(zpath):
        print(f"fetching {PY_URL}", flush=True)
        urllib.request.urlretrieve(PY_URL, zpath + ".part")
        os.replace(zpath + ".part", zpath)
    pydir = os.path.join(STAGE, "python")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(pydir)
    with open(os.path.join(pydir, "python312._pth"), "w", newline="\r\n") as f:
        f.write(python_pth())
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--only-binary=:all:", "--platform", "win_amd64",
                    "--python-version", "3.12", "--implementation", "cp", "--target",
                    os.path.join(pydir, "Lib", "site-packages"), *PACKAGES], check=True)
    # the bundled Python runs the server's own self-checks before it is packed
    for args in (["-c", "import ssl, sqlite3, cryptography, numpy, PIL, cv2; print('imports ok', cv2.__version__)"],
                 [os.path.join(STAGE, "installer", "setup_pc.py"), "--help"]):
        subprocess.run([os.path.join(pydir, "python.exe"), *args], cwd=STAGE, check=True)
    subprocess.run([a.iscc, f"/DAppVersion={version}", f"/DStage={STAGE}", os.path.join(HERE, "fo3id.iss")], check=True)
    print(f"built {os.path.join(ROOT, 'dist', f'FO3ID-Server-Setup-{version}.exe')}")


if __name__ == "__main__":
    main()
