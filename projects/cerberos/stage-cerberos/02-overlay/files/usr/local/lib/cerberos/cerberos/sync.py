"""Generate /srv/local-models and the categorized start menu from the catalog.

    /srv/local-models/<category>/<slug>/README.md   model card
    /srv/local-models/<category>/<slug>/chat        ./chat to talk to it
    /usr/share/applications/cerberos-*.desktop      one per app and model
    /usr/share/desktop-directories/cerberos-*.directory
    /etc/xdg/menus/applications-merged/cerberos.menu  Kali-style numbered categories
"""

import os
import shutil
from xml.sax.saxutils import escape

from .config import MODELS_DIR, Catalog

APPS_DIR = "/usr/share/applications"
DIRS_DIR = "/usr/share/desktop-directories"
MENU_FILE = "/etc/xdg/menus/applications-merged/cerberos.menu"
HEAD_TEXT = {"npu": "Hailo-10H NPU (hailo-ollama)", "cpu": "Pi CPU (Ollama)",
             "remote": "remote head (another machine on your LAN)"}


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def model_card(m, cat):
    lines = [
        f"# {m.name}",
        "",
        f"> {cat.label}: {cat.tagline}" if cat else "",
        "",
        m.blurb,
        "",
        "| | |",
        "| --- | --- |",
        f"| Call it | `{m.alias}` |",
        f"| Runs on | {HEAD_TEXT.get(m.head, m.head)} |",
        f"| Upstream name | `{m.model}` |",
        f"| Strengths | {', '.join(m.strengths)} |",
    ]
    if m.size_gb:
        lines.append(f"| Download | {m.size_gb:g} GB |")
    if m.ram_gb:
        lines.append(f"| RAM needed | {m.ram_gb:g} GB |")
    lines.append(f"| Ships | {dict(build='baked into the image', firstboot='downloaded at first boot').get(m.preload, 'downloaded when you first use it')} |")
    lines += ["", "## Use it", "", "```sh"]
    if m.embedding:
        lines.append(f"curl localhost:11434/api/embed -d '{{\"model\": \"{m.alias}\", \"input\": \"hello\"}}'")
    else:
        lines += [f"./chat                         # or: cerb chat {m.alias}",
                  f"cerb ask -m {m.alias} \"...\"",
                  f"curl localhost:11434/api/chat -d '{{\"model\": \"{m.alias}\", "
                  "\"messages\": [{\"role\": \"user\", \"content\": \"hi\"}]}'"]
    lines += ["```", ""]
    return "\n".join(lines)


def sync_models_tree(cat_by_id, catalog, root=MODELS_DIR):
    os.makedirs(root, exist_ok=True)
    wanted = set()
    for m in catalog.models:
        d = os.path.join(root, m.category, m.slug)
        wanted.add(d)
        write(os.path.join(d, "README.md"), model_card(m, cat_by_id.get(m.category)))
        if m.embedding:
            continue
        write(os.path.join(d, "chat"),
              f"#!/bin/sh\n# Talk to {m.name}\nexec cerb chat {m.alias} \"$@\"\n", 0o755)
    # Remove model dirs that left the catalog (only ones we generated).
    for cat in os.listdir(root):
        cdir = os.path.join(root, cat)
        if not os.path.isdir(cdir):
            continue
        for slug in os.listdir(cdir):
            d = os.path.join(cdir, slug)
            if d not in wanted and os.path.exists(os.path.join(d, "README.md")):
                shutil.rmtree(d)
        if not os.listdir(cdir):
            os.rmdir(cdir)
    index = ["# local-models", "", "Every model CerberOS knows, sorted by strength. "
             "Call any of them by its path: `cerb chat chatbots/gemma`.", ""]
    for c in catalog.categories:
        ms = catalog.models_in(c.id)
        if not ms:
            continue
        index += [f"## {c.label}: {c.tagline}", ""]
        index += [f"- `{m.alias}`: {m.name} ({m.head}). {', '.join(m.strengths)}" for m in ms]
        index.append("")
    write(os.path.join(root, "README.md"), "\n".join(index))


def desktop_entry(name, comment, exec_args, icon, category, terminal=True):
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={name}",
        f"Comment={comment}",
        f"Exec=env CERBEROS_FROM_MENU=1 cerb {exec_args}",
        f"Icon={icon}",
        f"Terminal={'true' if terminal else 'false'}",
        f"Categories=X-CerberOS-{category};",
        "",
    ])


def sync_menu(catalog, apps_dir=APPS_DIR, dirs_dir=DIRS_DIR, menu_file=MENU_FILE):
    if os.path.isdir(apps_dir):
        for f in os.listdir(apps_dir):
            if f.startswith("cerberos-") and f.endswith(".desktop"):
                os.remove(os.path.join(apps_dir, f))
    for c in catalog.categories:
        write(os.path.join(dirs_dir, f"cerberos-{c.id}.directory"), "\n".join([
            "[Desktop Entry]", "Type=Directory", f"Name={c.label}",
            f"Comment={c.tagline}", f"Icon={c.icon}", ""]))
    for a in catalog.apps:
        write(os.path.join(apps_dir, f"cerberos-{a.id}.desktop"),
              desktop_entry(a.name, a.blurb, f"launch {a.id}", "cerberos", a.category))
    for m in catalog.models:
        if m.embedding:
            continue
        write(os.path.join(apps_dir, f"cerberos-model-{m.category}-{m.slug}.desktop"),
              desktop_entry(f"{m.name}", f"{', '.join(m.strengths)}. {m.blurb}",
                            f"chat {m.alias}", "cerberos-model", m.category))
    menus = []
    for c in catalog.categories:
        # <Name> is an internal id; the .directory file supplies the visible label.
        menus.append(f"""  <Menu>
    <Name>CerberOS-{c.index:02d}-{escape(c.id)}</Name>
    <Directory>cerberos-{escape(c.id)}.directory</Directory>
    <Include><Category>X-CerberOS-{escape(c.id)}</Category></Include>
  </Menu>""")
    write(menu_file, f"""<!DOCTYPE Menu PUBLIC "-//freedesktop//DTD Menu 1.0//EN"
 "http://www.freedesktop.org/standards/menu-spec/menu-1.0.dtd">
<!-- Generated by `cerb sync` from /etc/cerberos/catalog.toml. Don't edit. -->
<Menu>
  <Name>Applications</Name>
{chr(10).join(menus)}
</Menu>
""")


def sync(catalog=None, models_root=MODELS_DIR, desktop=True):
    catalog = catalog or Catalog.load()
    cat_by_id = {c.id: c for c in catalog.categories}
    sync_models_tree(cat_by_id, catalog, models_root)
    if desktop:
        sync_menu(catalog)
    return catalog
