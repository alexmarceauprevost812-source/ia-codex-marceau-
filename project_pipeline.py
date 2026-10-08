"""Écriture transactionnelle des fichiers générés par TI-LEX CODEX.

Les fichiers sont validés par le moteur avant cet appel. Ce module protège
le projet contre les erreurs d'écriture, les conflits et les lots incomplets.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import time
import uuid
from pathlib import Path


def fingerprint(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes, original_mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tilex-write-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if original_mode is not None:
            os.chmod(tmp_name, stat.S_IMODE(original_mode))
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def commit_project_files(
    root: Path,
    files: list[tuple[str, str]],
    backup_dir: Path,
    expected: dict[str, str | None] | None = None,
) -> list[str]:
    """Commit tout le lot ou restaure les fichiers déjà modifiés.

    expected contient les empreintes capturées AVANT la génération :
    si un fichier est modifié entre-temps, on n'écrase pas ce changement.
    Les nouveaux fichiers sont aussi contrôlés (valeur None).
    """
    root = Path(root).resolve()
    backup_dir = Path(backup_dir).resolve()
    expected = expected or {}
    seen: set[str] = set()
    staged: list[tuple[str, Path, bytes, bytes | None, int | None]] = []

    # Phase 1 : vérification complète sans écriture dans le projet.
    for rel, content in files:
        rel = str(rel).replace("\\", "/")
        if rel.lower() in seen:
            raise ValueError(f"Fichier en double dans le lot : {rel}")
        seen.add(rel.lower())
        target = (root / rel).resolve()
        if target == root or root not in target.parents:
            raise ValueError(f"Chemin hors projet refusé : {rel}")
        if target.exists() and not target.is_file():
            raise ValueError(f"Ce chemin n'est pas un fichier : {rel}")
        previous = target.read_bytes() if target.is_file() else None
        if rel in expected and fingerprint(previous) != expected[rel]:
            raise RuntimeError(
                f"Conflit : {rel} a changé pendant la génération. "
                "Aucune écriture effectuée. Recharge le fichier avant de réessayer."
            )
        new_bytes = str(content).encode("utf-8")
        if new_bytes != previous:
            mode = target.stat().st_mode if target.is_file() else None
            staged.append((rel, target, new_bytes, previous, mode))

    if not staged:
        return []

    # Phase 2 : sauvegarder TOUS les originaux avant le moindre changement.
    snapshot = backup_dir / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    for rel, target, _data, previous, _mode in staged:
        if previous is not None:
            dest = snapshot / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, dest)

    # Phase 3 : remplacements atomiques individuels, avec rollback du lot.
    done: list[tuple[str, Path, bytes, bytes | None, int | None]] = []
    try:
        for item in staged:
            rel, target, new_bytes, previous, mode = item
            # Détecte aussi un changement survenu après la phase de préparation.
            current = target.read_bytes() if target.is_file() else None
            if current != previous:
                raise RuntimeError(f"Conflit de modification simultanée : {rel}")
            _atomic_write(target, new_bytes, mode)
            done.append(item)
    except Exception as exc:
        rollback_errors = []
        for rel, target, _new, previous, mode in reversed(done):
            try:
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    _atomic_write(target, previous, mode)
            except Exception as rollback_exc:
                rollback_errors.append(f"{rel}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                f"Écriture interrompue : {exc}. Restauration incomplète : "
                + "; ".join(rollback_errors)
                + f". Sauvegardes disponibles dans {snapshot}"
            ) from exc
        raise RuntimeError(
            f"Écriture du lot annulée : {exc}. Les anciens fichiers ont été restaurés."
        ) from exc

    return [rel for rel, *_rest in staged]
