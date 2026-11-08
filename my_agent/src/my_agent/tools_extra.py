"""Outils supplémentaires pour l'agent local.

Chaque outil :
- hérite de BaseTool (CrewAI)
- définit args_schema Pydantic (contrôle strict des arguments)
- reste confiné à WORKSPACE pour les opérations fichiers
- renvoie une chaîne lisible par le LLM (max 8 Ko)
"""
import io
import json
import os
import re
import sqlite3
import tarfile
import zipfile
from difflib import unified_diff
from pathlib import Path

import httpx
from pydantic import BaseModel
from crewai.tools import BaseTool

try:
    import jmespath
    _JMESPATH_OK = True
except ImportError:
    _JMESPATH_OK = False

try:
    from pypdf import PdfReader
    _PYPDF_OK = True
except ImportError:
    _PYPDF_OK = False

try:
    from duckduckgo_search import DDGS
    _DDGS_OK = True
except ImportError:
    _DDGS_OK = False

WORKSPACE = "/home/tara/agent-workspace"
MAX_OUT = 8000


def _truncate(s: str, n: int = MAX_OUT) -> str:
    return s if len(s) <= n else s[:n] + f"\n...[tronqué à {n} caractères]"


def _safe_path(filename: str) -> Path:
    root = Path(WORKSPACE).resolve()
    p = (root / filename).resolve()
    if p != root and not str(p).startswith(str(root) + os.sep):
        raise ValueError(f"Chemin '{filename}' hors périmètre.")
    return p


# ─── 1. web_fetch ────────────────────────────────────────────────────────────

class WebFetchInput(BaseModel):
    url: str
    max_chars: int = 5000


class WebFetchTool(BaseTool):
    name: str = "web_fetch"
    description: str = (
        "Récupère le contenu d'une URL (HTTP GET). Retourne le titre HTML et "
        "le texte brut (tags retirés). Utile pour lire une page web, un article, une doc. "
        "Nécessite une URL complète (avec https://)."
    )
    args_schema: type[BaseModel] = WebFetchInput

    def _run(self, url: str, max_chars: int = 5000, **kwargs) -> str:
        try:
            r = httpx.get(url, timeout=15, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; LocalAgent/1.0)"})
            r.raise_for_status()
            html = r.text
            title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
            title = title_m.group(1).strip() if title_m else ""
            text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
            text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            out = f"URL: {url}\nStatus: {r.status_code}\nTitle: {title}\n\n{text}"
            return _truncate(out, min(int(max_chars), MAX_OUT))
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"


# ─── 2. web_search ───────────────────────────────────────────────────────────

class WebSearchInput(BaseModel):
    query: str
    max_results: int = 5


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = (
        "Recherche web via DuckDuckGo. Retourne titre, URL et extrait pour chaque résultat. "
        "Utilise pour trouver des informations récentes. Pas besoin de clé API."
    )
    args_schema: type[BaseModel] = WebSearchInput

    def _run(self, query: str, max_results: int = 5, **kwargs) -> str:
        if not _DDGS_OK:
            return "ERREUR: duckduckgo-search non installé."
        try:
            n = max(1, min(int(max_results), 10))
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=n))
            if not results:
                return f"Aucun résultat pour '{query}'."
            lines = [f"Résultats pour '{query}' :\n"]
            for i, r in enumerate(results, 1):
                lines.append(
                    f"{i}. {r.get('title', '?')}\n"
                    f"   URL : {r.get('href', '')}\n"
                    f"   {r.get('body', '')[:300]}"
                )
            return _truncate("\n\n".join(lines))
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"


# ─── 3. grep_files ───────────────────────────────────────────────────────────

class GrepInput(BaseModel):
    pattern: str
    file_glob: str = "*"
    case_sensitive: bool = False
    max_matches: int = 100


