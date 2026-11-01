# Certificates

**English** | [Русский](README.ru.md)

`russian_trusted_root_ca.pem` is the Russian Ministry of Digital Development (Минцифры) root certificate. It is needed only for GigaChat:
the certificate chain of Sber's API is signed by it, and without it requests fail with `certificate verify failed`.

The Dockerfile downloads it automatically during the build. If the download fails (no network during the build),
download the file manually from https://www.gosuslugi.ru/crt (root certificate, Linux format, PEM)
and place it here as `russian_trusted_root_ca.pem`, then rebuild: `docker compose up --build`.

The certificate is used only for requests to GigaChat, not for the whole system.
