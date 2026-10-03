"""Beton (img2pbr) + peinture jaune ecaillee -> cartes PBR finales + preview sphere lisse."""
import sys, numpy as np
from PIL import Image
from scipy import ndimage as ndi

src, out = sys.argv[1], sys.argv[2]            # dossier img2pbr, dossier sortie
N = 1024
def ld(n, mode=None):
    a = np.asarray(Image.open(f"{src}/agg_{n}.png"))
    return a
def srgb2lin(c): return np.where(c <= .04045, c/12.92, ((c+.055)/1.055)**2.4)
def lin2srgb(c): c = np.clip(c, 0, 1); return np.where(c <= .0031308, c*12.92, 1.055*c**(1/2.4)-.055)
def smooth(a, b, x): t = np.clip((x-a)/(b-a), 0, 1); return t*t*(3-2*t)
def tile_noise(seed, sigma):
    rng = np.random.default_rng(seed)
    n = ndi.gaussian_filter(rng.standard_normal((N, N)), sigma, mode="wrap")
    return (n-n.mean())/n.std()

diff = srgb2lin(ld("diffuse").astype(np.float32)[..., :3]/255)
gm = diff.mean(-1, keepdims=True); diff = (gm + (diff-gm)*0.55)*np.array([1.04, 1.0, 0.93])*0.85   # beton : moins sature/bleute, un peu plus sombre
height = ld("height").astype(np.float32); height = height if height.ndim == 2 else height[..., 0]; height /= 255
rough_c = ld("roughness").astype(np.float32); rough_c = rough_c if rough_c.ndim == 2 else rough_c[..., 0]; rough_c /= 255
ao = ld("ao").astype(np.float32); ao = ao if ao.ndim == 2 else ao[..., 0]; ao /= 255
nrm = ld("normal").astype(np.float32)[..., :3]/255*2-1

# masque de peinture : bruit grande echelle + relief du beton (la peinture saute sur les creux/bosses)
hp = (height-ndi.gaussian_filter(height, 12, mode="wrap")); hp = hp/hp.std()
field = 0.75*tile_noise(1, 55) + 0.35*tile_noise(2, 9) + 0.30*hp
paint = smooth(-0.95, -0.55, field)             # ~80 % couvert, bords irreguliers
paint_h = ndi.gaussian_filter(paint, 0.8, mode="wrap")

# couleur peinture (mesuree sur la photo) + variation
yel = np.array([0.82, 0.663, 0.02]); yel = srgb2lin(yel)
var = 1 + 0.10*tile_noise(3, 4)[..., None]*0.5 + 0.06*tile_noise(4, 40)[..., None]
paint_col = yel*var*(0.8+0.2*ao[..., None])
dirt = smooth(0.2, 1.2, tile_noise(5, 30))[..., None]*0.35
paint_col = paint_col*(1-0.55*dirt)

base = diff*paint_h[..., None]*0 + diff
base = diff*(1-paint_h[..., None]) + paint_col*paint_h[..., None]
rough = rough_c*(1-paint_h) + (0.55+0.1*tile_noise(6, 2))*paint_h
rough = np.clip(rough, .05, 1)
hfin = height*0.6 + paint_h*0.35                 # la peinture ajoute de l'epaisseur
gy, gx = np.gradient(ndi.gaussian_filter(paint_h, 1.2, mode="wrap"))
nxy = nrm[..., :2] + np.stack([-gx, -gy], -1)*6
nz = np.sqrt(np.clip(1-(nxy**2).sum(-1), 0.05, 1))
nfin = np.dstack([nxy, nz]); nfin /= np.linalg.norm(nfin, axis=2, keepdims=True)

def sv(name, a, mode=None): Image.fromarray(np.clip(a*255+.5, 0, 255).astype(np.uint8)).save(f"{out}/{name}.png")
sv("yellow_curb_basecolor", lin2srgb(base)); sv("yellow_curb_roughness", rough)
sv("yellow_curb_height", np.clip(hfin, 0, 1)); sv("yellow_curb_normal", nfin*.5+.5)
sv("yellow_curb_ao", ao); sv("yellow_curb_metallic", np.zeros((N, N))); sv("yellow_curb_paint_mask", paint_h)

# ---------- preview : sphere lisse, triplanar, GGX + eclairage studio ----------
R = 900; SS = 2; W = R*SS
y, x = np.mgrid[0:W, 0:W].astype(np.float32); x = (x/(W-1)*2-1)*1.05; y = -(y/(W-1)*2-1)*1.05
r2 = x*x+y*y; inside = r2 < 1; z = np.sqrt(np.clip(1-r2, 0, 1))
Ng = np.dstack([x, y, z])                       # normale geometrique (sphere lisse)
P = Ng*1.0
tile = 1.6                                       # repetitions de la texture sur la sphere
def samp(t, u, v):
    h, w = t.shape[:2]; uu = (u*tile % 1)*(w-1); vv = (v*tile % 1)*(h-1)
    ch = [ndi.map_coordinates(t[..., c], [vv, uu], order=1, mode="wrap") for c in range(t.shape[2])] if t.ndim == 3 else [ndi.map_coordinates(t, [vv, uu], order=1, mode="wrap")]
    return np.stack(ch, -1) if t.ndim == 3 else ch[0]
