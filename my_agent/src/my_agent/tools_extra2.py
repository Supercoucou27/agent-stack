"""Outils supplémentaires (batch 2) pour l'agent local.

Chaque outil reste confiné au workspace pour les opérations fichiers,
sauf les outils système (docker, notify, screenshot) qui s'exécutent
explicitement sur l'hôte.
"""
import io
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from pydantic import BaseModel
from crewai.tools import BaseTool

try:
    import pandas as pd
    _PANDAS_OK = True
except ImportError:
    _PANDAS_OK = False

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

WORKSPACE = "/home/tara/agent-workspace"
LLAMA_PORT = 8080
MAX_OUT = 8000


def _truncate(s: str, n: int = MAX_OUT) -> str:
    return s if len(s) <= n else s[:n] + f"\n...[tronqué à {n} caractères]"


def _safe_path(filename: str) -> Path:
    root = Path(WORKSPACE).resolve()
    p = (root / filename).resolve()
    if p != root and not str(p).startswith(str(root) + os.sep):
        raise ValueError(f"Chemin '{filename}' hors périmètre.")
    return p


def _run(cmd, cwd=None, timeout=30):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return -1, f"timeout après {timeout}s"
    except FileNotFoundError:
        return -2, f"commande introuvable : {cmd[0]}"
    except Exception as e:
        return -3, f"{type(e).__name__}: {e}"


# ─── 1. git_operations ───────────────────────────────────────────────────────

class GitInput(BaseModel):
    action: str          # status | log | diff | commit | add | branch | clone
    repo: str = "."      # chemin relatif au workspace
    args: str = ""       # arguments supplémentaires (ex: message de commit)
    max_lines: int = 100


class GitTool(BaseTool):
    name: str = "git_operations"
    description: str = (
        "Opérations git sur un dépôt dans le workspace. "
        "Actions : 'status', 'log', 'diff', 'add', 'commit', 'branch', 'clone'. "
        "repo='.' (workspace) ou 'mon-projet'. "
        "Pour commit : args='mon message'. Pour clone : args='https://github.com/x/y.git mon-dossier'. "
        "Retourne la sortie git (tronquée à max_lines lignes)."
    )
    args_schema: type[BaseModel] = GitInput

    def _run(self, action: str, repo: str = ".", args: str = "", max_lines: int = 100, **kwargs) -> str:
        try:
            p = _safe_path(repo)
        except ValueError as e:
            return f"ERREUR: {e}"
        if action == "clone":
            parts = args.split()
            if len(parts) < 2:
                return "ERREUR: args doit être 'URL DESTINATION'."
            url, dest = parts[0], parts[1]
            code, out = _run(["git", "clone", url, dest], cwd=WORKSPACE, timeout=120)
        else:
            if not p.is_dir():
                return f"ERREUR: {repo} n'est pas un répertoire."
            if not (p / ".git").exists():
                return f"ERREUR: {repo} n'est pas un dépôt git."
            cmds = {
                "status": ["git", "status", "--short"],
                "log": ["git", "log", "--oneline", "-n", str(max_lines)],
                "diff": ["git", "diff"] + (args.split() if args else []),
                "add": ["git", "add"] + (args.split() if args else ["."]),
                "commit": ["git", "commit", "-m", args] if args else None,
                "branch": ["git", "branch", "-a"],
            }
            cmd = cmds.get(action)
            if cmd is None:
                return f"ERREUR: action '{action}' inconnue ou args manquants (commit nécessite args='message')."
            code, out = _run(cmd, cwd=p)
        lines = out.splitlines()
        if len(lines) > int(max_lines):
            out = "\n".join(lines[:int(max_lines)]) + f"\n...[+{len(lines)-int(max_lines)} lignes]"
        return f"git {action} → exit={code}\n{_truncate(out)}"
    


# ─── 2. docker_manage ────────────────────────────────────────────────────────

class DockerInput(BaseModel):
    action: str          # ps | logs | start | stop | restart | inspect
    container: str = ""
    lines: int = 100


class DockerTool(BaseTool):
    name: str = "docker_manage"
    description: str = (
        "Gère les conteneurs Docker sur l'hôte. "
        "Actions : 'ps' (liste), 'logs' (container requis), 'start', 'stop', 'restart', 'inspect'. "
        "container='nom_ou_id'. Retourne la sortie Docker."
    )
    args_schema: type[BaseModel] = DockerInput

    def _run(self, action: str, container: str = "", lines: int = 100, **kwargs) -> str:
        if action == "ps":
            code, out = _run(["docker", "ps", "-a", "--format",
                              "table {{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}"])
            return _truncate(f"exit={code}\n{out}")
        if not container:
            return "ERREUR: 'container' requis pour cette action."
        cmds = {
            "logs": ["docker", "logs", "--tail", str(int(lines)), container],
            "start": ["docker", "start", container],
            "stop": ["docker", "stop", container],
            "restart": ["docker", "restart", container],
            "inspect": ["docker", "inspect", "--format",
                        "{{.Name}} | {{.State.Status}} | {{.Config.Image}}", container],
        }
        cmd = cmds.get(action)
        if cmd is None:
            return f"ERREUR: action '{action}' inconnue."
        code, out = _run(cmd, timeout=30)
        return _truncate(f"exit={code}\n{out}")


