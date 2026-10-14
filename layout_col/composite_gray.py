import cv2, numpy as np, sys
def comp(bg_path, fg_path, out):
    bg=cv2.imread(bg_path).astype(np.float32)
    fg=cv2.imread(fg_path, cv2.IMREAD_UNCHANGED).astype(np.float32)
    a=fg[...,3:4]/255.0; rgb=fg[...,:3]
    g=(0.114*rgb[...,0]+0.587*rgb[...,1]+0.299*rgb[...,2])[...,None]   # BGR -> gris
    g=np.repeat(g,3,axis=2)
    res=bg*(1-a)+g*a
    cv2.imwrite(out,res.clip(0,255).astype(np.uint8))
if __name__=="__main__":
    for n,f in ((1,1),(57,57),(113,113)):
        comp(f"vid/full/{n:03d}.png",f"out/c{f}.png",f"out/comp{n}.png")
