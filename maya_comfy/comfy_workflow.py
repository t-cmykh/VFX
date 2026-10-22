"""Injection des valeurs de Maya dans un workflow ComfyUI exporte au format API.

Le workflow reste construit a la main dans ComfyUI : on repere les noeuds a piloter par leur
titre (clic droit > Title). Un titre peut contenir plusieurs jetons, ex. "MAYA_SEED MAYA_DENOISE"
sur un KSampler, et plusieurs noeuds peuvent porter le meme jeton (ex. deux redimensionnements MAYA_SIZE).
"""
import copy
import json
import re

# jeton -> entrees candidates du noeud (la premiere presente est remplie). "value" = noeuds Primitive*.
SLOTS = {
    "MAYA_PROMPT": ("text", "prompt", "value"),
    "MAYA_NEGATIVE": ("text", "prompt", "value"),
    "MAYA_SEED": ("seed", "noise_seed", "value"),
    "MAYA_DENOISE": ("denoise", "strength", "value"),
    "MAYA_IMAGE": ("image",),
    "MAYA_DEPTH": ("image",),
    "MAYA_VIDEO": ("video", "file"),
    "MAYA_DEPTH_VIDEO": ("video", "file"),
    "MAYA_FRAMES": ("length", "num_frames", "frame_count", "frames", "value"),
    "MAYA_FPS": ("frame_rate", "fps", "value"),
    "MAYA_WIDTH": ("width", "value"),
    "MAYA_HEIGHT": ("height", "value"),
}
KEY_RE = re.compile(r"^MAYA_KEY_(\d+)$")
KEY_IDX_RE = re.compile(r"^MAYA_KEY_(\d+)_IDX$")
TOKEN_SPLIT = re.compile(r"[\s,;|]+")


class WorkflowError(ValueError):
    pass


def load_api_workflow(path):
    try:
        with open(path, encoding="utf-8") as f:
            wf = json.load(f)
    except FileNotFoundError:
        raise WorkflowError("Workflow introuvable : %s" % path)
    if isinstance(wf, dict) and "nodes" in wf and "links" in wf:
        raise WorkflowError("%s est au format interface. Dans ComfyUI : Workflow > Export (API)." % path)
    if not isinstance(wf, dict) or not wf or not all(isinstance(n, dict) and "class_type" in n for n in wf.values()):
        raise WorkflowError("%s n'est pas un workflow ComfyUI au format API." % path)
    return wf


def node_tokens(node):
    return [t for t in TOKEN_SPLIT.split(node.get("_meta", {}).get("title", "")) if t]


def tokens(wf):
    return {t for node in wf.values() for t in node_tokens(node)}


def key_slots(wf):
    """Nombre d'emplacements d'images cles MAYA_KEY_1..n (doivent etre numerotes sans trou)."""
    nums = sorted({int(m.group(1)) for t in tokens(wf) for m in [KEY_RE.match(t)] if m})
    if nums and nums != list(range(1, len(nums) + 1)):
        raise WorkflowError("Images cles mal numerotees : %s (attendu MAYA_KEY_1..%d)" % (nums, len(nums)))
    return len(nums)


def _candidates(token):
    if token in SLOTS:
        return SLOTS[token]
    if KEY_RE.match(token):
        return ("image",)
    if KEY_IDX_RE.match(token):
        return ("frame_idx", "index", "frame", "value")
    return None


def _is_link(value):
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)


def _set_node(node_id, node, token, value):
    inputs = node.setdefault("inputs", {})
    if token == "MAYA_SIZE":
        done = False
        for key, v in (("width", value[0]), ("height", value[1])):
            if key in inputs and not _is_link(inputs[key]):
                inputs[key] = v
                done = True
        if done:
            return
        cands = ("width", "height")
    else:
        cands = _candidates(token)
        if cands is None:
            raise WorkflowError("Jeton inconnu : %s" % token)
        for key in cands:
            if key in inputs and not _is_link(inputs[key]):
                inputs[key] = value
                return
    raise WorkflowError("Noeud %s (%s) titre %s : aucune entree libre parmi %s"
                        % (node_id, node.get("class_type"), token, ", ".join(cands)))


def inject(wf, params):
    """Copie du workflow avec les valeurs posees. Renvoie (workflow, jetons absents du workflow)."""
    wf = copy.deepcopy(wf)
    missing = []
    for token, value in params.items():
        hit = False
        for node_id, node in wf.items():
            if token in node_tokens(node):
                _set_node(node_id, node, token, value)
                hit = True
        if not hit:
            missing.append(token)
    return wf, missing


def assign_keys(keys, slots):
    """Repartit les images cles (triees par frame) sur les emplacements du workflow.

    Trop d'images : on garde la premiere, la derniere et des intermediaires reparties.
    Pas assez : la derniere est repetee (meme image au meme index, sans effet visible).
    """
    if not keys or not slots:
        return []
    keys = list(keys)
    if len(keys) > slots:
        if slots == 1:
            keys = keys[:1]
        else:
            keys = [keys[round(i * (len(keys) - 1) / (slots - 1))] for i in range(slots)]
    return keys + [keys[-1]] * (slots - len(keys))


def snap_frames(n, step=8, max_frames=None):
    """Nombre de frames de la forme step*k+1 (LTX : 8k+1, Wan : 4k+1), arrondi vers le bas."""
    if max_frames:
        n = min(n, max_frames)
    return max(1, (n - 1) // step) * step + 1


def snap(v, multiple):
    return max(multiple, int(round(v / float(multiple))) * multiple)


def size_from_aspect(width, height, long_side, multiple):
    """Taille de generation au ratio du rendu Maya, cote long impose, multiple de `multiple`."""
    if width >= height:
        return snap(long_side, multiple), snap(long_side * height / float(width), multiple)
    return snap(long_side * width / float(height), multiple), snap(long_side, multiple)
