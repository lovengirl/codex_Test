"""OpenSSL로 127.0.0.1 전용 개발 인증서를 생성한다."""

from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    cert_dir = ROOT / ".certs"
    cert = cert_dir / "localhost.crt"
    key = cert_dir / "localhost.key"
    if cert.exists() and key.exists():
        print("기존 개발 인증서를 재사용합니다.")
        return
    openssl = shutil.which("openssl")
    if not openssl:
        candidate = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        if candidate.is_file():
            openssl = str(candidate)
    if not openssl:
        raise SystemExit("OpenSSL이 필요합니다. Git for Windows의 OpenSSL 또는 PATH를 확인해 주세요.")
    cert_dir.mkdir(exist_ok=True)
    config = cert_dir / "openssl.cnf"
    config.write_text("[req]\ndistinguished_name=dn\n[dn]\n", encoding="ascii")
    result = subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-sha256",
         "-noenc", "-days", "365", "-config", str(config),
         "-keyout", str(key), "-out", str(cert),
         "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
         "-addext", "basicConstraints=critical,CA:TRUE"],
        capture_output=True,
    )
    if result.returncode:
        raise SystemExit("인증서 생성 실패: " + result.stderr.decode("utf-8", errors="replace"))
    print("개발용 자체 서명 인증서 생성 완료: .certs/localhost.crt")


if __name__ == "__main__":
    main()
