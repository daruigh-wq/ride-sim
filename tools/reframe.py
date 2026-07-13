#!/usr/bin/env python3
"""reframe.py — turn a GoPro Max 2 .360 into a road-following pinhole clip that matches
ride_sim's overlay, plus a TCX route so the app can draw the centerline/cube on it.

Everything is pulled from the ONE .360: the video (its two equi-angular cube tracks) and the
telemetry (GPS9 route + CORI orientation + GRAV). No external route, no GoPro/Gyroflow GUI.

What it does, and why (see docs/video_reframe_roadmap.md for the full derivation):
  * de-EAC the .360 to equirect, then per-frame v360 flat -> a TRUE rectilinear pinhole that
    matches ride_sim `_project()` exactly (set the app's FOV to --fov, default 130).
  * VIEW HEADING = pure-CORI road-follow: applied = cori_head - lowpass(cori_head).  A local
    high-pass that keeps the slow road heading and removes bar weave. CORI (gyro-fused) is
    drift-free short-term; its yaw has no absolute fiducial so we never integrate it globally.
  * ROLL = GRAV de-roll (-atan2(gx,gy)); PITCH = 0 (camera is level in pitch per GRAV).
  * ROUTE = the .360's own GPS9 track, exported as TCX. Course-over-ground is SPEED-GATED
    (undefined when stationary) so the start ramp / final stop don't corrupt it.

Outputs:  OUT.mp4  (reframed clip)  +  OUT.tcx  (route for the app)
Load in ride_sim: pick OUT.mp4 as the video and OUT.tcx as the route, offset 0, FOV = --fov.

Usage:  reframe.py IN.360 [OUT.mp4] [--t0 S --dur S --fov 130 --camh 1.0 --validate]
"""
import sys, os, math, struct, subprocess, shutil, argparse, importlib.util
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
INV  = os.path.join(HERE, "gpmf_inventory.py")          # sibling tool (GPS9-aware walker)
NS_TCX = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"

ap = argparse.ArgumentParser()
ap.add_argument("src"); ap.add_argument("out", nargs="?", default="reframe_out.mp4")
ap.add_argument("--t0", type=float, default=0.0); ap.add_argument("--dur", type=float, default=None)
ap.add_argument("--fov", type=float, default=130.0); ap.add_argument("--camh", type=float, default=1.0)
ap.add_argument("--gpmd", default=None); ap.add_argument("--clipdur", type=float, default=None)
ap.add_argument("--tau-road", type=float, default=0.7); ap.add_argument("--speed-gate", type=float, default=1.5)
ap.add_argument("--sgn", type=float, default=1.0); ap.add_argument("--yaw-off", type=float, default=0.0)
ap.add_argument("--reuse-eqf", action="store_true"); ap.add_argument("--validate", action="store_true")
A = ap.parse_args()
FPS=30; W,H=1024,576; VFOV=round(A.fov*H/W,1); EQW,EQH=2880,1440; GAP,SIZE=5.0,1.0
BASE = os.path.splitext(A.out)[0]

def run(c): subprocess.run(c, check=True)

# ── telemetry: extract the gpmd stream if not supplied ──
gpmd = A.gpmd or BASE+"_gpmd.bin"
if not A.gpmd:
    run(["ffmpeg","-y","-v","error","-i",A.src,"-map","0:3","-c","copy","-f","data",gpmd])
b = open(gpmd,"rb").read()
CLIP = A.clipdur or float(subprocess.check_output(["ffprobe","-v","error","-show_entries",
    "format=duration","-of","default=noprint_wrappers=1:nokey=1",A.src]).strip())
T0 = A.t0; DUR = A.dur if A.dur else CLIP-T0

