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
