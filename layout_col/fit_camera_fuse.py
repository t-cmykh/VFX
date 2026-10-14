import numpy as np, json, sys
from scipy.optimize import minimize
from scipy.signal import savgol_filter
import fit as F
w=float(sys.argv[1])
fit=json.load(open("vid/fit.json")); R=fit["R"]
trk=np.array(json.load(open("vid/fit_track2.json"))["cams"]); N=len(trk)
masks=[F.road_mask(n) for n in range(1,N+1)]
sc=np.array([0.5,0.5,0.3,0.003,0.003,1.0])
cams=[trk[0].copy()]
for i in range(1,N):
    pred=cams[-1]+(trk[i]-trk[i-1])
    fun=lambda c: 60*F.loss(R,c[:5],c[5],masks[i])+w*np.sum(((c-pred)/sc)**2)
    r=minimize(fun,pred,method="Nelder-Mead",options=dict(maxiter=300,xatol=1e-4,fatol=1e-6))
    cams.append(r.x)
cams=np.array(cams); sm=savgol_filter(cams,11,2,axis=0)
ious=[1-F.loss(R,sm[i,:5],sm[i,5],masks[i]) for i in range(N)]
print("w",w,"IoU moy/min",round(np.mean(ious),3),round(np.min(ious),3))
fit["cams"]=sm.tolist(); json.dump(fit,open(f"vid/fit_fuse_{w}.json","w"))