# ── GPS9 (compound 'lllllllSS': lat, lon, alt, 2Dspeed, ...) ──
def klv(pos): ss=b[pos+5]; rep=struct.unpack(">H",b[pos+6:pos+8])[0]; return ss,rep,b[pos+8:pos+8+ss*rep]
sc = struct.unpack(">9l", klv(b.find(b"SCAL", b.find(b"GPS9")-200))[2])
lat=[];lon=[];alt=[];spd=[]; p=0
while True:
    p=b.find(b"GPS9",p)
    if p<0: break
    ss,rep,pay=klv(p)
    for r in range(rep):
        f=struct.unpack(">7l",pay[r*32:r*32+28])
        lat.append(f[0]/sc[0]); lon.append(f[1]/sc[1]); alt.append(f[2]/sc[2]); spd.append(f[3]/sc[3])
    p+=4
lat=np.array(lat);lon=np.array(lon);alt=np.array(alt);spd=np.array(spd); tg=np.arange(len(lat))*CLIP/len(lat)
mpd_lat=111320.0; mpd_lon=111320.0*math.cos(math.radians(lat.mean()))
E=(lon-lon.mean())*mpd_lon; N=(lat-lat.mean())*mpd_lat
def lp(x,ts):
    n=max(1,int(round(ts*FPS))); n+=(n%2==0)
    return x if n<=1 else np.convolve(np.pad(x,n//2,mode="edge"),np.ones(n)/n,"valid")
tt=np.arange(0,CLIP,1.0/FPS)
Eg=np.interp(tt,tg,E); Ng=np.interp(tt,tg,N); Egs=lp(Eg,0.5); Ngs=lp(Ng,0.5)
distg=np.concatenate([[0],np.cumsum(np.hypot(np.diff(Eg),np.diff(Ng)))])

# ── CORI + GRAV (via the GPS9-aware inventory walker) ──
spec=importlib.util.spec_from_file_location("inv",INV); inv=importlib.util.module_from_spec(spec); spec.loader.exec_module(inv)
streams=inv.walk(b,0,len(b),0,{},[])
def grab(k):
    v=[];ax=None
    for s in streams:
        kk,typ,ssz,rep,pay=s["_data"]
        if kk==k: es=inv.TYPE_SIZE.get(typ,1);ax=max(1,ssz//es);v.extend(inv.decode(pay,typ,es,ax))
    return np.array(v,float).reshape(-1,ax)
cori=grab("CORI")/32767.0; cori/=np.linalg.norm(cori,axis=1,keepdims=True); tcq=np.arange(len(cori))*CLIP/len(cori)
grav=grab("GRAV"); grav/=np.linalg.norm(grav,axis=1,keepdims=True); tgv=np.arange(len(grav))*CLIP/len(grav)
def qconj(q): return np.array([q[0],-q[1],-q[2],-q[3]])
def qmul(a,c):
    w0,x0,y0,z0=a; w1,x1,y1,z1=c
    return np.array([w0*w1-x0*x1-y0*y1-z0*z1,w0*x1+x0*w1+y0*z1-z0*y1,w0*y1-x0*z1+y0*w1+z0*x1,w0*z1+x0*y1-y0*x1+z0*w1])
# continuous CORI heading: integrate INCREMENTAL inter-sample yaw (the clip-start rotation
# vector folds past +-180 over a long ride) and cumsum.
dhead=np.zeros(len(cori))
for i in range(1,len(cori)):
    qd=qmul(qconj(cori[i-1]),cori[i]); qd=qd if qd[0]>=0 else -qd
    v=qd[1:]; n=np.linalg.norm(v); dhead[i]=np.degrees(2*np.arctan2(n,qd[0])*(v[1]/n)) if n>1e-9 else 0.0
rvy=np.interp(tt,tcq,np.cumsum(dhead))
def roll_at(t):
    g=grav[min(len(grav)-1,np.searchsorted(tgv,t))]; return -np.rad2deg(np.arctan2(g[0],g[1]))

# ── heading: pure-CORI road-follow (local, drift-immune) ──
applied_full = rvy - lp(rvy, A.tau_road)
# GPS bearing for the overlay sidecar (distance-based, SPEED-GATED)
def bearing_at_s(s):
    a=int(np.interp(max(0,s-2),distg,np.arange(len(distg))))
    c2=int(np.interp(min(distg[-1],s+2),distg,np.arange(len(distg))))
    return math.atan2(Egs[c2]-Egs[a], Ngs[c2]-Ngs[a])
gps_brg=np.unwrap(np.array([bearing_at_s(distg[i]) for i in range(len(tt))]))
spd_grid=np.interp(tt,tg,spd); mv=spd_grid>A.speed_gate
if mv.any(): gps_brg=np.interp(np.arange(len(tt)),np.where(mv)[0],gps_brg[mv])
overlay_head=gps_brg + A.sgn*np.deg2rad(applied_full) + math.radians(A.yaw_off)
print(f"clip {CLIP:.1f}s  GPS {len(lat)}@{len(lat)/CLIP:.0f}Hz {distg[-1]:.0f}m mean {spd.mean():.2f}m/s  "
      f"applied {applied_full.min():+.1f}..{applied_full.max():+.1f} deg")

# ── TCX route for the window [T0, T0+DUR] (times & distance relative to window start) ──
def write_tcx(path):
    m=(tg>=T0)&(tg<=T0+DUR)
    if m.sum()<10: raise SystemExit("too few GPS points in window for a TCX")
    distraw=np.concatenate([[0],np.cumsum(np.hypot(np.diff(E),np.diff(N)))])
    d0=float(np.interp(T0,tg,distraw)); base=datetime(2000,1,1,tzinfo=timezone.utc)
    ET.register_namespace("",NS_TCX)
    root=ET.Element(f"{{{NS_TCX}}}TrainingCenterDatabase")
    acts=ET.SubElement(root,f"{{{NS_TCX}}}Activities"); act=ET.SubElement(acts,f"{{{NS_TCX}}}Activity",Sport="Biking")
    ET.SubElement(act,f"{{{NS_TCX}}}Id").text=base.isoformat().replace("+00:00","Z")
    lap=ET.SubElement(act,f"{{{NS_TCX}}}Lap"); track=ET.SubElement(lap,f"{{{NS_TCX}}}Track")
    for i in np.where(m)[0]:
        tp=ET.SubElement(track,f"{{{NS_TCX}}}Trackpoint")
        ET.SubElement(tp,f"{{{NS_TCX}}}Time").text=(base+timedelta(seconds=float(tg[i]-T0))).isoformat().replace("+00:00","Z")
        pos=ET.SubElement(tp,f"{{{NS_TCX}}}Position")
        ET.SubElement(pos,f"{{{NS_TCX}}}LatitudeDegrees").text=f"{lat[i]:.7f}"
        ET.SubElement(pos,f"{{{NS_TCX}}}LongitudeDegrees").text=f"{lon[i]:.7f}"
        ET.SubElement(tp,f"{{{NS_TCX}}}AltitudeMeters").text=f"{alt[i]:.1f}"
        ET.SubElement(tp,f"{{{NS_TCX}}}DistanceMeters").text=f"{float(distraw[i])-d0:.1f}"
    ET.ElementTree(root).write(path,xml_declaration=True,encoding="UTF-8")
tcx=BASE+".tcx"; write_tcx(tcx)

# ── reframe the window ──
nfr=int(DUR*FPS); f0=int(T0*FPS)
if not (A.reuse_eqf and os.path.isdir("eqf")):
    shutil.rmtree("eqf",ignore_errors=True); os.makedirs("eqf")
    run(["ffmpeg","-y","-v","error","-an","-ss",str(T0),"-i",A.src,"-t",str(DUR),
         "-filter_complex",f"[0:0][0:4]vstack,v360=eac:e:w={EQW}:h={EQH}","-r",str(FPS),BASE+"_eq.mp4"])
    run(["ffmpeg","-y","-v","error","-i",BASE+"_eq.mp4","eqf/%04d.png"])
frames=sorted(os.listdir("eqf"))[:nfr]; nfr=len(frames)
shutil.rmtree("of",ignore_errors=True); os.makedirs("of"); procs=[]
for i,fn in enumerate(frames):
    gi=f0+i
    vf=f"v360=e:flat:w={W}:h={H}:h_fov={A.fov}:v_fov={VFOV}:yaw={applied_full[gi]:.3f}:pitch=0:roll={roll_at(tt[gi]):.3f}"
    procs.append(subprocess.Popen(["ffmpeg","-y","-v","error","-i",f"eqf/{fn}","-vf",vf,f"of/{i:04d}.png"]))
    if len(procs)>=8:
        for pr in procs: pr.wait()
        procs=[]
for pr in procs: pr.wait()
run(["ffmpeg","-y","-v","error","-framerate",str(FPS),"-i","of/%04d.png","-c:v","libx264","-pix_fmt","yuv420p",A.out])

np.savez(BASE+".route.npz", t=tt, E=Eg, N=Ng, dist=distg, gps_brg=gps_brg,
         overlay_head=overlay_head, applied=applied_full, camh=A.camh, fov=A.fov)
print(f"\nwrote {A.out}\n      {tcx}\n      {BASE}.route.npz")
print(f"→ In ride_sim: video = {os.path.basename(A.out)}, route = {os.path.basename(tcx)}, "
      f"offset 0, FOV {A.fov:.0f}, camera height {A.camh:.2f} m")

# ── optional QC overlay montage ──
if A.validate:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt, matplotlib.image as mpimg
    def s_at(t): return float(np.interp(t,tt,distg))
    def en(s): return float(np.interp(s,distg,Eg)), float(np.interp(s,distg,Ng))
    def project(x,y,z):
        if z>=-0.01: return None
        f=(W/2.0)/math.tan(math.radians(A.fov)/2.0); return (W/2.0+f*(x/-z), H/2.0-f*(y/-z))
    def overlay(gi):
        ti=tt[gi]; s_r=s_at(ti); head=float(overlay_head[gi]); e_r,n_r=en(s_r)
        def wc(d):
            e,n=en(s_r+d); dee=e-e_r; dnn=n-n_r
            return project(dee*math.cos(head)-dnn*math.sin(head),-A.camh,-(dee*math.sin(head)+dnn*math.cos(head)))
        cen=[]; d=2.0
        while d<=45:
            a=wc(d); bb=wc(d+0.3)
            if a and bb: cen.append((a,bb))
            d+=0.6
        cy=-A.camh+SIZE/2; sh=SIZE/2; e5,n5=en(s_r+GAP); de5=e5-e_r; dn5=n5-n_r
        fwd=de5*math.sin(head)+dn5*math.cos(head); rgt=de5*math.cos(head)-dn5*math.sin(head)
        vv=[(rgt+dx,cy+dy,-fwd+dz) for dx in(-sh,sh) for dy in(-sh,sh) for dz in(-sh,sh)]
        ed=[(0,1),(0,2),(0,4),(1,3),(1,5),(2,3),(2,6),(3,7),(4,5),(4,6),(5,7),(6,7)]
        P=[project(*v) for v in vv]; cub=[(P[a],P[bb]) for a,bb in ed if P[a] and P[bb]]
        return cen,cub
    cols=[int(round(x)) for x in np.linspace(0,nfr-1,5)]
    fig,axes=plt.subplots(1,5,figsize=(20,2.6))
    for ax,ci in zip(axes,cols):
        ax.imshow(mpimg.imread(f"of/{ci:04d}.png")); ax.set_xlim(0,W); ax.set_ylim(H,0); ax.axis("off")
        cen,cub=overlay(f0+ci)
        for a,bb in cen: ax.plot([a[0],bb[0]],[a[1],bb[1]],color="red",lw=2)
        for a,bb in cub: ax.plot([a[0],bb[0]],[a[1],bb[1]],color="cyan",lw=1.5)
        ax.set_title(f"t={ci/FPS:.1f}s",fontsize=7)
    plt.tight_layout(pad=0.2); plt.savefig(BASE+"_validate.png",dpi=115,bbox_inches="tight")
    print("wrote",BASE+"_validate.png")