# ─── 3. csv_query ────────────────────────────────────────────────────────────

class CsvInput(BaseModel):
    filename: str
    query: str       # ex: "ville == 'Paris'" | "age > 30" | "groupby ville"
    max_rows: int = 30


class CsvQueryTool(BaseTool):
    name: str = "csv_query"
    description: str = (
        "Charge un CSV du workspace et exécute un filtre ou groupby. "
        "filename='data.csv'. "
        "query ex: \"ville == 'Paris'\", \"age > 30\", \"groupby ville\", "
        "\"sort age desc\", \"head 10\", \"columns\" (liste les colonnes). "
        "Retourne les lignes au format texte (tronqué à max_rows)."
    )
    args_schema: type[BaseModel] = CsvInput

    def _run(self, filename: str, query: str, max_rows: int = 30, **kwargs) -> str:
        if not _PANDAS_OK:
            return "ERREUR: pandas non installé."
        try:
            p = _safe_path(filename)
            if not p.is_file():
                return f"ERREUR: {filename} introuvable."
            df = pd.read_csv(p)
            q = query.strip().lower()
            if q == "columns":
                return "Colonnes : " + ", ".join(df.columns) + f"\nLignes : {len(df)}"
            if q.startswith("head"):
                n = int(q.split()[1]) if len(q.split()) > 1 else 5
                return _truncate(df.head(n).to_string(index=False))
            if q.startswith("groupby "):
                col = q.split()[1]
                agg = df.groupby(col).size().reset_index(name="count")
                return _truncate(agg.to_string(index=False))
            if q.startswith("sort "):
                parts = q.split()
                col = parts[1]
                asc = not (len(parts) > 2 and parts[2] == "desc")
                return _truncate(df.sort_values(col, ascending=asc).head(int(max_rows)).to_string(index=False))
            # filtre pandas via query()
            filtered = df.query(query).head(int(max_rows))
            return _truncate(filtered.to_string(index=False))
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"


# ─── 4. image_info ───────────────────────────────────────────────────────────

class ImageInput(BaseModel):
    filename: str


class ImageInfoTool(BaseTool):
    name: str = "image_info"
    description: str = (
        "Analyse une image du workspace : dimensions, format, mode, EXIF, palette. "
        "filename='photo.jpg' ou 'images/logo.png'. "
        "Utile pour vérifier avant traitement ou générer un rapport."
    )
    args_schema: type[BaseModel] = ImageInput

    def _run(self, filename: str, **kwargs) -> str:
        if not _PIL_OK:
            return "ERREUR: Pillow non installé."
        try:
            p = _safe_path(filename)
            if not p.is_file():
                return f"ERREUR: {filename} introuvable."
            img = Image.open(p)
            lines = [
                f"Fichier : {filename}",
                f"Format : {img.format}",
                f"Dimensions : {img.width} x {img.height} px",
                f"Mode : {img.mode}",
                f"Taille : {p.stat().st_size} octets",
            ]
            exif = img.getexif()
            if exif:
                lines.append("\nEXIF :")
                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, tag_id)
                    lines.append(f"  {tag} : {str(value)[:100]}")
            if img.mode == "P":
                lines.append(f"Palette : {len(img.getpalette())//3} couleurs")
            return _truncate("\n".join(lines))
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"


# ─── 5. notify ───────────────────────────────────────────────────────────────

class NotifyInput(BaseModel):
    title: str
    message: str
    urgency: str = "normal"   # low | normal | critical


class NotifyTool(BaseTool):
    name: str = "notify"
    description: str = (
        "Envoie une notification desktop (via notify-send). "
        "title='Rapport prêt', message='...', urgency='normal' (ou 'low', 'critical'). "
        "Utile pour les tâches longues : notifie l'utilisateur quand c'est fini."
    )
    args_schema: type[BaseModel] = NotifyInput

    def _run(self, title: str, message: str, urgency: str = "normal", **kwargs) -> str:
        if not shutil.which("notify-send"):
            return "ERREUR: notify-send absent (sudo apt install libnotify-bin)."
        u = urgency if urgency in ("low", "normal", "critical") else "normal"
        code, out = _run(["notify-send", "-u", u, title, message], timeout=5)
        return f"notification envoyée (exit={code})" if code == 0 else f"ERREUR: {out}"


