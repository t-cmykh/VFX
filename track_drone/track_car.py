"""Suivi 2D de la voiture : template matching adaptatif (maj à chaque image) recalé sur le blob bleu foncé.
Sortie : (N,4) cx, cy, taille(px ~ longueur apparente), score. Usage: track_car.py frames_dir out.npy"""
import sys, glob, numpy as np, cv2
files = sorted(glob.glob(sys.argv[1]+"/*.png")); N=len(files)
def blue(im):
    hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV); m=cv2.inRange(hsv,(100,60,10),(135,255,160)); return cv2.morphologyEx(m,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
im=cv2.imread(files[0]); m=blue(im); n,lab,st,cen=cv2.connectedComponentsWithStats(m); k=1+np.argmax(st[1:,4])
c=cen[k].copy(); size=max(st[k,2],st[k,3]); out=np.zeros((N,4)); out[0]=[c[0],c[1],size,1]
g=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY); vel=np.zeros(2)
def crop(g,c,h): 
    x,y=int(round(c[0])),int(round(c[1])); return g[max(y-h,0):y+h+1, max(x-h,0):x+h+1], (max(x-h,0), max(y-h,0))
for i in range(1,N):
    im=cv2.imread(files[i]); g2=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
    h=int(max(size*0.7,8)); tpl,_=crop(g,c,h); pred=c+vel; S=int(max(size,14)*0.6)+8
    area,(ox,oy)=crop(g2,pred,h+S)
    if area.shape[0]>=tpl.shape[0] and area.shape[1]>=tpl.shape[1] and tpl.size>0:
        r=cv2.matchTemplate(area,tpl,cv2.TM_CCOEFF_NORMED); _,mx,_,loc=cv2.minMaxLoc(r)
        newc=np.array([ox+loc[0]+tpl.shape[1]/2-0.5, oy+loc[1]+tpl.shape[0]/2-0.5])
    else: newc=pred; mx=0
    # recale sur le blob bleu proche
    m=blue(im); x0,y0=int(newc[0]),int(newc[1]); r_=int(max(size,10)*0.9)
    sub=m[max(y0-r_,0):y0+r_+1, max(x0-r_,0):x0+r_+1]; ys,xs=np.nonzero(sub)
    if len(xs)>=max(6,0.12*(size**2)*0.2):
        bc=np.array([xs.mean()+max(x0-r_,0), ys.mean()+max(y0-r_,0)]); newc=0.4*newc+0.6*bc
        size=0.85*size+0.15*max(xs.max()-xs.min()+1, ys.max()-ys.min()+1)*1.1
    vel=0.7*vel+0.3*(newc-c); c=newc; out[i]=[c[0],c[1],size,mx]; g=g2
print("min score", out[:,3].min(), "taille debut/fin", out[0,2], out[-1,2])
np.save(sys.argv[2], out)
