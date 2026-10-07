from __future__ import annotations

from dataclasses import dataclass

try:
    import keyring
    from keyring.errors import KeyringError
except Exception as exc:  # import guard
    keyring = None
    KeyringError = Exception
    _KEYRING_IMPORT_ERROR = exc
else:
    _KEYRING_IMPORT_ERROR = None


SERVICE_NAME = "TI-LEX-CODEX"


@dataclass(frozen=True)
class SecretStatus:
    provider: str
    configured: bool
    backend: str


class SecretStore:
    """Coffre local basé sur le gestionnaire de secrets du système."""

    def __init__(self, service_name: str = SERVICE_NAME):
        self.service_name = service_name

    def _require_backend(self) -> None:
        if keyring is None:
            raise RuntimeError(
                "Le module 'keyring' n'est pas installé. "
                "Installe les dépendances du projet avec: py -m pip install -r requirements.txt"
            ) from _KEYRING_IMPORT_ERROR

    @staticmethod
    def _account(provider: str) -> str:
        clean = str(provider or "").strip().lower()
        if not clean:
            raise ValueError("Nom du fournisseur vide.")
        return f"api-key:{clean}"

    def backend_name(self) -> str:
        self._require_backend()
        try:
            backend = keyring.get_keyring()
            return f"{backend.__class__.__module__}.{backend.__class__.__name__}"
        except Exception:
            return "inconnu"

    def set_api_key(self, provider: str, secret: str) -> None:
        self._require_backend()
        value = str(secret or "").strip()
        if not value:
            raise ValueError("La clé API est vide.")
        try:
            keyring.set_password(
                self.service_name,
                self._account(provider),
                value,
            )
        except KeyringError as exc:
            raise RuntimeError(f"Impossible d'enregistrer la clé dans le coffre système: {exc}") from exc

    def get_api_key(self, provider: str) -> str | None:
        self._require_backend()
        try:
            value = keyring.get_password(
                self.service_name,
                self._account(provider),
            )
            return value.strip() if value else None
        except KeyringError as exc:
            raise RuntimeError(f"Impossible de lire la clé dans le coffre système: {exc}") from exc

    def delete_api_key(self, provider: str) -> bool:
        self._require_backend()
        account = self._account(provider)
        try:
            existing = keyring.get_password(self.service_name, account)
            if existing is None:
                return False
            keyring.delete_password(self.service_name, account)
            return True
        except KeyringError as exc:
            raise RuntimeError(f"Impossible de supprimer la clé du coffre système: {exc}") from exc

    def status(self, provider: str) -> SecretStatus:
        return SecretStatus(
            provider=str(provider or "").strip().lower(),
            configured=bool(self.get_api_key(provider)),
            backend=self.backend_name(),
        )
