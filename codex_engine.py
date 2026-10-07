"""TI-LEX CODEX - moteur agentique local pour projets multi-fichiers."""
from __future__ import annotations

import json
import os
import difflib
import ast
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from secret_store import SecretStore

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

    def __init__(self, root: Path, model: str = "qwen2.5:7b",
                 endpoint: str = "http://127.0.0.1:11434",
                 status: Callable[[str], None] | None = None,
                 provider: str = "OLLAMA",
                 anthropic_model: str | None = None):
        self.root = Path(root).resolve()
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.status = status or (lambda _msg: None)
        self.provider = str(provider or "OLLAMA").upper()
        self.secret_store = SecretStore()
        self.anthropic_model = str(
            anthropic_model
            or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        ).strip()
        self.openai_model = os.environ.get("OPENAI_MODEL", "gpt-5.5")
        self.perplexity_model = os.environ.get("PERPLEXITY_MODEL", "sonar-pro")
        self.deepseek_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
        self.deepseek_base_url = os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        ).rstrip("/")
        self.deepseek_reasoning_effort = os.environ.get(
            "DEEPSEEK_REASONING_EFFORT", "high"
        ).strip().lower()
        if self.deepseek_reasoning_effort not in {"none", "low", "high", "max"}:
            self.deepseek_reasoning_effort = "high"
        self.gemini_model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
        self.last_file = None
        self.last_content = ""
        self.last_stats = {"added": 0, "modified": 0, "deleted": 0}
        self.last_outputs = {}
        self.last_stats_by_file = {}
        self.last_diffs = {}
        self.state_dir = self.root / ".tilex"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.state_dir / "project_memory.json"

    def _safe(self, rel: str) -> Path:
        rel = rel.replace("\\", "/").lstrip("/")
        p = (self.root / rel).resolve()
        if p == self.root or self.root not in p.parents:
            raise ValueError(f"Chemin refusé: {rel}")
        return p

    def _resolve_model(self) -> str:
        """Choisit un modèle Ollama réellement installé, avec repli automatique."""
        try:
            response = requests.get(self.endpoint + "/api/tags", timeout=(2, 4))
            response.raise_for_status()
            models = [
                item.get("name", "").strip()
                for item in response.json().get("models", [])
                if item.get("name")
            ]
        except Exception:
            return self.model

        if not models or self.model in models:
            return self.model

        preferred = [
            "qwen2.5:7b",
            "qwen2.5-coder:7b",
            "mistral:latest",
            "mistral",
        ]
        for candidate in preferred:
            if candidate in models:
                self.status(f"Modèle {self.model} absent -> utilisation de {candidate}")
                self.model = candidate
                return self.model

        self.status(f"Modèle {self.model} absent -> utilisation de {models[0]}")
        self.model = models[0]
        return self.model

    def _ask(
        self,
        prompt: str,
        temperature: float = 0.15,
        json_mode: bool = False,
        num_predict: int | None = None,
        read_timeout: int | None = None,
    ) -> str:
        if num_predict is None:
            num_predict = 512 if json_mode else 1800
        if read_timeout is None:
            read_timeout = 60 if json_mode else 150

        if self.provider in {"CLAUDE", "CLAUDE_CONTROL"}:
            return self._ask_anthropic(
                prompt,
                temperature=temperature,
                num_predict=num_predict,
                read_timeout=read_timeout,
            )
        if self.provider in {"OPENAI", "OPENAI_WEB"}:
            return self._ask_openai(
                prompt,
                temperature=temperature,
                num_predict=num_predict,
                read_timeout=read_timeout,
                web_search=self.provider == "OPENAI_WEB",
            )
        if self.provider == "PERPLEXITY":
            return self._ask_openai_compatible(
                provider="perplexity",
                label="PERPLEXITY",
                endpoint="https://api.perplexity.ai/chat/completions",
                model=self.perplexity_model,
                prompt=prompt,
                temperature=temperature,
                num_predict=num_predict,
                read_timeout=read_timeout,
            )
        if self.provider == "DEEPSEEK":
            return self._ask_deepseek(
                prompt,
                temperature=temperature,
                num_predict=num_predict,
                read_timeout=read_timeout,
            )
        if self.provider == "GEMINI":
            return self._ask_gemini(
                prompt,
                temperature=temperature,
                num_predict=num_predict,
                read_timeout=read_timeout,
            )

        active_model = self._resolve_model()
        payload = {
            "model": active_model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "15m",
            "options": {
                "temperature": temperature,
                "num_ctx": 4096,
                "num_predict": num_predict,
            },
        }
        if json_mode:
            payload["format"] = "json"

        response = requests.post(
            self.endpoint + "/api/generate",
            json=payload,
            timeout=(5, read_timeout),
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text.strip()
            raise RuntimeError(
                f"Ollama HTTP {response.status_code} avec le modèle {active_model}: {body or exc}"
            ) from exc

        data = response.json()
        if data.get("error"):
            raise RuntimeError(f"Ollama ({active_model}): {data['error']}")
        answer = data.get("response", "").strip()
        if not answer:
            raise RuntimeError(f"Ollama ({active_model}) a retourné une réponse vide.")
        return answer

    def _ask_anthropic(
        self,
        prompt: str,
        temperature: float = 0.15,
        num_predict: int = 1800,
        read_timeout: int = 150,
    ) -> str:
        api_key = self.secret_store.get_api_key("anthropic")
        if not api_key:
            raise RuntimeError(
                "Clé Anthropic absente. Utilise le menu CLÉS API en haut à droite."
            )

        self.status(f"☁ CLAUDE • {self.anthropic_model}")
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.anthropic_model,
                "max_tokens": int(max(64, num_predict)),
                "temperature": float(temperature),
                "messages": [
                    {"role": "user", "content": prompt}
                ],
            },
            timeout=(10, read_timeout),
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text.strip()
            raise RuntimeError(
                f"Anthropic HTTP {response.status_code}: {body or exc}"
            ) from exc

        data = response.json()
        blocks = data.get("content", [])
        parts = [
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        answer = "\n".join(part for part in parts if part).strip()
        if not answer:
            raise RuntimeError("Claude a retourné une réponse vide.")
        return answer

    def _ask_openai(
        self,
        prompt: str,
        temperature: float = 0.15,
        num_predict: int = 1800,
        read_timeout: int = 150,
        web_search: bool = False,
    ) -> str:
        api_key = self.secret_store.get_api_key("openai")
        if not api_key:
            raise RuntimeError(
                "Clé OpenAI absente. Utilise le menu CLÉS API en haut à droite."
            )

        label = "CHATGPT + INTERNET" if web_search else "CHATGPT"
        self.status(f"🌐 {label} • {self.openai_model}")

        payload = {
            "model": self.openai_model,
            "input": prompt,
            "max_output_tokens": int(max(64, num_predict)),
        }
        if web_search:
            payload["tools"] = [{"type": "web_search"}]

        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(10, read_timeout),
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text.strip()
            raise RuntimeError(
                f"OpenAI HTTP {response.status_code}: {body or exc}"
            ) from exc

        data = response.json()
        answer = str(data.get("output_text") or "").strip()
        if not answer:
            parts = []
            for item in data.get("output", []):
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                        text_value = block.get("text")
                        if text_value:
                            parts.append(str(text_value))
            answer = "\n".join(parts).strip()

        if not answer:
            raise RuntimeError("ChatGPT a retourné une réponse vide.")
        return answer

    def _ask_deepseek(
        self,
        prompt: str,
        temperature: float = 0.15,
        num_predict: int = 1800,
        read_timeout: int = 150,
    ) -> str:
        """Appelle DeepSeek avec le mode réflexion officiel, sans exposer le raisonnement interne."""
        api_key = self.secret_store.get_api_key("deepseek")
        if not api_key:
            raise RuntimeError(
                "Clé DeepSeek absente. Utilise le menu CLÉS API en haut à droite."
            )

        effort = self.deepseek_reasoning_effort
        thinking_enabled = effort != "none"
        mode_label = f"réflexion {effort}" if thinking_enabled else "réflexion désactivée"
        self.status(f"🧠 DEEPSEEK • {self.deepseek_model} • {mode_label}")

        payload = {
            "model": self.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Tu es TI-LEX CODEX / Codex Marceau, un assistant de programmation. "
                        "Réponds avec le résultat utile demandé, sans exposer ton raisonnement interne."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "max_tokens": int(max(64, num_predict)),
            "thinking": {"type": "enabled" if thinking_enabled else "disabled"},
            "reasoning_effort": effort,
        }
        # La documentation DeepSeek indique que temperature n'a pas d'effet
        # quand le mode thinking est actif. On ne l'envoie qu'en mode normal.
        if not thinking_enabled:
            payload["temperature"] = float(temperature)

        response = requests.post(
            self.deepseek_base_url + "/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(10, read_timeout),
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text.strip()
            raise RuntimeError(
                f"DEEPSEEK HTTP {response.status_code}: {body or exc}"
            ) from exc

        data = response.json()
        try:
            message = data["choices"][0]["message"]
            answer = str(message.get("content") or "").strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            answer = ""

        if not answer:
            raise RuntimeError("DeepSeek a retourné une réponse finale vide.")
        return answer

    def _ask_openai_compatible(
        self,
        provider: str,
        label: str,
        endpoint: str,
        model: str,
        prompt: str,
        temperature: float = 0.15,
        num_predict: int = 1800,
        read_timeout: int = 150,
    ) -> str:
        api_key = self.secret_store.get_api_key(provider)
        if not api_key:
            raise RuntimeError(
                f"Clé {label} absente. Utilise le menu CLÉS API en haut à droite."
            )

        self.status(f"☁ {label} • {model}")
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": float(temperature),
                "max_tokens": int(max(64, num_predict)),
                "stream": False,
            },
            timeout=(10, read_timeout),
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text.strip()
            raise RuntimeError(
                f"{label} HTTP {response.status_code}: {body or exc}"
            ) from exc

        data = response.json()
        try:
            answer = str(data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            answer = ""
        if not answer:
            raise RuntimeError(f"{label} a retourné une réponse vide.")
        return answer

    def _ask_gemini(
        self,
        prompt: str,
        temperature: float = 0.15,
        num_predict: int = 1800,
        read_timeout: int = 150,
    ) -> str:
        api_key = self.secret_store.get_api_key("gemini")
        if not api_key:
            raise RuntimeError(
                "Clé Gemini absente. Utilise le menu CLÉS API en haut à droite."
            )

        self.status(f"✨ GEMINI • {self.gemini_model}")
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent"
        )
        response = requests.post(
            endpoint,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}],
                    }
                ],
                "generationConfig": {
                    "temperature": float(temperature),
                    "maxOutputTokens": int(max(64, num_predict)),
                },
            },
            timeout=(10, read_timeout),
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = response.text.strip()
            raise RuntimeError(
                f"Gemini HTTP {response.status_code}: {body or exc}"
            ) from exc

        data = response.json()
        parts = []
        try:
            for part in data["candidates"][0]["content"]["parts"]:
                if isinstance(part, dict) and part.get("text"):
                    parts.append(str(part["text"]))
        except (KeyError, IndexError, TypeError):
            pass
        answer = "\n".join(parts).strip()
        if not answer:
            raise RuntimeError("Gemini a retourné une réponse vide.")
        return answer

    def _control_review(self, target: str, request: str, content: str) -> str:
        """Deuxième passage Claude avant écriture quand CLAUDE + CONTRÔLE est actif."""
        if self.provider != "CLAUDE_CONTROL":
            return content

        self.status(f"🛡 CLAUDE CONTRÔLE • {target}")
        review_prompt = f"""Tu es le contrôleur qualité de TI-LEX CODEX.

Demande utilisateur:
{request}

Fichier cible:
{target}

Contenu proposé:
{content}

Vérifie uniquement les erreurs réelles: syntaxe, API inexistante, imports inventés,
mauvaise plateforme, commandes impossibles, perte évidente de comportement.
Réponds uniquement en JSON valide:
{{"ok": true, "issues": []}}
ou
{{"ok": false, "issues": ["erreur 1", "erreur 2"]}}
"""
        try:
            review = self._json(
                self._ask(
                    review_prompt,
                    temperature=0.0,
                    json_mode=True,
                    num_predict=500,
                    read_timeout=90,
                )
            )
        except Exception as exc:
            self.status(f"⚠ Contrôle Claude indisponible • {exc}")
            return content

        if bool(review.get("ok")):
            self.status(f"✅ CLAUDE CONTRÔLE • validé • {target}")
            return content

        issues = [str(x) for x in review.get("issues", []) if str(x).strip()][:8]
        if not issues:
            return content

        repair_prompt = f"""Tu es le réparateur final de TI-LEX CODEX.
Demande: {request}
Fichier cible: {target}
Erreurs détectées: {json.dumps(issues, ensure_ascii=False)}

CONTENU À CORRIGER:
{content}

Corrige toutes les erreurs signalées sans supprimer les fonctions utiles.
Retourne uniquement le contenu complet final de {target}, sans explication."""
        repaired = self._clean_model_output(
            self._ask(
                repair_prompt,
                temperature=0.05,
                num_predict=2200,
                read_timeout=150,
            )
        )
        return self._repair_until_valid(target, request, repaired, max_repairs=2)

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

    def context_for(self, paths: list[str], max_chars: int = 5000) -> str:
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

    def _load_project_memory(self) -> list[dict]:
        try:
            if not self.memory_file.is_file():
                return []
            data = json.loads(self.memory_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [x for x in data[-12:] if isinstance(x, dict)]
        except Exception:
            pass
        return []

    def _remember_run(self, request: str, changed: list[str], plan: dict) -> None:
        memory = self._load_project_memory()
        memory.append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "request": " ".join(request.split())[:500],
            "changed": list(changed)[:2],
            "summary": " ".join(str(plan.get("summary") or "").split())[:300],
        })
        self.memory_file.write_text(
            json.dumps(memory[-12:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _request_terms(request: str) -> set[str]:
        words = re.findall(r"[A-Za-zÀ-ÿ0-9_+-]{3,}", request.lower())
        stop = {
            "avec", "dans", "pour", "plus", "code", "fichier", "faire", "crée",
            "cree", "ajoute", "modifier", "modifie", "une", "des", "les", "qui",
            "sur", "the", "and", "file", "create", "update",
        }
        return {w for w in words if w not in stop}

    def relevant_files(self, request: str, max_files: int = 10) -> list[str]:
        """Classe les fichiers du projet selon leur pertinence pour la demande."""
        terms = self._request_terms(request)
        recent = []
        for item in reversed(self._load_project_memory()):
            recent.extend(str(x) for x in item.get("changed", []))

        scored = []
        for rel in self.inventory(max_files=240):
            low = rel.lower()
            p = Path(rel)
            score = 0
            for term in terms:
                if term in low:
                    score += 6
                if term in p.stem.lower():
                    score += 4
            if rel in recent:
                score += max(1, 5 - recent.index(rel))
            if p.name in {"package.json", "requirements.txt", "pyproject.toml", "README.md"}:
                score += 1
            if score > 0:
                scored.append((score, len(rel), rel))

        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        return [rel for _, _, rel in scored[:max_files]]

    def _fast_continuation_target(self, request: str) -> str | None:
        """Choisit localement un fichier évident pour éviter un appel IA de planification."""
        lower = request.lower()

        # Une demande de création doit rester à l'architecte.
        create_words = (
            "nouveau fichier", "nouvelle fichier", "crée un fichier", "cree un fichier",
            "créer un fichier", "nouveau module", "nouvelle page", "nouveau script",
            "create file", "new file", "new module",
        )
        if any(word in lower for word in create_words):
            return None

        # Si la dernière commande a modifié exactement un fichier, une demande courte
        # d'amélioration est très probablement une continuation de ce fichier.
        memory = self._load_project_memory()
        if memory:
            last_changed = [str(x) for x in memory[-1].get("changed", []) if str(x)]
            if len(last_changed) == 1:
                candidate = last_changed[0]
                try:
                    if self._safe(candidate).is_file():
                        return candidate
                except (OSError, ValueError):
                    pass

        # Sinon utilise le meilleur fichier pertinent si un seul ressort.
        relevant = self.relevant_files(request, max_files=2)
        if len(relevant) == 1:
            try:
                if self._safe(relevant[0]).is_file():
                    return relevant[0]
            except (OSError, ValueError):
                pass
        return None

    def _execution_mode(self, request: str) -> tuple[str, str]:
        """Choisit FAST ou PRO sans appel IA. /fast et /pro forcent le mode."""
        raw = request.strip()
        lower = raw.lower()

        if lower.startswith("/fast "):
            return "FAST", raw[6:].strip()
        if lower == "/fast":
            return "FAST", ""
        if lower.startswith("/pro "):
            return "PRO", raw[5:].strip()
        if lower == "/pro":
            return "PRO", ""

        file_pattern = r"(?i)([A-Za-z0-9_./\\-]+\.(?:py|pyw|js|jsx|mjs|cjs|ts|tsx|html?|css|scss|json|md|txt|toml|ya?ml|sql|sh|bash|zsh|ps1|bat|cmd|c|h|cpp|hpp|cc|java|go|rs|php|rb|lua|xml|ini|cfg|env))"
        explicit = list(dict.fromkeys(re.findall(file_pattern, raw)))
        if len(explicit) >= 2:
            return "PRO", raw

        complex_words = (
            "architecture", "architecte", "refactor", "refactorise", "refactoriser",
            "plusieurs fichiers", "multi-fichier", "multifichier", "projet complet",
            "analyse tout le projet", "analyse le projet", "corrige tout le projet",
            "migration", "intégration complète", "integration complete",
            "tests d'intégration", "tests integration", "base de données",
            "database", "api complète", "api complete",
        )
        if any(word in lower for word in complex_words):
            return "PRO", raw

        # Les commandes système et la documentation d’installation demandent plus de vérification.
        environment_words = (
            "readme", "readme.md", "installation", "installer", "installe",
            "commande", "commandes", "terminal", "powershell", "power shell",
            "windows", "kali", "linux", "ubuntu", "bash", "shell", "wsl",
            "compatible", "compatibilité", "compatibilite", "lancer", "démarrer",
            "demarrer", "git clone", "npm install", "pip install", "apt install",
        )
        if any(word in lower for word in environment_words):
            return "PRO", raw

        # Une demande longue avec plusieurs actions est plus sûre en PRO.
        action_markers = sum(
            lower.count(word)
            for word in (" ajoute ", " corrige ", " modifie ", " crée ", " cree ", " puis ", " et ensuite ")
        )
        if len(raw) > 420 or action_markers >= 3:
            return "PRO", raw

        return "FAST", raw

    def turbo_task(self, request: str) -> dict | None:
        """Résout localement une commande simple sans appel IA de planification."""
        raw = request.strip()
        lower = raw.lower()

        # 1) README.md: tolère les fautes fréquentes de frappe de l'utilisateur.
        readme_aliases = (
            "readme", "readme.md", "readme.dm", "readm.md", "readm.dm",
            "reamd.md", "reamd.dm", "redame.md", "redame.dm",
        )
        if any(alias in lower for alias in readme_aliases):
            return {
                "id": "FAST",
                "goal": request,
                "files": ["README.md"],
                "needs": ["README.md"],
            }

        # 2) Nom de fichier explicite avec extension connue: priorité absolue.
        matches = re.findall(
            r"(?i)([A-Za-z0-9_./\\-]+\.(?:py|pyw|js|jsx|mjs|cjs|ts|tsx|html?|css|scss|json|md|txt|toml|ya?ml|sql|sh|bash|zsh|ps1|bat|cmd|c|h|cpp|hpp|cc|java|go|rs|php|rb|lua|xml|ini|cfg|env))",
            raw,
        )
        explicit_files = list(dict.fromkeys(x.replace("\\", "/") for x in matches))[:2]
        if len(explicit_files) == 1:
            rel = explicit_files[0]
            return {"id": "FAST", "goal": request, "files": [rel], "needs": [rel]}
        if len(explicit_files) >= 2:
            return None

        # 3) Si l'utilisateur dit clairement "fichier <nom>" sans extension reconnue,
        # ne choisis PAS main.py automatiquement. Laisse l'architecte décider.
        mentions_file = bool(
            re.search(r"(?i)\b(?:fichier|file)\b", raw)
            or re.search(r"(?i)\b[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8}\b", raw)
        )
        if mentions_file:
            return None

        # 4) Aucun fichier explicite: pour une continuation évidente, évite
        # complètement l'appel IA de planification (gain majeur de vitesse).
        fast_target = self._fast_continuation_target(request)
        if fast_target:
            return {
                "id": "FAST",
                "goal": request,
                "files": [fast_target],
                "needs": [fast_target],
            }

        # Sinon seulement, l'architecte IA décide.
        return None

    def _normalize_plan(self, plan: dict, request: str) -> dict:
        """Nettoie le plan IA: 1-2 fichiers valides, sans doublons."""
        raw_tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
        clean_tasks = []
        seen = set()
        for index, task in enumerate(raw_tasks[:2], 1):
            if not isinstance(task, dict):
                continue
            files = [str(x).strip().replace("\\", "/") for x in task.get("files", []) if str(x).strip()]
            if not files:
                continue
            rel = files[0]
            self._safe(rel)
            key = rel.lower()
            if key in seen:
                continue
            seen.add(key)
            clean_tasks.append({
                "id": str(task.get("id") or f"T{index:02}"),
                "goal": str(task.get("goal") or request),
                "files": [rel],
                "needs": [str(x).strip().replace("\\", "/") for x in task.get("needs", [])[:12] if str(x).strip()],
            })

        if not clean_tasks:
            raise ValueError("Le plan IA n'a choisi aucun fichier valide.")
        plan["tasks"] = clean_tasks
        return plan
    def make_plan(self, request: str) -> dict:
        files = self.inventory()
        relevant = self.relevant_files(request, max_files=12)
        memory = self._load_project_memory()
        prompt = f"""Tu es l'ARCHITECTE de TI-LEX CODEX, un agent de développement local.
Projet: {self.root.name}
Demande: {request}
Fichiers existants: {json.dumps(files, ensure_ascii=False)}
Fichiers probablement pertinents: {json.dumps(relevant, ensure_ascii=False)}
Mémoire récente du projet: {json.dumps(memory[-8:], ensure_ascii=False)}

{self._project_generation_rules("(plan)", request)}

Utilise la mémoire uniquement pour comprendre la continuité du projet. La demande actuelle reste prioritaire.

Conçois un plan professionnel en utilisant LE MINIMUM DE FICHIERS NÉCESSAIRE.
Règle principale: utilise 1 seul fichier par défaut. Crée un nouveau fichier si c'est réellement le bon endroit pour le code demandé. Utilise 2 fichiers MAXIMUM seulement si la séparation est techniquement nécessaire ou explicitement demandée par l'utilisateur. N'éparpille jamais une petite modification dans plusieurs fichiers. Si tout peut être proprement fait dans 1 fichier, fais-le dans 1 seul fichier.
Choisis de vrais noms de fichiers avec une extension adaptée au langage. Respecte un fichier explicitement nommé par l'utilisateur. N’utilise jamais README.md comme remplacement d’un fichier de code sauf si la demande parle explicitement du README.
TI-LEX est un CODEX DE LABORATOIRE: quand la demande concerne un test, conçois du code réellement exécutable dans un labo local autorisé, avec des données fictives ou des cibles locales comme 127.0.0.1. Favorise les tests unitaires, intégration, diagnostics, mocks, serveurs locaux et simulations défensives. Ne planifie pas de vol d'identifiants, malware, persistance, contournement de sécurité, destruction ou attaque contre des systèmes tiers.
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
Règles: chemins relatifs seulement; pas de .git/.venv/node_modules; 1 tâche par défaut, 2 tâches uniquement si indispensable; chaque tâche cible EXACTEMENT 1 fichier. Ne crée jamais 3 fichiers pour une seule commande. Donne toujours un vrai nom de fichier avec extension. Si un fichier existant convient, modifie-le plutôt que de créer inutilement un doublon. Si aucun fichier existant ne convient, crée un nouveau fichier au nom clair. Ne génère jamais un résumé à la place du code. N’utilise README.md que si l’utilisateur le demande explicitement."""
        plan = self._json(self._ask(prompt, json_mode=True, num_predict=600, read_timeout=60))
        if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list):
            raise ValueError("Plan IA invalide")
        plan = self._normalize_plan(plan, request)
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

    def _project_facts(self) -> dict:
        """Faits locaux simples pour éviter les commandes et dépendances inventées."""
        files = self.inventory(max_files=240)
        file_set = {str(x).replace("\\", "/") for x in files}
        return {
            "files": files,
            "has_package_json": "package.json" in file_set,
            "has_pyproject": "pyproject.toml" in file_set,
            "has_requirements": "requirements.txt" in file_set,
            "has_python": any(Path(x).suffix.lower() == ".py" for x in file_set),
            "has_powershell": any(Path(x).suffix.lower() == ".ps1" for x in file_set),
            "has_shell": any(Path(x).suffix.lower() in {".sh", ".bash"} for x in file_set),
        }

    def _project_generation_rules(self, target: str, request: str) -> str:
        facts = self._project_facts()
        lower = str(request or "").lower()
        platform_hint = "non précisée"
        if any(x in lower for x in ("powershell", "windows", "wsl")):
            platform_hint = "Windows/PowerShell"
        elif any(x in lower for x in ("kali", "linux", "ubuntu", "bash")):
            platform_hint = "Linux/Kali/Bash"
        return f"""
RÈGLES DE FIABILITÉ:
- Plateforme demandée: {platform_hint}.
- package.json présent: {facts["has_package_json"]}.
- requirements.txt présent: {facts["has_requirements"]}.
- Projet Python présent: {facts["has_python"]}.
- Ne mélange jamais PowerShell et Bash/Kali dans le même bloc de commandes.
- Ne propose pas npm install/npm start si package.json est absent, sauf si la demande crée réellement un projet Node.
- N’invente pas un fichier, une commande, un module Python ou une dépendance.
- Pour citer un script existant, utilise son vrai nom présent dans le projet.
- En Python multiplateforme, préfère sys.executable et shutil.which() quand approprié.
- subprocess.run()/Popen() n’acceptent pas de paramètre use_powershell.
- Ne fais pas import powershell sauf si ce module est réellement fourni ou déclaré.
- Fichier cible: {target}.
"""

    def _dependency_declared(self, name: str) -> bool:
        needle = str(name or "").lower().replace("_", "-")
        for rel in ("requirements.txt", "pyproject.toml", "setup.cfg", "setup.py"):
            try:
                p = self.root / rel
                if p.is_file():
                    data = p.read_text(encoding="utf-8", errors="replace").lower().replace("_", "-")
                    if needle in data:
                        return True
            except OSError:
                pass
        return False

    def _validate_python_semantics(self, target: str, content: str) -> tuple[bool, str]:
        """Détecte quelques erreurs Python valides syntaxiquement mais non exécutables."""
        try:
            tree = ast.parse(content, filename=target)
        except SyntaxError:
            return True, ""

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id == "subprocess":
                    if node.func.attr in {"run", "Popen", "call", "check_call", "check_output"}:
                        for kw in node.keywords:
                            if kw.arg in {"use_powershell", "powershell"}:
                                return False, f"Paramètre subprocess non supporté: {kw.arg!r}."

            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
                if "powershell" in names:
                    if not (self.root / "powershell.py").is_file() and not self._dependency_declared("powershell"):
                        return False, "Import powershell non déclaré dans le projet."

            if isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".", 1)[0]
                if module == "powershell":
                    if not (self.root / "powershell.py").is_file() and not self._dependency_declared("powershell"):
                        return False, "Import depuis powershell non déclaré dans le projet."

        return True, ""

    @staticmethod
    def _validate_script_language(target: str, content: str) -> tuple[bool, str]:
        suffix = Path(target).suffix.lower()
        text = content or ""
        if suffix in {".sh", ".bash", ".zsh"}:
            if "```" in text:
                return False, "Script shell invalide: balises Markdown détectées."
            if re.search(r"(?mi)^\s*(Get-ChildItem|Copy-Item|Set-Location|Write-Host|New-Item)\b", text):
                return False, "Script Linux mélangé avec des commandes PowerShell."
        if suffix == ".ps1":
            if "```" in text:
                return False, "Script PowerShell invalide: balises Markdown détectées."
            if re.search(r"(?mi)^\s*(?:sudo\s+)?apt(?:-get)?\s+", text):
                return False, "Script PowerShell mélangé avec des commandes apt Linux/Kali."
            if re.search(r"(?mi)^\s*#!/bin/(?:ba)?sh", text):
                return False, "Script PowerShell contient un shebang Bash."
        if suffix in {".bat", ".cmd"} and "```" in text:
            return False, "Script batch invalide: balises Markdown détectées."
        return True, ""

    @staticmethod
    def _readme_generation_rules(target: str) -> str:
        """Règles supplémentaires quand le fichier cible est un README."""
        if Path(target).name.lower() != "readme.md":
            return ""
        return """
RÈGLES README OBLIGATOIRES:
- Une section "Structure du projet" est informative seulement. Mets l’arborescence uniquement dans un bloc de code marqué text.
- Les lignes d’arborescence utilisant ├──, └──, │ ou caractères similaires ne sont JAMAIS des commandes.
- Les blocs PowerShell doivent contenir uniquement de vraies commandes PowerShell exécutables.
- Les blocs Bash ou sh doivent contenir uniquement de vraies commandes Linux/Kali exécutables.
- Ne mélange jamais une arborescence de fichiers avec une section Installation, Lancement, Commandes, PowerShell ou Bash.
- Si le projet est Python, ne propose pas npm install/npm start sauf si un package.json et un workflow Node sont réellement présents.
- Vérifie les vrais noms de fichiers du projet avant d’écrire une commande de lancement.
"""

    def _validate_readme_command_blocks(self, content: str) -> tuple[bool, str]:
        """Valide les blocs copiables d’un README selon leur shell réel."""
        fence = re.compile(
            r"```(powershell|ps1|bash|sh|shell|cmd|bat)\s*\n([\s\S]*?)```",
            re.IGNORECASE,
        )
        tree_line = re.compile(r"^\s*[├└│┌┬┼─]+")
        facts = self._project_facts()
        known = {str(x).replace("\\", "/") for x in facts["files"]}

        for language, block in fence.findall(content or ""):
            lang = language.lower()
            for line in block.splitlines():
                if tree_line.match(line):
                    return False, "README invalide: arborescence placée dans un bloc de commandes."

            if lang in {"powershell", "ps1"}:
                if re.search(r"(?mi)^\s*(?:sudo\s+)?apt(?:-get)?\s+", block):
                    return False, "README invalide: commande apt Linux dans un bloc PowerShell."
                if re.search(r"(?mi)^\s*chmod\s+", block):
                    return False, "README invalide: commande chmod Linux dans un bloc PowerShell."

            if lang in {"bash", "sh", "shell"}:
                if re.search(r"(?mi)^\s*(Get-ChildItem|Copy-Item|Set-Location|Write-Host|New-Item)\b", block):
                    return False, "README invalide: commande PowerShell dans un bloc Bash/Linux."
                if re.search(r"(?mi)^\s*\.\\[A-Za-z0-9_.-]+", block):
                    return False, "README invalide: syntaxe .\\ PowerShell dans un bloc Bash/Linux."

            if not facts["has_package_json"] and re.search(r"(?mi)^\s*npm\s+(?:install|start|run)\b", block):
                return False, "README invalide: npm proposé alors que package.json est absent."

            for match in re.finditer(
                r"(?mi)^\s*(?:python3?|py)\s+(?:\.\\|\./)?([A-Za-z0-9_./\\-]+\.py)\b",
                block,
            ):
                rel = match.group(1).replace("\\", "/").lstrip("./")
                if rel not in known and not (self.root / rel).is_file():
                    return False, f"README invalide: la commande lance {rel}, mais ce fichier n’existe pas."

        return True, ""

    def _validate_generated_content(self, target: str, content: str):
        """Valide le langage et la syntaxe avant d'écrire un fichier."""
        suffix = Path(target).suffix.lower()
        stripped = content.lstrip()

        if suffix == ".py":
            if stripped.lower().startswith(("<!doctype html", "<html", "<script", "<style")):
                return False, "Le modèle a généré du HTML au lieu de Python."
            try:
                ast.parse(content, filename=target)
            except SyntaxError as exc:
                return False, f"Python invalide: ligne {exc.lineno}: {exc.msg}"
            valid, error = self._validate_python_semantics(target, content)
            if not valid:
                return False, error
            return True, ""

        if suffix == ".json":
            try:
                json.loads(content)
            except Exception as exc:
                return False, f"JSON invalide: {exc}"
            return True, ""

        if suffix in {".html", ".htm"}:
            if stripped.startswith(("def ", "import ", "from ")) and "<html" not in stripped.lower():
                return False, "Le modèle a généré du Python au lieu de HTML."
            return True, ""

        if suffix in {".js", ".jsx", ".ts", ".tsx"}:
            if stripped.lower().startswith(("<!doctype html", "<html")):
                return False, "Le modèle a généré une page HTML complète au lieu du fichier JavaScript/TypeScript demandé."
            if re.match(r"(?s)^\s*(?:def|class)\s+[A-Za-z_][A-Za-z0-9_]*\s*[:(]", stripped):
                return False, "Le modèle a généré du Python au lieu de JavaScript/TypeScript."
            return True, ""

        if suffix in {".md", ".txt"}:
            if Path(target).name.lower() == "readme.md":
                valid, error = self._validate_readme_command_blocks(content)
                if not valid:
                    return False, error
            return True, ""

        if suffix in {".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd"}:
            valid, error = self._validate_script_language(target, content)
            if not valid:
                return False, error
            if not stripped:
                return False, "Le script généré est vide."
            return True, ""

        if not stripped:
            return False, "Le fichier généré est vide."

        return True, ""
    @staticmethod
    def _clean_model_output(content: str) -> str:
        content = (content or "").strip()
        fenced = re.fullmatch(r"```(?:[A-Za-z0-9_+.-]+)?\s*([\s\S]*?)\s*```", content)
        if fenced:
            content = fenced.group(1).strip()
        return content

    def _repair_until_valid(self, target: str, request: str, content: str, max_repairs: int = 2) -> str:
        """Répare automatiquement un fichier invalide avant toute sauvegarde."""
        content = self._clean_model_output(content)
        if not content:
            raise ValueError(f"Réponse vide pour {target}")

        for attempt in range(max_repairs + 1):
            valid, error = self._validate_generated_content(target, content)
            if valid:
                if attempt:
                    self.status(f"✅ Auto-correction réussie • {target} • tentative {attempt}/{max_repairs}")
                return content

            if attempt >= max_repairs:
                raise ValueError(
                    f"Auto-correction impossible pour {target} après {max_repairs} tentative(s): {error}"
                )

            self.status(
                f"🧪 Erreur détectée • {target} • réparation {attempt + 1}/{max_repairs} • {error}"
            )
            repair_prompt = f"""Tu es le RÉPARATEUR de TI-LEX CODEX.

Fichier cible: {target}
Demande utilisateur: {request}
Erreur détectée automatiquement: {error}

CONTENU INVALIDE:
{content}

Corrige uniquement ce qui est nécessaire pour produire le contenu COMPLET et valide de {target}.
{self._project_generation_rules(target, request)}
{self._readme_generation_rules(target)}
Respecte strictement le langage correspondant à l'extension.
Conserve les fonctions, classes, imports et comportements utiles déjà présents.
Ne renvoie ni explication, ni Markdown, ni diff, ni résumé.
Retourne uniquement le contenu final complet du fichier."""
            content = self._clean_model_output(self._ask(repair_prompt, temperature=0.05, num_predict=1400, read_timeout=120))
            if not content:
                raise ValueError(f"Réparation vide pour {target}")

        return content

    def _try_fast_patch(self, target: str, request: str, old_content: str) -> str | None:
        """Tente une modification ciblée pour éviter de régénérer tout le fichier."""
        if not old_content or len(old_content) > 6500:
            return None

        lower = request.lower()
        full_rewrite_words = (
            "réécris tout", "reecris tout", "remplace tout", "refais tout",
            "rewrite all", "replace all", "nouveau fichier", "new file",
        )
        if any(word in lower for word in full_rewrite_words):
            return None

        prompt = f"""Tu es le mode PATCH RAPIDE de TI-LEX CODEX.

Fichier cible: {target}
Demande: {request}

CONTENU ACTUEL:
{old_content}

{self._project_generation_rules(target, request)}
{self._readme_generation_rules(target)}

Réponds UNIQUEMENT en JSON valide avec cette structure:
{{
  "replacements": [
    {{
      "old": "texte EXACT présent dans le fichier",
      "new": "texte de remplacement"
    }}
  ]
}}

Règles:
- Fais le minimum de remplacements nécessaires.
- "old" doit être copié EXACTEMENT depuis le contenu actuel.
- Maximum 4 remplacements.
- Ne renvoie jamais le fichier complet.
- Ne renvoie aucun Markdown ni explication.
- Si la modification ciblée est impossible proprement, réponds {{"replacements":[]}}.
"""
        self.status(f"⚡ PATCH RAPIDE • {target}")
        try:
            payload = self._json(self._ask(prompt, temperature=0.05, json_mode=True, num_predict=420, read_timeout=45))
        except Exception:
            return None

        replacements = payload.get("replacements", []) if isinstance(payload, dict) else []
        if not isinstance(replacements, list) or not replacements or len(replacements) > 4:
            return None

        updated = old_content
        for item in replacements:
            if not isinstance(item, dict):
                return None
            old = str(item.get("old", ""))
            new = str(item.get("new", ""))
            if not old or old not in updated:
                return None
            # Un patch doit viser une occurrence non ambiguë.
            if updated.count(old) != 1:
                return None
            updated = updated.replace(old, new, 1)

        if updated == old_content:
            return None

        valid, error = self._validate_generated_content(target, updated)
        if not valid:
            self.status(f"🧪 PATCH invalide • bascule génération complète • {error}")
            return None

        self.status(f"✅ PATCH RAPIDE appliqué • {target}")
        return updated

    def _fast_excerpt(self, content: str, request: str, max_chars: int = 3200) -> str:
        """Extrait seulement la zone la plus pertinente pour FAST."""
        if len(content) <= max_chars:
            return content

        lines = content.splitlines(keepends=True)
        terms = self._request_terms(request)
        best_index = 0
        best_score = -1

        for i, line in enumerate(lines):
            low = line.lower()
            score = sum(1 for term in terms if term in low)
            if score > best_score:
                best_score = score
                best_index = i

        start = max(0, best_index - 35)
        end = min(len(lines), best_index + 55)
        excerpt = "".join(lines[start:end])

        if len(excerpt) > max_chars:
            excerpt = excerpt[:max_chars]
        return excerpt

    def generate_fast_task(self, request: str, task: dict) -> list[dict]:
        """DIRECT: un appel Qwen, fichier final complet, aucune seconde réflexion."""
        targets = [str(x) for x in task.get("files", [])][:1]
        if not targets:
            raise ValueError("DIRECT: aucun fichier cible.")

        target = targets[0]
        target_path = self._safe(target)
        old_content = (
            target_path.read_text(encoding="utf-8", errors="replace")
            if target_path.is_file() else ""
        )

        # Qwen 7B / ctx 4096: garde de la place pour une vraie sortie de code.
        # Pour un fichier existant raisonnable, on fournit le contenu complet.
        # Pour un très gros fichier, DIRECT refuse plutôt que d'inventer un mini fichier.
        if len(old_content) > 9000:
            raise ValueError(
                f"DIRECT: {target} est trop gros pour une réécriture complète rapide. "
                "Utilise /pro pour ce fichier."
            )

        prompt = f"""Tu es TI-LEX CODEX DIRECT.
Tu dois exécuter UNE commande de programmation immédiatement.

FICHIER CIBLE: {target}
COMMANDE: {request}

FICHIER ACTUEL:
{old_content if old_content else "(nouveau fichier)"}

{self._project_generation_rules(target, request)}
{self._readme_generation_rules(target)}

CONTRAT OBLIGATOIRE:
- Retourne le CONTENU FINAL COMPLET du fichier {target}.
- Pas de résumé, pas d'explication, pas de Markdown, pas de diff.
- Pas de pseudo-code, pas de TODO, pas de points de suspension.
- Si le fichier existe, conserve tout ce qui n'est pas concerné par la commande.
- Le résultat doit être du vrai code correspondant à l'extension de {target}.
- Ne remplace jamais une vraie application par un exemple minimal du genre print("Bonjour").
- N'écris qu'un seul fichier.
"""
        self.status(f"⚡ DIRECT • Qwen écrit le fichier complet • {target}")
        content = self._clean_model_output(
            self._ask(
                prompt,
                temperature=0.05,
                num_predict=2600,
                read_timeout=120,
            )
        )
        if not content:
            raise ValueError("DIRECT: Qwen a retourné une réponse vide.")

        content = self._repair_until_valid(
            target,
            request,
            content,
            max_repairs=2,
        )
        content = self._control_review(target, request, content)

        # Protection anti-miniature: une modification ne doit pas écraser un vrai
        # fichier par quelques lignes sans demande explicite.
        old_lines = old_content.splitlines()
        new_lines = content.splitlines()
        destructive = any(
            word in request.lower()
            for word in (
                "supprime", "efface", "vide le fichier", "remplace tout",
                "réécris tout", "reecris tout",
            )
        )
        if (
            old_lines
            and not destructive
            and len(old_lines) >= 20
            and len(new_lines) < max(8, int(len(old_lines) * 0.60))
        ):
            raise ValueError(
                f"DIRECT: résultat trop court ({len(new_lines)} lignes) pour "
                f"remplacer {len(old_lines)} lignes. Fichier original conservé."
            )

        return [{"path": target, "content": content}]

    def generate_task(self, request: str, task: dict) -> list[dict]:
        targets = [str(x) for x in task.get("files", [])][:1]
        if not targets:
            raise ValueError("Tâche sans fichier cible")
        target = targets[0]
        target_path = self._safe(target)
        old_content = target_path.read_text(encoding="utf-8", errors="replace") if target_path.is_file() else ""

        # Pour une petite modification d'un fichier existant, un patch ciblé évite
        # de faire générer inutilement le fichier complet par le modèle.
        patched = self._try_fast_patch(target, request, old_content)
        if patched is not None:
            return [{"path": target, "content": patched}]

        needs = [str(x) for x in task.get("needs", [])][:6]
        related = self.relevant_files(request, max_files=2)
        context_paths = list(dict.fromkeys([target] + needs + related))
        context = self.context_for(context_paths)
        prompt = f"""Tu es l'IMPLEMENTEUR de TI-LEX CODEX.
Demande globale: {request}
Fichier cible: {target}
Objectif: {task.get("goal", "implémenter la demande")}
Contexte utile:
{context or "(aucun fichier source nécessaire)"}

{self._project_generation_rules(target, request)}
{self._readme_generation_rules(target)}

Écris le contenu COMPLET, exécutable et professionnel du fichier cible uniquement.
Le résultat doit être le vrai fichier final demandé, jamais un résumé, jamais une description et jamais un pseudo-code.
Si le fichier existe déjà, conserve tout le code qui n'est pas directement concerné par la demande:
imports, fonctions, classes, commentaires utiles et comportements existants.
Ne remplace jamais un gros fichier par une version miniature sauf si l'utilisateur le demande explicitement.
N'écris jamais "voici le code", "résumé", "TODO", "à compléter", "..." ou une explication à la place du contenu réel.
Réponds avec le contenu brut du fichier, sans JSON, sans explication et sans bloc Markdown.
Ne génère aucun autre fichier. Ne renvoie jamais un diff ni des points de suspension.

MODE LABORATOIRE TI-LEX:
- Le fichier doit pouvoir être lancé et testé localement quand c'est pertinent.
- Pour un test réseau ou sécurité, utilise uniquement un environnement local/autorisé, des données synthétiques, des mocks ou 127.0.0.1.
- Ajoute des messages clairs de démarrage, résultat et erreur pour faciliter les essais.
- Préfère des fonctions testables et une section main quand le langage s'y prête.
- Ne produis pas de code de vol d'identifiants, malware, persistance, évasion, destruction, ou d'attaque contre des systèmes tiers.
- Si la demande dangereuse ne peut pas être rendue sûre, transforme-la en simulation défensive locale qui démontre le concept sans capacité offensive réelle."""
        self.status(f"✍ IA • écrit le fichier {target}")
        content = self._repair_until_valid(
            target,
            request,
            self._ask(prompt, num_predict=1800, read_timeout=150),
            max_repairs=2,
        )
        content = self._control_review(target, request, content)

        old_lines = old_content.splitlines()
        new_lines = content.splitlines()
        destructive_words = (
            "supprime", "efface", "remplace tout", "réécris tout", "reecris tout",
            "simplifie", "réduis", "reduit", "vide le fichier"
        )
        destructive_request = any(word in request.lower() for word in destructive_words)

        if old_lines and not destructive_request and len(old_lines) >= 20 and len(new_lines) < max(8, int(len(old_lines) * 0.60)):
            retry_prompt = f"""Tu modifies un fichier EXISTANT de {len(old_lines)} lignes.
Ta première réponse ne contient que {len(new_lines)} lignes, ce qui risque d'effacer du code utile.

Demande utilisateur: {request}
Fichier cible: {target}

CONTENU ACTUEL COMPLET:
{old_content}

{self._project_generation_rules(target, request)}
{self._readme_generation_rules(target)}

Refais la modification en conservant TOUT ce qui n'est pas directement concerné par la demande.
Ne raccourcis pas le fichier inutilement. Garde les fonctions, classes, imports et comportements existants.
Réponds uniquement avec le contenu COMPLET du fichier final, sans markdown ni explication."""
            content = self._repair_until_valid(
                target,
                request,
                self._ask(retry_prompt, num_predict=1800, read_timeout=150),
                max_repairs=2,
            )
            new_lines = content.splitlines()
            if len(new_lines) < max(8, int(len(old_lines) * 0.45)):
                raise ValueError(
                    f"Protection activée: résultat trop court ({len(new_lines)} lignes) "
                    f"pour remplacer {len(old_lines)} lignes."
                )

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
            valid_content, validation_error = self._validate_generated_content(rel, content)
            if not valid_content:
                raise ValueError(
                    f"Validation finale refusée pour {rel}: {validation_error}. "
                    "Le fichier original est conservé."
                )

            # Deuxième garde Python: compile le contenu sans l'exécuter.
            if Path(rel).suffix.lower() == ".py":
                try:
                    compile(content, rel, "exec")
                except SyntaxError as exc:
                    raise ValueError(
                        f"Compilation Python refusée pour {rel}: "
                        f"ligne {exc.lineno}: {exc.msg}. "
                        "Le fichier original est conservé."
                    ) from exc

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
                diff_lines = list(difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"a/{rel}",
                    tofile=f"b/{rel}",
                    lineterm="",
                    n=2,
                ))
                self.last_diffs[rel] = "\n".join(diff_lines)

                target.write_text(content, encoding="utf-8")
                self.last_file = rel
                self.last_content = content
                self.last_stats = stats
                self.last_outputs[rel] = content
                self.last_stats_by_file[rel] = stats
                changed.append(rel)
        return changed

    def _append_readme_summary(self, request: str, changed: list[str], plan: dict) -> None:
        readme = self.root / "README.md"
        previous = readme.read_text(encoding="utf-8", errors="replace") if readme.is_file() else "# TI-LEX CODEX\n"

        install_header = "## Installation depuis GitHub"
        install_block = """## Installation depuis GitHub

```powershell
git clone https://github.com/alexmarceauprevost812-source/ia-codex-marceau-.git
cd ia-codex-marceau-
.\\start_codex.bat
```
"""
        if install_header not in previous:
            previous = previous.rstrip() + "\n\n" + install_block

        history_header = "## Historique CODEX"
        if history_header not in previous:
            previous = previous.rstrip() + "\n\n" + history_header + "\n"

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        clean_request = " ".join(str(request).split()).replace("`", "'")
        target = changed[-1] if changed else "(aucun fichier modifié)"
        summary = " ".join(str(plan.get("summary") or "Commande CODEX exécutée").split()).replace("`", "'")
        stats = self.last_stats or {"added": 0, "modified": 0, "deleted": 0}
        entry = (
            f"\n### {timestamp}\n"
            f"- **Commande :** {clean_request}\n"
            f"- **Résumé :** {summary}\n"
            f"- **Fichier :** `{target}`\n"
            f"- **Changements :** +{stats.get('added', 0)} ajoutées, "
            f"~{stats.get('modified', 0)} modifiées, "
            f"-{stats.get('deleted', 0)} supprimées\n"
        )
        readme.write_text(previous.rstrip() + "\n" + entry, encoding="utf-8")

        if self.last_file and Path(self.last_file).as_posix().lower() == "readme.md":
            self.last_content = readme.read_text(encoding="utf-8", errors="replace")

    def chat(self, question: str) -> CodexResult:
        """Conversation locale avec l'IA, sans modifier aucun fichier."""
        try:
            question = str(question or "").strip()
            if question.lower().startswith("/chat"):
                question = question[5:].strip()
            if not question:
                return CodexResult(
                    False,
                    "Utilise /chat suivi de ta question. Exemple : /chat explique ce projet",
                    [],
                    {"mode": "CHAT"},
                )

            self.status("💬 CHAT IA • préparation de la réponse")

            files = self.inventory(max_files=80)
            project_files = "\n".join(f"- {name}" for name in files[:80])
            memory = self._load_project_memory()
            recent = "\n".join(
                f"- {item.get('request', '')}: {item.get('summary', '')}"
                for item in memory[-4:]
            )

            prompt = f"""Tu es TI-LEX CODEX, assistant local de programmation.
Tu réponds en français clair et directement à la question.
MODE CHAT UNIQUEMENT: ne propose pas de modifier automatiquement des fichiers et n'écris aucun fichier.

Projet: {self.root.name}

Fichiers du projet:
{project_files or "(aucun fichier détecté)"}

Historique récent:
{recent or "(aucun historique)"}

Question utilisateur:
{question}

Réponds comme un assistant de développement utile. Si la question concerne un fichier précis mais que son contenu n'est pas fourni, indique ce qu'il faudrait ouvrir ou préciser."""

            answer = self._ask(
                prompt,
                temperature=0.35,
                json_mode=False,
                num_predict=1200,
                read_timeout=150,
            )
            self.status("✅ CHAT IA • réponse terminée")
            return CodexResult(
                True,
                answer,
                [],
                {
                    "mode": "CHAT",
                    "question": question,
                },
            )
        except Exception as exc:
            self.status(f"❌ CHAT IA • {exc}")
            return CodexResult(
                False,
                f"Erreur CHAT IA: {exc}",
                [],
                {"mode": "CHAT"},
            )

    def build(self, request: str, max_tasks: int = 40) -> CodexResult:
        try:
            self.last_outputs = {}
            self.last_stats_by_file = {}
            self.last_diffs = {}

            mode, clean_request = self._execution_mode(request)
            request = clean_request
            if not request:
                raise ValueError("Commande vide après sélection du mode.")

            self.status(
                "⚡ MODE DIRECT • 1 appel Qwen • fichier complet" if mode == "FAST"
                else "🧠 MODE PRO • analyse approfondie"
            )

            task = self.turbo_task(request) if mode == "FAST" else None

                # Si DIRECT ne peut pas déterminer le fichier cible,
            # bascule automatiquement en PRO au lieu d'exiger /pro.
            if mode == "FAST" and task is None:
                mode = "PRO"

            if mode == "FAST" and task is not None:
                plan = {
                    "summary": "Mode DIRECT: un appel Qwen, fichier complet",
                    "architecture": ["1 commande", "1 fichier", "1 appel Qwen", "fichier complet"],
                    "tasks": [task],
                    "request": request,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "mode": "FAST",
                }
            else:
                plan = self.make_plan(request)
                plan["mode"] = "PRO"
            tasks = plan.get("tasks", [])[:2]
            request_lower = request.lower()
            readme_requested = any(alias in request_lower for alias in (
                "readme", "readme.md", "readme.dm", "readm.md", "readm.dm",
                "reamd.md", "reamd.dm", "redame.md", "redame.dm",
            ))
            if not readme_requested:
                for task_item in tasks:
                    files = [str(x) for x in task_item.get("files", [])]
                    if any(Path(x).name.lower() == "readme.md" for x in files):
                        raise ValueError("Le plan IA a ciblé README.md au lieu du fichier demandé.")
            changed = []
            for index, task in enumerate(tasks, 1):
                task_id = task.get("id", f"T{index:02}")
                self.status(f"⚙ {task_id} • {task.get('goal', 'génération')} ({index}/{len(tasks)})")
                target_names = ", ".join(str(x) for x in task.get("files", [])[:1]) or "fichier"
                self.status(f"🎯 Fichier choisi • {target_names}")
                if str(plan.get("mode") or "").upper() == "FAST":
                    items = self.generate_fast_task(request, task)
                else:
                    items = self.generate_task(request, task)
                self.status(f"💾 Sauvegarde • {target_names}")
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
            self._remember_run(request, report["changed"], plan)
            self.status("✅ Génération terminée")
            mode_label = str(plan.get("mode") or "PRO")
            return CodexResult(
                True,
                f"MODE {mode_label} • {len(report['changed'])} fichier(s) modifié(s).",
                report["changed"],
                plan,
            )
        except Exception as exc:
            error_report = {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "request": request,
                "error": str(exc),
            }
            try:
                (self.state_dir / "last_error.json").write_text(
                    json.dumps(error_report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
            self.status(f"❌ Erreur CODEX • {exc}")
            return CodexResult(False, f"Erreur moteur CODEX: {exc}")