class GrepFilesTool(BaseTool):
    name: str = "grep_files"
    description: str = (
        "Recherche une expression régulière dans les fichiers du workspace. "
        "Ex: pattern='def main', file_glob='*.py'. Retourne fichier:ligne:texte. "
        "Utile pour trouver où un symbole est défini/utilisé."
    )
    args_schema: type[BaseModel] = GrepInput

    def _run(self, pattern: str, file_glob: str = "*", case_sensitive: bool = False, max_matches: int = 100, **kwargs) -> str:
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            rx = re.compile(pattern, flags)
        except re.error as e:
            return f"ERREUR: regex invalide : {e}"
        root = Path(WORKSPACE)
        matches = []
        try:
            for p in root.rglob(file_glob):
                if not p.is_file():
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        rel = p.relative_to(root)
                        matches.append(f"{rel}:{n}: {line.strip()[:200]}")
                        if len(matches) >= int(max_matches):
                            break
                if len(matches) >= int(max_matches):
                    break
        except Exception as e:
            return f"ERREUR: {e}"
        if not matches:
            return f"Aucun match pour '{pattern}' (glob={file_glob})."
        return _truncate(f"{len(matches)} match(s) :\n" + "\n".join(matches))


# ─── 4. diff_files ───────────────────────────────────────────────────────────

class DiffInput(BaseModel):
    file_a: str
    file_b: str
    context: int = 3


class DiffFilesTool(BaseTool):
    name: str = "diff_files"
    description: str = (
        "Compare deux fichiers du workspace et retourne le diff unifié. "
        "Fournis deux chemins relatifs (ex: 'v1.txt', 'v2.txt')."
    )
    args_schema: type[BaseModel] = DiffInput

    def _run(self, file_a: str, file_b: str, context: int = 3, **kwargs) -> str:
        try:
            pa = _safe_path(file_a)
            pb = _safe_path(file_b)
            if not pa.is_file():
                return f"ERREUR: {file_a} introuvable."
            if not pb.is_file():
                return f"ERREUR: {file_b} introuvable."
            a = pa.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
            b = pb.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
            diff = list(unified_diff(a, b, fromfile=file_a, tofile=file_b, n=int(context)))
            if not diff:
                return "Les fichiers sont identiques."
            return _truncate("".join(diff))
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"


# ─── 5. archive ──────────────────────────────────────────────────────────────

class ArchiveInput(BaseModel):
    action: str  # create | list | extract
    archive: str
    files: str = ""
    dest: str = "."


class ArchiveTool(BaseTool):
    name: str = "archive"
    description: str = (
        "Gère des archives zip/tar.gz dans le workspace. "
        "Actions : 'create' (files='a.txt b.txt' ou '*' pour tout), "
        "'list' (liste le contenu), 'extract' (extrait dans dest). "
        "archive='mon.zip' ou 'mon.tar.gz'. "
        "EX: action='create', archive='backup.zip', files='*'."
    )
    args_schema: type[BaseModel] = ArchiveInput

    def _run(self, action: str, archive: str, files: str = "", dest: str = ".", **kwargs) -> str:
        try:
            pa = _safe_path(archive)
            if action == "create":
                src_files = []
                if files.strip() in ("", "*"):
                    src_files = [p for p in Path(WORKSPACE).rglob("*") if p.is_file() and p != pa]
                else:
                    for f in files.split():
                        src_files.append(_safe_path(f))
                if not src_files:
                    return "ERREUR: aucun fichier à archiver."
                if pa.suffix in (".zip",):
                    with zipfile.ZipFile(pa, "w", zipfile.ZIP_DEFLATED) as z:
                        for f in src_files:
                            z.write(f, f.relative_to(WORKSPACE))
                elif pa.suffix in (".gz", ".tgz") or pa.name.endswith(".tar.gz"):
                    with tarfile.open(pa, "w:gz") as t:
                        for f in src_files:
                            t.add(f, arcname=str(f.relative_to(WORKSPACE)))
                else:
                    return f"ERREUR: format non supporté ({pa.suffix}). Utilise .zip ou .tar.gz."
                return f"Archive créée : {archive} ({len(src_files)} fichiers, {pa.stat().st_size} octets)"
            elif action == "list":
                if not pa.is_file():
                    return f"ERREUR: {archive} introuvable."
                if pa.suffix == ".zip":
                    with zipfile.ZipFile(pa) as z:
                        names = z.namelist()
                else:
                    with tarfile.open(pa, "r:*") as t:
                        names = t.getnames()
                return _truncate("\n".join(names))
            elif action == "extract":
                if not pa.is_file():
                    return f"ERREUR: {archive} introuvable."
                dest_p = _safe_path(dest)
                dest_p.mkdir(parents=True, exist_ok=True)
                if pa.suffix == ".zip":
                    with zipfile.ZipFile(pa) as z:
                        z.extractall(dest_p)
                else:
                    with tarfile.open(pa, "r:*") as t:
                        t.extractall(dest_p)
                return f"Extrait dans {dest}"
            else:
                return f"ERREUR: action inconnue '{action}' (create|list|extract)."
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"


