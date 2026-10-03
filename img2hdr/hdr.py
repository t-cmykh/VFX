"""Photo LDR -> pseudo-HDRI equirectangulaire (lineaire, sRGB/Rec.709 en interne)."""
import cv2
import numpy as np

import colorspace as cs


def luma(rgb):
    return rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


# ---------- 1. expansion des hautes lumieres ----------
def emitter_mask(enc, core_thresh=0.985, max_area_frac=0.02):
    """Sources lumineuses probables : noyaux ecretes (les 3 canaux ~ saturés) de taille raisonnable.

    Le min des canaux exclut un fond bleu sature ; la limite de surface exclut un grand mur blanc.
    Retourne un masque flottant [0,1] adouci (halo de 2 px).
    """
    core = (enc.min(axis=2) >= core_thresh).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
    keep = np.zeros(n, np.uint8)
    max_area = max_area_frac * core.size
    keep[1:] = (st[1:, cv2.CC_STAT_AREA] >= 4) & (st[1:, cv2.CC_STAT_AREA] <= max_area)
    m = keep[lab].astype(np.float32)
    # le JPEG ecrete en douceur autour des sources : on elargit un peu, puis on adoucit
    m = cv2.dilate(m, np.ones((5, 5), np.uint8))
    return np.clip(cv2.GaussianBlur(m, (0, 0), 2.0) * 1.5, 0, 1)


def expand_highlights(enc, lin, peak=40.0, shoulder=2.5, exposure=0.0):
    """Retourne (hdr_lineaire, masque_sources). `peak` = gain applique aux sources ecretees,
    `shoulder` = gain max sur les hautes lumieres non ecretees (murs blancs, reflets)."""
    em = emitter_mask(enc)
    y = luma(enc)
    g_shoulder = 1.0 + (shoulder - 1.0) * smoothstep(0.75, 1.0, y) ** 2
    g_emit = 1.0 + (peak - 1.0) * em
    gain = np.maximum(g_shoulder, g_emit) * 2.0 ** exposure
    return (lin * gain[..., None]).astype(np.float32), em


# ---------- 2. projection equirectangulaire ----------
def _rot(yaw, pitch, roll):
    cy, sy, cp, sp, cr, sr = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch), \
        np.cos(roll), np.sin(roll)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
    return ry @ rx @ rz


def project(img, hfov_deg, width, yaw=0.0, pitch=0.0, roll=0.0, feather_deg=4.0):
    """Place la photo (vue rectilineare) sur une sphere equirectangulaire width x width/2.

    yaw/pitch/roll en degres. Retourne (env, poids) ; poids = 1 dans la photo, 0 en dehors,
    avec un fondu de `feather_deg` sur les bords.
    """
    h, w = img.shape[:2]
    H = width // 2
    f_src = (w / 2) / np.tan(np.radians(hfov_deg) / 2)
    # on ramene la source a la densite de la sortie (moyenne = energie conservee, pas d'aliasing)
    px_per_deg_out = width / 360.0
    px_per_deg_src = w / hfov_deg
    if px_per_deg_out < px_per_deg_src:
        k = px_per_deg_out / px_per_deg_src
        img = cv2.resize(img, (max(2, int(w * k)), max(2, int(h * k))), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
        f_src = (w / 2) / np.tan(np.radians(hfov_deg) / 2)

    u = (np.arange(width, dtype=np.float64) + 0.5) / width
    v = (np.arange(H, dtype=np.float64) + 0.5) / H
    lon = (u * 2 - 1) * np.pi
    lat = (0.5 - v) * np.pi
    lon, lat = np.meshgrid(lon, lat)
    d = np.stack([np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon)], axis=-1)
    d = d @ _rot(np.radians(yaw), np.radians(pitch), np.radians(roll))   # monde -> camera
    z = d[..., 2]
    ok = z > 1e-6
    zs = np.where(ok, z, 1.0)
    mx = (w / 2 + f_src * d[..., 0] / zs).astype(np.float32)
    my = (h / 2 - f_src * d[..., 1] / zs).astype(np.float32)

    # fondu : distance au bord de la photo, en pixels source
    dist = np.minimum(np.minimum(mx, w - mx), np.minimum(my, h - my))
    feather_px = max(2.0, feather_deg * (w / hfov_deg))
    weight = (smoothstep(0.0, feather_px, dist) * ok).astype(np.float32)

    env = cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return env, weight