wts = np.abs(Ng)**6; wts /= wts.sum(-1, keepdims=True)+1e-9
planes = [(1, 2, 0), (0, 2, 1), (0, 1, 2)]       # (u,v,axe)
A = np.zeros((W, W, 3), np.float32); Rg = np.zeros((W, W), np.float32); Ao = np.zeros((W, W), np.float32); Nacc = np.zeros((W, W, 3), np.float32)
ntex = nfin.astype(np.float32)
for k, (iu, iv, ia) in enumerate(planes):
    u = P[..., iu]*0.5+0.5; v = P[..., iv]*0.5+0.5
    wk = wts[..., ia]
    A += samp(base.astype(np.float32), u, v)*wk[..., None]; Rg += samp(rough.astype(np.float32), u, v)*wk; Ao += samp(ao, u, v)*wk
    t = samp(ntex, u, v); tn = t.copy()
    s = np.sign(Ng[..., ia]); s[s == 0] = 1
    wn = np.zeros_like(Ng); wn[..., iu] = t[..., 0]; wn[..., iv] = t[..., 1]; wn[..., ia] = t[..., 2]*s
    Nacc += (Ng*0 + wn)*wk[..., None]*1.0
# whiteout simplifie : perturbation = Nacc - Ng*(Nacc.Ng) + Ng
pert = Nacc - Ng*(Nacc*Ng).sum(-1, keepdims=True)
N_ = Ng + pert*0.45; N_ /= np.linalg.norm(N_, axis=2, keepdims=True)+1e-9
V = np.array([0, 0, 1.0], np.float32)
def ggx(N, L, rough, F0):
    H = L+V; H = H/np.linalg.norm(H, axis=-1, keepdims=True); a = np.clip(rough, .04, 1)**2
    NH = np.clip((N*H).sum(-1), 0, 1); NL = np.clip((N*L).sum(-1), 0, 1); NV = np.clip(N[..., 2], 1e-3, 1); VH = np.clip((V*H).sum(-1), 0, 1)
    D = a**2/(np.pi*((NH**2)*(a**2-1)+1)**2+1e-9); k = (a+1)**2/8
    G = (NL/(NL*(1-k)+k+1e-9))*(NV/(NV*(1-k)+k+1e-9)); F = F0+(1-F0)*(1-VH)**5
    return D*G*F/(4*NL*NV+1e-9)*NL, NL
lights = [(np.array([-0.55, 0.65, 0.55]), np.array([4.2, 4.0, 3.8])),   # key
          (np.array([0.8, 0.1, 0.35]),  np.array([0.9, 1.0, 1.25])),    # fill froid
          (np.array([0.1, -0.5, -0.6]), np.array([0.45, 0.45, 0.5]))]    # rim
col = np.zeros((W, W, 3), np.float32); F0 = 0.04
for d, c in lights:
    L = (d/np.linalg.norm(d)).astype(np.float32)[None, None, :]*np.ones_like(Ng)
    spec, NL = ggx(N_, L, Rg, F0)
    col += (A/np.pi*NL[..., None] + spec[..., None])*c
# ambiance hemispherique + reflet d'env flou selon la rugosite
up = np.clip(N_[..., 1]*0.5+0.5, 0, 1)[..., None]
amb = (np.array([0.55, 0.62, 0.75])*up + np.array([0.28, 0.26, 0.24])*(1-up))*0.9
col += A*amb*Ao[..., None]**1.5
Rv = 2*N_[..., 2:3]*N_ - V; sky = np.clip(Rv[..., 1:2]*0.5+0.5, 0, 1)
env = (np.array([0.5, 0.6, 0.8])*sky+np.array([0.3, 0.28, 0.25])*(1-sky))*(1-Rg[..., None])**2.5
fres = 0.04+(1-0.04)*(1-np.clip(N_[..., 2:3], 0, 1))**5
col += env*fres*Ao[..., None]*1.5
col = col/(1+col*0.18)                          # leger tonemap
# fond degrade + sphere
bgv = np.linspace(0, 1, W)[:, None, None]; bg = (0.20*(1-bgv)+0.05*bgv)*np.ones((W, W, 3))*(1-0.5*np.clip(r2/2.2,0,1))[..., None]
img = np.where(inside[..., None], col, bg)
img = lin2srgb(img)
pil = Image.fromarray((img*255+.5).astype(np.uint8)).resize((R, R), Image.LANCZOS)
pil.save(f"{out}/yellow_curb_sphere_preview.png")
print("ok")