# ─── 6. sqlite_query ─────────────────────────────────────────────────────────

class SqliteInput(BaseModel):
    db: str
    query: str
    max_rows: int = 50


class SqliteQueryTool(BaseTool):
    name: str = "sqlite_query"
    description: str = (
        "Exécute une requête SQL sur une base SQLite du workspace. "
        "db='data.db' (créé si absent). query='SELECT * FROM t LIMIT 10'. "
        "Support SELECT, INSERT, UPDATE, CREATE. Retourne les résultats en tableau."
    )
    args_schema: type[BaseModel] = SqliteInput

    def _run(self, db: str, query: str, max_rows: int = 50, **kwargs) -> str:
        try:
            p = _safe_path(db)
            p.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(p))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(query)
            if query.strip().upper().startswith("SELECT"):
                rows = cur.fetchmany(int(max_rows))
                if not rows:
                    return "(0 lignes)"
                cols = rows[0].keys()
                header = " | ".join(cols)
                sep = "-|-".join("---" for _ in cols)
                body = "\n".join(" | ".join(str(r[c]) for c in cols) for r in rows)
                return _truncate(f"{header}\n{sep}\n{body}")
            else:
                conn.commit()
                return f"OK, {cur.rowcount} ligne(s) affectée(s)."
        except Exception as e:
            return f"ERREUR SQL: {type(e).__name__}: {e}"
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ─── 7. json_query ───────────────────────────────────────────────────────────

class JsonQueryInput(BaseModel):
    filename: str
    jmespath_expr: str = "@"


class JsonQueryTool(BaseTool):
    name: str = "json_query"
    description: str = (
        "Extrait des données d'un fichier JSON du workspace via JMESPath. "
        "filename='data.json'. jmespath_expr ex: 'users[*].name', 'items[0]', "
        "'foo.bar', '@' pour tout. Retourne le résultat JSON."
    )
    args_schema: type[BaseModel] = JsonQueryInput

    def _run(self, filename: str, jmespath_expr: str = "@", **kwargs) -> str:
        if not _JMESPATH_OK:
            return "ERREUR: jmespath non installé."
        try:
            p = _safe_path(filename)
            if not p.is_file():
                return f"ERREUR: {filename} introuvable."
            data = json.loads(p.read_text(encoding="utf-8"))
            result = jmespath.search(jmespath_expr, data) if jmespath_expr != "@" else data
            return _truncate(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"


# ─── 8. pdf_extract ──────────────────────────────────────────────────────────

class PdfExtractInput(BaseModel):
    filename: str
    max_pages: int = 50


class PdfExtractTool(BaseTool):
    name: str = "pdf_extract"
    description: str = (
        "Extrait le texte d'un PDF du workspace. "
        "filename='doc.pdf'. max_pages=50 par défaut. "
        "Retourne le texte page par page (séparateur '--- Page N ---')."
    )
    args_schema: type[BaseModel] = PdfExtractInput

    def _run(self, filename: str, max_pages: int = 50, **kwargs) -> str:
        if not _PYPDF_OK:
            return "ERREUR: pypdf non installé."
        try:
            p = _safe_path(filename)
            if not p.is_file():
                return f"ERREUR: {filename} introuvable."
            reader = PdfReader(str(p))
            total = len(reader.pages)
            limit = min(int(max_pages), total)
            out = [f"PDF : {filename} ({total} pages, extraction {limit} max)\n"]
            for i in range(limit):
                try:
                    txt = reader.pages[i].extract_text() or ""
                    out.append(f"\n--- Page {i+1} ---\n{txt.strip()}")
                except Exception as e:
                    out.append(f"\n--- Page {i+1} (erreur) --- {e}")
            return _truncate("".join(out))
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"