# ---------- 3. remplissage de la sphere hors photo ----------
def push_pull(img, conf):
    """Extension lisse : propage les couleurs connues (conf > 0) vers les zones inconnues."""
    levels = [(img * conf[..., None], conf.copy())]
    while min(levels[-1][1].shape) > 8:
        a, c = levels[-1]
        hh, ww = c.shape
        levels.append((cv2.resize(a, (ww // 2, hh // 2), interpolation=cv2.INTER_AREA),
                       cv2.resize(c, (ww // 2, hh // 2), interpolation=cv2.INTER_AREA)))
    a, c = levels[-1]
    res = a / np.maximum(c, 1e-6)[..., None]
    for a, c in reversed(levels[:-1]):
        up = cv2.resize(res, (c.shape[1], c.shape[0]), interpolation=cv2.INTER_LINEAR)
        conf_l = np.clip(c * 4.0, 0, 1)[..., None]       # c reste petit sur les bords (moyenne AREA)
        res = conf_l * (a / np.maximum(c, 1e-6)[..., None]) + (1 - conf_l) * up
    return res.astype(np.float32)


def row_prior(env, w, sigma_rows):
    """Hypothese 'studio a peu pres uniforme en azimut' : moyenne par latitude de ce que voit la
    photo, prolongee au-dessus / en dessous par la derniere ligne connue. Conserve l'energie
    moyenne des lampes meme hors champ (sans inventer leur position)."""
    H = env.shape[0]
    wsum = w.sum(axis=1)
    covered = wsum > 2.0
    if not covered.any():
        return np.zeros((H, 3), np.float32) + env.reshape(-1, 3).mean(axis=0)
    rows = (env * w[..., None]).sum(axis=1) / np.maximum(wsum, 1e-6)[:, None]
    idx = np.arange(H)
    prior = np.stack([np.interp(idx, idx[covered], rows[covered, c]) for c in range(3)], axis=1)
    return cv2.GaussianBlur(prior.astype(np.float32), (0, 0), sigma_rows,
                            borderType=cv2.BORDER_REPLICATE)


def build_env(ldr_lin, hdr_lin, hfov, width, yaw, pitch, roll, fill_gain=0.7, feather=4.0,
              fill_reach=12.0):
    """Photo HDR placee sur la sphere. Hors photo :
    - pres de la photo : extension lisse de la photo non etendue (continuite, pas de halo de lampe)
    - loin : moyenne par latitude (plafond lumineux en haut, sol en bas), HDR
    La transition se fait sur ~`fill_reach` degres. Le tout multiplie par `fill_gain`."""
    env_hdr, w = project(hdr_lin, hfov, width, yaw, pitch, roll, feather)
    env_ldr, _ = project(ldr_lin, hfov, width, yaw, pitch, roll, feather)
    near = push_pull(env_ldr, w)
    prior = row_prior(env_hdr, w, sigma_rows=width / 360.0 * 3)
    dist = cv2.distanceTransform((w < 0.5).astype(np.uint8), cv2.DIST_L2, 5)
    a = np.exp(-dist / (fill_reach * width / 360.0)).astype(np.float32)[..., None]
    fill = (a * near + (1 - a) * prior[:, None, :]) * fill_gain
    out = w[..., None] * env_hdr + (1 - w[..., None]) * fill
    return out.astype(np.float32), w


# ---------- 4. analyse ----------
def find_lights(env, n=3, frac=0.5):
    """Principales sources de l'env : centroide (azimut, elevation en degres) et part d'energie.

    L'energie est ponderee par l'angle solide (cos(lat)) car l'equirect sur-echantillonne les poles.
    """
    H, W = env.shape[:2]
    y = luma(env)
    lat = (0.5 - (np.arange(H) + 0.5) / H) * np.pi
    solid = np.cos(lat)[:, None].astype(np.float32)
    energy = y * solid
    mask = (y >= frac * y.max()).astype(np.uint8)
    cnt, lab, st, cen = cv2.connectedComponentsWithStats(mask, connectivity=8)
    total = energy.sum()
    blobs = []
    for i in range(1, cnt):
        sel = lab == i
        e = energy[sel].sum()
        cx, cy = cen[i]
        blobs.append((e / total, (cx / W * 2 - 1) * 180, (0.5 - cy / H) * 180, float(y[sel].max())))
    return sorted(blobs, reverse=True)[:n]
