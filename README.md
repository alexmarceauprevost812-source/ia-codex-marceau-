# TI-LEX CODEX

Assistant de programmation local en Python, avec interface terminal gamer dark et IA gratuite via Ollama.

## V1

- coloration syntaxique du code avec Pygments/Rich
- écriture fluide
- chat IA local
- page interactive `-aide`
- 20 choix de polices
- affichage de fichiers
- explication et correction proposées par l'IA
- exécution Python avec confirmation

## Installation Kali / Ubuntu

```bash
git clone https://github.com/alexmarceauprevost812-source/ia-codex-marceau-.git
cd ia-codex-marceau-
git checkout main
chmod +x install.sh
./install.sh
source .venv/bin/activate
python codex.py
```

## Installation Windows 10 / 11

### 1. Installer Ollama

Ouvre **PowerShell** et lance :

```powershell
winget install Ollama.Ollama
```

Ferme puis rouvre PowerShell et vérifie :

```powershell
ollama --version
```

### 2. Télécharger l'IA gratuite Qwen Coder

```powershell
ollama pull qwen2.5-coder:7b
```

Vérifie que le modèle est présent :

```powershell
ollama list
```

Tu peux tester directement le modèle avec :

```powershell
ollama run qwen2.5-coder:7b
```

### 3. Installer TI-LEX CODEX

Dans PowerShell, place-toi dans le dossier du projet puis lance :

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
```

### 4. Lancer TI-LEX CODEX

```powershell
.\start_codex.bat
```

TI-LEX CODEX utilise Ollama localement sur `127.0.0.1:11434`. Aucune clé API payante n'est nécessaire pour le modèle local.

## Agent DeepSeek

L'interface graphique peut utiliser DeepSeek comme agent distant.

1. Ouvre **CLÉS API** puis **Enregistrer clé DeepSeek**.
2. Ouvre **AGENT** puis sélectionne **DEEPSEEK**.
3. Le moteur utilise par défaut `deepseek-flash` avec le mode de réflexion activé en effort `high`.
4. La clé reste dans le coffre de secrets du système via `keyring`; elle n'est pas écrite dans le dépôt.

Variables optionnelles :

```text
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_REASONING_EFFORT=high
```

Valeurs acceptées pour `DEEPSEEK_REASONING_EFFORT` : `none`, `low`, `high`, `max`.

## IA locale gratuite

Installe Ollama depuis sa documentation officielle, puis :

```bash
ollama pull qwen2.5-coder:7b
```

TI-LEX CODEX contacte Ollama localement sur `127.0.0.1:11434`.

## Commandes

```text
-aide
-chat
-ouvrir fichier.py
-explique fichier.py
-corrige fichier.py
-run fichier.py
-police
-retour
-quitter
```

## Polices

Le sélecteur propose 20 familles. La V1 mémorise le choix dans `~/.ti_lex_codex/config.json`. La police doit être installée sur Linux et, selon l'émulateur de terminal, sélectionnée dans les préférences du terminal pour modifier réellement les glyphes affichés.

## Sécurité

L'analyse et l'affichage ne modifient pas les fichiers. `-run` demande une confirmation et la V1 limite l'exécution automatique aux fichiers Python.

## Interface graphique TI-LEX CODEX

Une nouvelle interface PySide6 séparée du terminal est disponible sur la branche `main`.

### Windows

Après l'installation :

```powershell
.\start_codex_gui.bat
```

### Kali / Ubuntu

```bash
source .venv/bin/activate
python codex_gui.py
```

Fonctions de la V1 graphique :

- navigation complète à la souris
- explorateur de projet
- un seul fichier affiché à la fois
- éditeur de code sombre avec coloration Python
- boutons Lancer, Sauvegarder, Tester, Build, Ouvrir projet et Créer ZIP
- statut Ollama et modèle local
- console de statut
- commande Codex en bas
- modes AUTO, PRO et DIRECT
- utilisation du vrai `CodexEngine`

