"""TI-LEX CODEX - moteur agentique local pour projets multi-fichiers."""
from __future__ import annotations

import json
import difflib
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tilex"}
TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".scss", ".json",
    ".md", ".txt", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".sh", ".ps1",
    ".sql", ".xml", ".env.example"
}

@dataclass
class CodexResult:
    ok: bool
    message: str
    changed: list[str] = field(default_factory=list)
    plan: dict = field(default_factory=dict)

class CodexEngine:
    """Planifie et applique un projet par lots pour rester utilisable avec un LLM local."""

    def __init__(self, root: Path, model: str = "qwen2.5-coder:7b",
                 endpoint: str = "http://127.0.0.1:11434",
                 status: Callable[[str], None] | None = None):
        self.root = Path(root).resolve()
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.status = status or (lambda _msg: None)
        self.last_file = None
        self.last_content = ""
        self.last_stats = {"added": 0, "modified": 0, "deleted": 0}
        self.state_dir = self.root / ".tilex"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _safe(self, rel: str) -> Path:
        rel = rel.replace("\\", "/").lstrip("/")
        p = (self.root / rel).resolve()
        if p == self.root or self.root not in p.parents:
            raise ValueError(f"Chemin refusé: {rel}")
        return p

    def _ask(self, prompt: str, temperature: float = 0.15, json_mode: bool = False) -> str:
        payload = {"model": self.model, "prompt": prompt, "stream": False,
                   "options": {"temperature": temperature}}
        if json_mode:
            payload["format"] = "json"
        response = requests.post(
            self.endpoint + "/api/generate",
            json=payload,
            timeout=600,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()

    @staticmethod
    def _json(text: str):
        text = text.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
        if fenced:
            text = fenced.group(1).strip()
        start_candidates = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if start_candidates:
            start = min(start_candidates)
            end = max(text.rfind("}"), text.rfind("]"))
            if end >= start:
                text = text[start:end + 1]
        return json.loads(text)

    def inventory(self, max_files: int = 240) -> list[str]:
        out = []
        for p in sorted(self.root.rglob("*")):
            if any(part in IGNORE_DIRS for part in p.relative_to(self.root).parts):
                continue
            if p.is_file():
                out.append(str(p.relative_to(self.root)).replace("\\", "/"))
                if len(out) >= max_files:
                    break
        return out

    def context_for(self, paths: list[str], max_chars: int = 36000) -> str:
        chunks, used = [], 0
        for rel in paths:
            try:
                p = self._safe(rel)
                if not p.is_file() or p.stat().st_size > 180_000:
                    continue
                if p.suffix.lower() not in TEXT_EXTENSIONS and p.name not in {
                    "Dockerfile", "Makefile", "requirements.txt", "package.json"
                }:
                    continue
                data = p.read_text(encoding="utf-8", errors="replace")
                room = max_chars - used
                if room <= 0:
                    break
                data = data[:room]
                chunks.append(f"--- FILE: {rel} ---\n{data}")
                used += len(data)
            except (OSError, ValueError):
                continue
        return "\n\n".join(chunks)

    def make_plan(self, request: str) -> dict:
        files = self.inventory()
        prompt = f"""Tu es l'ARCHITECTE de TI-LEX CODEX, un agent de développement local.
Projet: {self.root.name}
Demande: {request}
Fichiers existants: {json.dumps(files, ensure_ascii=False)}

Conçois un plan pour UN SEUL fichier cible par commande. Même si la demande pourrait être découpée, cette exécution ne doit créer ou modifier qu’un seul fichier. Choisis le fichier le plus pertinent et concentre toute la réponse dedans.
Réponds UNIQUEMENT en JSON valide, sans markdown:
{{
  "summary": "résumé",
  "architecture": ["décision 1", "décision 2"],
  "tasks": [
    {{
      "id": "T01",
      "goal": "objectif précis",
      "files": ["chemin/fichier.py"],
      "needs": ["autre/fichier.py"]
    }}
  ]
}}
Règles: chemins relatifs seulement; pas de .git/.venv/node_modules; chaque tâche doit rester petite
(EXACTEMENT 1 fichier total et 1 seule tâche); ne crée jamais plusieurs tâches pour une même commande."""
        plan = self._json(self._ask(prompt, json_mode=True))
        if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list):
            raise ValueError("Plan IA invalide")
        plan["request"] = request
        plan["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (self.state_dir / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return plan

    def _backup(self, paths: list[str]) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.state_dir / "backups" / stamp
        for rel in paths:
            try:
                src = self._safe(rel)
                if src.is_file():
                    dst = backup / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            except (OSError, ValueError):
                pass
        return backup

    def generate_task(self, request: str, task: dict) -> list[dict]:
        targets = [str(x) for x in task.get("files", [])][:1]
        if not targets:
            raise ValueError("Tâche sans fichier cible")
        target = targets[0]
        needs = [str(x) for x in task.get("needs", [])][:12]
        context_paths = list(dict.fromkeys(needs + [target]))
        context = self.context_for(context_paths)
        prompt = f"""Tu es l'IMPLEMENTEUR de TI-LEX CODEX.
Demande globale: {request}
Fichier cible: {target}
Objectif: {task.get("goal", "implémenter la demande")}
Contexte utile:
{context or "(aucun fichier source nécessaire)"}

Écris le contenu COMPLET du fichier cible uniquement.
Réponds avec le contenu brut du fichier, sans JSON, sans explication et sans bloc Markdown.
Ne génère aucun autre fichier. Ne renvoie jamais un diff ni des points de suspension."""
        content = self._ask(prompt)
        fenced = re.fullmatch(r"```(?:[A-Za-z0-9_+.-]+)?\s*([\s\S]*?)\s*```", content)
        if fenced:
            content = fenced.group(1)
        if not content.strip():
            raise ValueError(f"Réponse vide pour {target}")
        return [{"path": target, "content": content}]

    def apply_files(self, items: list[dict]) -> list[str]:
        valid = []
        for item in items:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            rel = str(item["path"]).replace("\\", "/")
            if any(part in IGNORE_DIRS for part in Path(rel).parts):
                continue
            self._safe(rel)
            valid.append((rel, str(item.get("content", ""))))
        self._backup([rel for rel, _ in valid])
        changed = []
        for rel, content in valid:
            target = self._safe(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
            if old != content:
                old_lines = (old or "").splitlines()
                new_lines = content.splitlines()
                stats = {"added": 0, "modified": 0, "deleted": 0}
                matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    if tag == "insert":
                        stats["added"] += j2 - j1
                    elif tag == "delete":
                        stats["deleted"] += i2 - i1
                    elif tag == "replace":
                        paired = min(i2 - i1, j2 - j1)
                        stats["modified"] += paired
                        stats["deleted"] += max(0, (i2 - i1) - paired)
                        stats["added"] += max(0, (j2 - j1) - paired)
                target.write_text(content, encoding="utf-8")
                self.last_file = rel
                self.last_content = content
                self.last_stats = stats
                changed.append(rel)
        return changed

    def build(self, request: str, max_tasks: int = 40) -> CodexResult:
        try:
            self.status("🧠 Architecture du projet…")
            plan = self.make_plan(request)
            tasks = plan.get("tasks", [])[:1]
            changed = []
            for index, task in enumerate(tasks, 1):
                task_id = task.get("id", f"T{index:02}")
                self.status(f"⚙ {task_id} • {task.get('goal', 'génération')} ({index}/{len(tasks)})")
                items = self.generate_task(request, task)
                changed.extend(self.apply_files(items))
            report = {
                "request": request,
                "changed": list(dict.fromkeys(changed)),
                "tasks_completed": len(tasks),
                "plan": plan,
            }
            (self.state_dir / "last_run.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return CodexResult(True, f"Projet généré: {len(report['changed'])} fichier(s) modifié(s).",
                               report["changed"], plan)
        except Exception as exc:
            return CodexResult(False, f"Erreur moteur CODEX: {exc}")
