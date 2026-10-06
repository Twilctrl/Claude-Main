"""Settings (/etc/cerberos/cerberos.conf) and the model/app catalog."""

import glob
import os
import tomllib
from dataclasses import dataclass, field

ETC = os.environ.get("CERBEROS_ETC", "/etc/cerberos")
LIB = os.environ.get("CERBEROS_LIB", "/usr/local/lib/cerberos")
STATE = os.environ.get("CERBEROS_STATE", "/var/lib/cerberos")
MODELS_DIR = os.environ.get("CERBEROS_MODELS_DIR", "/srv/local-models")

DEFAULTS = {
    "CERBEROS_DEFAULT_MODEL": "chatbots/llama-npu",
    "CERBEROS_NPU_URL": "http://127.0.0.1:8000",
    "CERBEROS_CPU_URL": "http://127.0.0.1:11436",
    "CERBEROS_REMOTE_URL": "",
    "CERBEROS_REMOTE_API": "ollama",
    "CERBEROS_REMOTE_KEY": "",
    "CERBEROS_GATE_PORT": "11434",
    "CERBEROS_GATE_LAN": "0",
    "CERBEROS_GATE_KEY": "",
    "CERBEROS_GOVERNOR": "performance",
    "CERBEROS_FIRSTBOOT_APPS": "open-webui",
}

HEADS = ("npu", "cpu", "remote")
HEAD_LABEL = {"npu": "NPU · Hailo-10H", "cpu": "CPU · Ollama", "remote": "REMOTE"}


def read_conf(path=None):
    """Parse a shell-style KEY=value file. Environment variables win."""
    conf = dict(DEFAULTS)
    try:
        with open(path or os.path.join(ETC, "cerberos.conf")) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                conf[k.strip()] = v
    except OSError:
        pass
    for k in list(conf):
        if k in os.environ:
            conf[k] = os.environ[k]
    return conf


def head_urls(conf):
    urls = {"npu": conf["CERBEROS_NPU_URL"], "cpu": conf["CERBEROS_CPU_URL"],
            "remote": conf["CERBEROS_REMOTE_URL"]}
    return {h: u.rstrip("/") for h, u in urls.items() if u}


def gate_url(conf):
    return f"http://127.0.0.1:{conf['CERBEROS_GATE_PORT']}"


@dataclass
class Category:
    id: str
    name: str
    tagline: str = ""
    icon: str = "applications-other"
    index: int = 0

    @property
    def label(self):
        return f"{self.index:02d} · {self.name}"


@dataclass
class Model:
    category: str
    slug: str
    name: str
    head: str
    model: str
    preload: str = "ondemand"
    size_gb: float = 0.0
    ram_gb: float = 0.0
    strengths: list = field(default_factory=list)
    blurb: str = ""
    embedding: bool = False
    maker: str = ""
    origin: str = ""

    @property
    def alias(self):
        return f"{self.category}/{self.slug}"


@dataclass
class App:
    id: str
    category: str
    name: str
    kind: str
    blurb: str = ""
    run: list = field(default_factory=list)
    preinstalled: bool = False
    image: str = ""
    port: int = 0
    package: str = ""
    plugins: list = field(default_factory=list)
    bin: str = ""
    env: dict = field(default_factory=dict)
    needs: list = field(default_factory=list)  # models to pull before launching


class Catalog:
    def __init__(self, categories, models, apps):
        self.categories = categories
        self.models = models
        self.apps = apps
        self.by_alias = {m.alias: m for m in models}
        self.apps_by_id = {a.id: a for a in apps}

    @classmethod
    def load(cls, etc=None):
        etc = etc or ETC
        files = [os.path.join(etc, "catalog.toml")]
        files += sorted(glob.glob(os.path.join(etc, "catalog.d", "*.toml")))
        cats, models, apps = {}, {}, {}
        for path in files:
            try:
                with open(path, "rb") as f:
                    data = tomllib.load(f)
            except FileNotFoundError:
                continue
            for c in data.get("category", []):
                cats[c["id"]] = Category(**c)
            for m in data.get("model", []):
                mod = Model(**m)
                models[mod.alias] = mod  # later files override earlier ones
            for a in data.get("app", []):
                apps[a["id"]] = App(**a)
        for i, c in enumerate(cats.values(), 1):
            c.index = i
        for item in list(models.values()) + list(apps.values()):
            if item.category not in cats:
                cats[item.category] = Category(item.category, item.category.title(),
                                               index=len(cats) + 1)
        return cls(list(cats.values()), list(models.values()), list(apps.values()))

    def category(self, cid):
        return next((c for c in self.categories if c.id == cid), None)

    def models_in(self, cid):
        return [m for m in self.models if m.category == cid]

    def apps_in(self, cid):
        return [a for a in self.apps if a.category == cid]

    def find_model(self, name):
        """Catalog entry for an alias like chatbots/gemma (or local-models/...)."""
        name = name.strip().removeprefix("/srv/").removeprefix("local-models/").rstrip("/")
        return self.by_alias.get(name)


def mem_total_gb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 0.0
