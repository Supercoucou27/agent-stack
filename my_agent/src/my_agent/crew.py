import os
import subprocess
import chromadb
from pydantic import BaseModel
from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from crewai.tools import BaseTool

WORKSPACE = os.environ.get("AGENT_WORKSPACE", "/home/tara/agent-workspace")
CHROMA_PATH = "/home/tara/agent-stack/chroma"

LOCAL_LLM = LLM(
    model="openai/Qwen2.5-Coder-14B",
    base_url=(
        (os.environ.get("LLAMA_URL") or "http://127.0.0.1:8080")
        .replace("localhost", "127.0.0.1")
        .rstrip("/")
        + ("/v1" if not (os.environ.get("LLAMA_URL") or "").endswith("/v1") else "")
    ),
    api_key="sk-no-key-required",
    temperature=float(os.environ.get("CREW_TEMPERATURE", "0.2")),
    top_p=float(os.environ.get("CREW_TOP_P", "0.95")),
    context_window_size=int(os.environ.get("CREW_CTX_SIZE", "65536")),
    max_tokens=int(os.environ.get("CREW_MAX_TOKENS", "4096")),
    frequency_penalty=float(os.environ.get("CREW_FREQ_PENALTY", "0.0")),
    presence_penalty=float(os.environ.get("CREW_PRES_PENALTY", "0.0")),
)

# Strata Memory Hierarchy
_chroma = chromadb.PersistentClient(path=CHROMA_PATH)
_strata_principles = _chroma.get_or_create_collection("strata_principles") # Tier 3: Guardrails
_strata_workflows  = _chroma.get_or_create_collection("strata_workflows")  # Tier 2: Procedures
_strata_episodes   = _chroma.get_or_create_collection("strata_episodes")   # Tier 1: Raw outputs


class DirectoryReadInput(BaseModel):
    directory_path: str = ""

class SafeDirectoryReadTool(BaseTool):
    name: str = "list_files"
    description: str = "Liste les fichiers d'un dossier. Laisse vide pour le workspace courant."
    args_schema: type[BaseModel] = DirectoryReadInput

    def _run(self, directory_path: str = "", **kwargs) -> str:
        base = directory_path.strip() if directory_path.strip() else os.environ.get("AGENT_WORKSPACE", WORKSPACE)
        base = os.path.expanduser(base)
        if not os.path.exists(base):
            return f"Le dossier {base} n'existe pas."
        files = [os.path.join(r, f) for r, _, fs in os.walk(base) for f in fs]
        return f"Fichiers dans {base} :\n- " + "\n- ".join(files[:200]) if files else "Dossier vide."


class ReadInput(BaseModel):
    filename: str

class SafeFileReadTool(BaseTool):
    name: str = "read_file"
    description: str = "Lit un fichier sur le système hôte."
    args_schema: type[BaseModel] = ReadInput

    def _run(self, filename: str, **kwargs) -> str:
        base = os.environ.get("AGENT_WORKSPACE", WORKSPACE)
        target = os.path.expanduser(filename)
        if not os.path.isabs(target):
            target = os.path.join(base, target)
        full = os.path.realpath(target)
        if not os.path.isfile(full):
            return f"ERREUR: fichier introuvable : '{full}'."
        try:
            with open(full, encoding="utf-8") as f:
                return f.read()[:60000]
        except UnicodeDecodeError:
            return f"ERREUR: fichier binaire."


class WriteInput(BaseModel):
    filename: str
    content: str