# ─── 6. screenshot ───────────────────────────────────────────────────────────

class ScreenshotInput(BaseModel):
    filename: str = "screenshot.png"


class ScreenshotTool(BaseTool):
    name: str = "screenshot"
    description: str = (
        "Capture l'écran actuel dans un fichier PNG du workspace. "
        "filename='screenshot.png' par défaut. "
        "Utile pour documenter un état ou analyser visuellement."
    )
    args_schema: type[BaseModel] = ScreenshotInput

    def _run(self, filename: str = "screenshot.png", **kwargs) -> str:
        try:
            p = _safe_path(filename)
            p.parent.mkdir(parents=True, exist_ok=True)
            if shutil.which("scrot"):
                code, out = _run(["scrot", "-o", str(p)], timeout=10)
            elif shutil.which("gnome-screenshot"):
                code, out = _run(["gnome-screenshot", "-f", str(p)], timeout=10)
            elif shutil.which("import"):
                code, out = _run(["import", "-window", "root", str(p)], timeout=10)
            else:
                return "ERREUR: aucun outil de capture (scrot/gnome-screenshot/import)."
            if code != 0:
                return f"ERREUR: {out}"
            return f"Capture enregistrée : {filename} ({p.stat().st_size} octets)"
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"


# ─── 7. todo ─────────────────────────────────────────────────────────────────

class TodoInput(BaseModel):
    action: str          # add | list | done | delete | clear
    text: str = ""
    tid: int = 0


class TodoTool(BaseTool):
    name: str = "todo"
    description: str = (
        "Gestionnaire de tâches persistant (JSON dans le workspace). "
        "Actions : 'add' (text requis), 'list', 'done' (tid requis), "
        "'delete' (tid requis), 'clear' (tout supprimer). "
        "Utile pour suivre l'avancement d'un projet sur plusieurs sessions."
    )
    args_schema: type[BaseModel] = TodoInput

    def _run(self, action: str, text: str = "", tid: int = 0, **kwargs) -> str:
        try:
            p = _safe_path(".todo.json")
            todos = []
            if p.exists():
                try:
                    todos = json.loads(p.read_text())
                except Exception:
                    todos = []
            if action == "add":
                if not text:
                    return "ERREUR: 'text' requis pour add."
                new_id = max([t.get("id", 0) for t in todos] + [0]) + 1
                todos.append({"id": new_id, "text": text, "done": False,
                              "created": time.strftime("%Y-%m-%d %H:%M")})
                p.write_text(json.dumps(todos, indent=2))
                return f"Ajouté #{new_id} : {text}"
            if action == "list":
                if not todos:
                    return "Aucune tâche."
                lines = []
                for t in todos:
                    mark = "✓" if t.get("done") else "○"
                    lines.append(f"{mark} #{t['id']} {t['text']}  [{t.get('created','')}]")
                return "\n".join(lines)
            if action == "done":
                for t in todos:
                    if t["id"] == tid:
                        t["done"] = True
                        p.write_text(json.dumps(todos, indent=2))
                        return f"#{tid} marqué comme fait."
                return f"ERREUR: #{tid} introuvable."
            if action == "delete":
                before = len(todos)
                todos = [t for t in todos if t["id"] != tid]
                p.write_text(json.dumps(todos, indent=2))
                return f"#{tid} supprimé." if len(todos) < before else f"ERREUR: #{tid} introuvable."
            if action == "clear":
                p.write_text("[]")
                return "Toutes les tâches supprimées."
            return f"ERREUR: action inconnue '{action}'."
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"


# ─── 8. translate ────────────────────────────────────────────────────────────

class TranslateInput(BaseModel):
    text: str
    target_lang: str = "en"
    source_lang: str = "auto"


class TranslateTool(BaseTool):
    name: str = "translate"
    description: str = (
        "Traduit un texte via le LLM local (llama-server). "
        "text='Bonjour le monde', target_lang='en' (ou 'fr', 'es', 'de', 'zh', etc.), "
        "source_lang='auto' par défaut. Retourne la traduction seule."
    )
    args_schema: type[BaseModel] = TranslateInput

    def _run(self, text: str, target_lang: str = "en", source_lang: str = "auto", **kwargs) -> str:
        try:
            prompt = (
                f"Traduis le texte suivant en {target_lang}. "
                f"Réponds UNIQUEMENT avec la traduction, sans explication ni guillemets.\n\n"
                f"Texte : {text}"
            )
            r = httpx.post(
                f"http://localhost:{LLAMA_PORT}/v1/chat/completions",
                json={
                    "model": "local",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 1500,
                },
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"