class SafeFileWriteTool(BaseTool):
    name: str = "write_file"
    description: str = "Écrit du texte dans un fichier sur le système hôte."
    args_schema: type[BaseModel] = WriteInput

    def _run(self, filename: str, content: str, **kwargs) -> str:
        base = os.environ.get("AGENT_WORKSPACE", WORKSPACE)
        target = os.path.expanduser(filename)
        if not os.path.isabs(target):
            target = os.path.join(base, target)
        full = os.path.realpath(target)
        os.makedirs(os.path.dirname(full) or base, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Écrit : {full} ({len(content)} octets)"


class ShellInput(BaseModel):
    command: str
    timeout_s: int = 120

class ShellTool(BaseTool):
    name: str = "shell"
    description: str = "Exécute une commande bash sur la machine hôte."
    args_schema: type[BaseModel] = ShellInput

    def _run(self, command: str, timeout_s: int = 120, **kwargs) -> str:
        base = os.environ.get("AGENT_WORKSPACE", WORKSPACE)
        cwd_dir = base if os.path.isdir(base) else os.path.expanduser("~")
        timeout_s = min(int(timeout_s), int(os.environ.get("HARNESS_MAX_EXEC_TIME", "300")))
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=cwd_dir,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            code = proc.returncode
            if len(out) > 10000:
                out = out[:10000] + "\n...[tronqué]"
            return f"exit={code}\n{out}" if out else f"exit={code} (aucune sortie)"
        except subprocess.TimeoutExpired:
            return f"ERREUR: timeout ({timeout_s}s)"
        except Exception as e:
            return f"ERREUR: {type(e).__name__}: {e}"


# Strata Memory Tools
class StrataRecallInput(BaseModel):
    query: str
    tier: str = "all"

class StrataRecallTool(BaseTool):
    name: str = "strata_recall"
    description: str = "Recherche dans la mémoire hiérarchique. tier peut être 'principles' (règles), 'workflows' (procédures), ou 'all'."
    args_schema: type[BaseModel] = StrataRecallInput

    def _run(self, query: str, tier: str = "all", **kwargs) -> str:
        sections = []
        if tier in ("all", "principles"):
            p_res = _strata_principles.query(query_texts=[query], n_results=3)
            p_docs = p_res.get("documents", [[]])[0]
            if p_docs:
                sections.append("=== PRINCIPES ET RÈGLES (Tier 3) ===\n" + "\n".join(f"- {d}" for d in p_docs))
        
        if tier in ("all", "workflows"):
            w_res = _strata_workflows.query(query_texts=[query], n_results=3)
            w_docs = w_res.get("documents", [[]])[0]
            if w_docs:
                sections.append("=== PROCÉDURES ET WORKFLOWS (Tier 2) ===\n" + "\n---\n".join(w_docs))
                
        return "\n\n".join(sections) if sections else "Aucune correspondance dans la hiérarchie Strata."


class StrataStoreWorkflowInput(BaseModel):
    name: str
    steps: str

class StrataStoreWorkflowTool(BaseTool):
    name: str = "strata_store_workflow"
    description: str = "Enregistre une procédure validée dans Tier 2 (workflows)."
    args_schema: type[BaseModel] = StrataStoreWorkflowInput

    def _run(self, name: str, steps: str, **kwargs) -> str:
        entry = f"Workflow: {name}\nÉtapes:\n{steps}"
        _strata_workflows.add(documents=[entry], metadatas=[{"type": "workflow", "name": name}], ids=[f"wf-{abs(hash(name))}"])
        return f"Workflow '{name}' mémorisé dans Tier 2."


class StrataStorePrincipleInput(BaseModel):
    rule: str

class StrataStorePrincipleTool(BaseTool):
    name: str = "strata_store_principle"
    description: str = "Enregistre une règle ou contrainte obligatoire dans Tier 3 (principles)."
    args_schema: type[BaseModel] = StrataStorePrincipleInput

    def _run(self, rule: str, **kwargs) -> str:
        _strata_principles.add(documents=[rule], metadatas=[{"type": "principle"}], ids=[f"pr-{abs(hash(rule))}"])
        return f"Règle mémorisée dans Tier 3."


@CrewBase
class MyAgentCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def file_assistant(self) -> Agent:
        return Agent(
            config=self.agents_config["file_assistant"],
            llm=LOCAL_LLM,
            tools=[
                SafeDirectoryReadTool(),
                SafeFileReadTool(),
                SafeFileWriteTool(),
                ShellTool(),
                StrataRecallTool(),
                StrataStoreWorkflowTool(),
                StrataStorePrincipleTool(),
            ],
            allow_delegation=False,
            max_iter=int(os.environ.get("HARNESS_MAX_ITER", "35")),
            max_execution_time=int(os.environ.get("HARNESS_MAX_EXEC_TIME", "300")),
            max_retry_limit=int(os.environ.get("HARNESS_MAX_RETRY", "5")),
        )

    @task
    def execute_task(self) -> Task:
        return Task(
            description="{task}",
            expected_output="Le résultat factuel exact produit par les outils.",
            agent=self.file_assistant()
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
            max_rpm=None,
        )
