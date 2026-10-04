#!/usr/bin/env python3
"""Dense procedural diagrams; one consistent local snapshot per fastfetch process."""
import argparse
import datetime as dt
import fcntl
import getpass
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import secrets
import subprocess
import tempfile
import time
import unicodedata

PALETTE={'fg':'214;224;238','dim':'100;120;145','blue':'111;183;246','green':'116;209;174','gold':'237;191;117','pink':'218;144;196',
         'paper':'221;205;171','wood':'160;119;74','scrollgold':'204;168;105','faded':'155;143;119'}
SIZES={'circuit':(76,35),'metro':(78,36),'strata':(76,37),'radar':(78,35),'fabric':(78,38),'scroll':(80,25)}

def clean(value):
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', '', str(value))

def fit(text,n):
    text=clean(text)
    return text if len(text)<=n else text[:max(0,n-1)]+'…'

class Canvas:
    def __init__(self,w,h):
        self.w,self.h=w,h
        self.cells=[[(' ','fg') for _ in range(w)] for _ in range(h)]
        self.edges={}
    def put(self,x,y,text,color='fg'):
        for ch in clean(text):
            width=2 if unicodedata.east_asian_width(ch) in ('W','F') else 1
            if 0<=y<self.h and 0<=x and x+width<=self.w:
                self.cells[y][x]=(ch,color)
                if width==2:self.cells[y][x+1]=('',color)
            x+=width
    def wire(self,points,color='dim'):
        glyph={1:'│',2:'─',4:'│',8:'─',5:'│',10:'─',3:'└',6:'┌',12:'┐',9:'┘',7:'├',14:'┬',13:'┤',11:'┴',15:'┼'}
        for (x,y),(u,v) in zip(points,points[1:]):
            assert x==u or y==v
            dx=(u>x)-(u<x);dy=(v>y)-(v<y)
            while (x,y)!=(u,v):
                nx,ny=x+dx,y+dy
                a,b=(2,8) if dx==1 else (8,2) if dx==-1 else (4,1) if dy==1 else (1,4)
                for xx,yy,bits in [(x,y,a),(nx,ny,b)]:
                    if 0<=xx<self.w and 0<=yy<self.h:
                        self.edges[xx,yy]=self.edges.get((xx,yy),0)|bits
                        self.put(xx,yy,glyph[self.edges[xx,yy]],color)
                x,y=nx,ny
    def box(self,x,y,w,h,title,lines,color='blue'):
        # Clear interior before labeling so routes never run through values.
        for yy in range(y+1,y+h-1):self.put(x+1,yy,' '*(w-2))
        self.wire([(x,y),(x+w-1,y),(x+w-1,y+h-1),(x,y+h-1),(x,y)],color)
        self.put(x+2,y,' '+fit(title,w-6)+' ',color)
        for i,line in enumerate(lines[:h-2]):self.put(x+2,y+1+i,fit(line,w-4))
    def rows(self,ansi):
        rows=[]
        for row in self.cells:
            end=len(row)
            while end and row[end-1][0]==' ':end-=1
            text='';prev=None
            for ch,color in row[:end]:
                if ansi and color!=prev:text+='\033[38;2;'+PALETTE[color]+'m';prev=color
                text+=ch
            rows.append(text+('\033[0m' if ansi else ''))
        return rows

def gib(n):return f'{(n or 0)/2**30:.1f}G'
def bar(r,n=15):
    i=round(max(0,min(1,r))*n);return '━'*i+'·'*(n-i)
def snapshot():
    mods='OS:Host:Kernel:CPU:GPU:Memory:Swap:Disk:Display:WM:Shell:Terminal:Packages:Uptime:Battery'
    r=subprocess.run(['fastfetch','-c','none','--format','json','--structure',mods],capture_output=True,text=True,timeout=12,check=True)
    data={x['type'].lower():x.get('result') for x in json.loads(r.stdout)}
    def obj(k):return data.get(k) or {}
    def arr(k):return data.get(k) or []
    cpu=obj('cpu');mem=obj('memory');kern=obj('kernel');wm=obj('wm')
    disk=next((d for d in arr('disk') if d['mountpoint']=='/'),{})
    db=disk.get('bytes',{});sw=arr('swap');batt=arr('battery');freq=cpu.get('frequency',{})
    mtotal=mem.get('total',0);mused=mem.get('used',0);dtotal=db.get('total',0);dused=db.get('used',0)
    cores=cpu.get('cores',{});now=dt.datetime.now();up=obj('uptime').get('uptime',0)//1000
    displays=[]
    for d in arr('display'):
        out=d.get('output',{});scaled=d.get('scaled',{})
        displays.append([d.get('name','?'),f"{out.get('width','?')}x{out.get('height','?')} @ {out.get('refreshRate',0):g}Hz",f"logical {scaled.get('width','?')}x{scaled.get('height','?')}"])
    gpus=[]
    for g in arr('gpu'):
        gpus.append([g.get('name','?').replace('GeForce ','').replace(' Max-Q / Mobile',' Mobile'),g.get('type','?'),g.get('driver','?')])
    load=os.getloadavg();swapused=sum(x.get('used',0) for x in sw);swaptotal=sum(x.get('total',0) for x in sw)
    return dict(host=platform.node(),os=obj('os').get('prettyName','Linux'),kernel=kern.get('release','?'),arch=kern.get('architecture','?'),
        model=obj('host').get('name','?').replace('OMEN by HP Transcend ','OMEN '),cpu=cpu.get('cpu','?').replace('(R)','').replace('(TM)',''),
        cores=f"{cores.get('physical','?')}C / {cores.get('logical','?')}T",freq=f"{freq.get('base',0)/1000:g} / {freq.get('max',0)/1000:g} GHz base/max",
        march=cpu.get('march','?'),gpus=gpus,mem=f'{gib(mused)} / {gib(mtotal)}',mr=mused/max(1,mtotal),
        swap=f'{gib(swapused)} / {gib(swaptotal)}',sr=swapused/max(1,swaptotal),disk=f'{gib(dused)} / {gib(dtotal)}',dr=dused/max(1,dtotal),
        fs=disk.get('filesystem','?'),dev=disk.get('mountFrom','?'),free=gib(db.get('available',0)),
        wm=' '.join([wm.get('prettyName','?'),wm.get('version','')]).strip(),protocol=wm.get('protocolName','?'),
        shell=Path(os.environ.get('SHELL','unknown')).name,terminal=' '.join([obj('terminal').get('prettyName','?'),obj('terminal').get('version','')]).strip(),
        packages=obj('packages').get('all','?'),uptime=f'{up//86400}d {up%86400//3600:02}h {up%3600//60:02}m',
        load=' / '.join(f'{x:.2f}' for x in load),displays=displays,
        battery=(f"{batt[0].get('capacity',0):.0f}% / "+', '.join(batt[0].get('status',[]))) if batt else 'not present',
        date=now.strftime('%Y-%m-%d %H:%M:%S'),tasks=len([p for p in Path('/proc').iterdir() if p.name.isdigit()]))

def header(c,s,title,seed):
    c.put(1,0,title,'blue');c.put(1,1,f"{s['host']}  /  {s['os']}  /  {s['date']}",'dim')
    c.put(1,c.h-1,f"logical schematic  ·  seed {seed:08x}  ·  local snapshot",'dim')

def details(s):
    gpu=s['gpus'] or [['none detected','?','?']]
    displays=s['displays'] or [['none detected','?','?']]
    return gpu,displays

def circuit(c,s,rng,seed):
    header(c,s,'01 / CIRCUIT ATLAS                         SYSTEM INTERCONNECT',seed)
    c.box(1,3,74,5,'MACHINE / CONTROL PLANE',[s['model'],f"{s['kernel']}  [{s['arch']}]",f"UP {s['uptime']}    PACKAGES {s['packages']}    TASKS {s['tasks']}"],'gold')
    c.wire([(37,7),(37,9),(18,9),(18,11)],'blue');c.wire([(37,9),(56,9),(56,11)],'green')
    c.box(1,11,35,6,'PROCESSOR',[s['cpu'],s['cores']+'  '+s['march'],s['freq'],'load '+s['load']],'blue')
    c.box(40,11,35,6,'MEMORY CONTROLLER',[s['mem'],bar(s['mr'],20)+f" {s['mr']:.0%}",'swap '+s['swap'],'available '+f"{1-s['mr']:.0%}"],'green')
    c.wire([(18,16),(18,18),(69,18),(69,20)],'gold');c.wire([(56,16),(56,18)],'gold');c.wire([(37,18),(37,20)],'gold');c.wire([(10,18),(18,18)],'gold');c.wire([(10,18),(10,20)],'gold')
    gpu,displays=details(s)
    cards=[('GRAPHICS',[g[0] for g in gpu]+[gpu[0][2]],'pink'),('STORAGE',[s['dev'],s['fs']+'   '+s['disk'],'available '+s['free']],'gold'),('DISPLAY',[displays[0][0],displays[0][1],f'{len(s["displays"])} outputs'],'blue')]
    rng.shuffle(cards)
    for x,(title,lines,color) in zip([1,26,51],cards):c.box(x,20,24,6,title,lines,color)
    c.wire([(12,25),(12,27),(63,27),(63,25)],'dim');c.wire([(37,25),(37,29)],'dim')
    c.box(1,29,74,4,'USER SPACE',[f"{s['wm']} / {s['protocol']}     shell {s['shell']}     {s['terminal']}",f"POWER {s['battery']}     ROOT {s['dr']:.0%} used"],'green')

def metro(c,s,rng,seed):
    header(c,s,'02 / METROPOLIS                           SERVICE TRANSIT MAP',seed)
    # Parallel routes carry hardware, storage and desktop data; interchange columns are randomized.
    upper=rng.choice([37,38,39,40,41]);lower=rng.choice([49,50,51,52])
    for y,color in [(5,'blue'),(13,'green'),(21,'pink')]:c.wire([(3,y),(74,y)],color)
    c.wire([(upper,5),(upper,10),(lower,10),(lower,21)],'gold')
    for x in [1,76]:
        c.wire([(3 if x==1 else 74,5),(x,5),(x,21),(3 if x==1 else 74,21)],'dim')
    for x,y in [(upper,5),(lower,13),(lower,21)]:c.put(x,y,'◎','gold')
    c.put(3,3,'A / COMPUTE','blue');c.put(3,4,s['cpu'],'blue')
    c.put(3,6,'● CPU');c.put(3,7,s['cores']);c.put(3,8,s['freq'])
    c.put(44,6,'● KERNEL');c.put(44,7,s['kernel']);c.put(44,8,s['arch']+' / '+s['march'])
    c.put(3,11,'B / RESOURCES','green');c.put(3,12,'memory / storage / virtual memory','green')
    for x,title,rows in [(3,'● RAM',[s['mem'],bar(s['mr'],15)+f" {s['mr']:.0%}"]),(29,'● ROOT',[s['disk'],s['fs']+' '+f"{s['dr']:.0%}"]),(54,'● SWAP',[s['swap'],'free root '+s['free']])]:
        c.put(x,14,title);c.put(x,15,rows[0]);c.put(x,16,rows[1])
    gpu,displays=details(s)
    c.put(3,19,'C / VISUAL SESSION','pink');c.put(3,20,s['wm']+' / '+s['protocol'],'pink')
    c.put(3,22,'● GPU 0');c.put(3,23,fit(gpu[0][0],33));c.put(3,24,fit(gpu[0][2],33),'dim')
    c.put(42,22,'● DISPLAY');c.put(42,23,displays[0][0]+' '+displays[0][1]);c.put(42,24,f'{len(s["displays"])} outputs / {len(s["gpus"])} adapters','dim')
    c.box(1,27,37,6,'OPERATIONS',[f"uptime    {s['uptime']}",f"packages  {s['packages']}",f"tasks     {s['tasks']}",f"load      {s['load']}"],'gold')
    c.box(40,27,37,6,'TERMINUS',[s['terminal'],'shell '+s['shell'],s['battery'],displays[-1][0]+' '+displays[-1][1]],'pink')
    c.put(2,34,'● station  ◎ interchange    A compute / B resources / C session','dim')

def strata(c,s,rng,seed):
    header(c,s,'03 / STRATA                               MACHINE CROSS-SECTION',seed)
    gpu,displays=details(s)
    levels=[('SESSION',f"{s['wm']} / {s['protocol']}",f"{s['terminal']}  |  shell {s['shell']}",'pink'),
            ('SOFTWARE',f"{s['os']} / {s['arch']}",f"{s['packages']} packages  |  {s['tasks']} processes",'blue'),
            ('KERNEL',s['kernel'],f"uptime {s['uptime']}  |  load {s['load']}",'gold'),
            ('COMPUTE',s['cpu'],s['cores']+'  |  '+s['freq'],'blue'),
            ('MEMORY',s['mem']+'  '+bar(s['mr'],20)+f" {s['mr']:.0%}",'swap '+s['swap'],'green'),
            ('PERSISTENCE',s['dev']+'  /  '+s['fs'],s['disk']+'  '+bar(s['dr'],20)+f" {s['dr']:.0%}",'gold')]
    for i,(name,a,b,color) in enumerate(levels):
        y=3+i*4;indent=[0,2,4,2,0,2][i]
        x=1+indent;c.box(x,y,70-indent,4,f'{i:02} / {name}',[a,b],color)
        c.wire([(71,y+1),(74,y+1),(74,y+4)],'dim');c.put(72,y+2,'↓','dim')
    c.box(1,28,36,6,'GRAPHICS SIDECAR',[g[0] for g in gpu]+[gpu[0][2]],'pink')
    c.box(39,28,36,6,'OUTPUT / POWER',[displays[0][1],displays[-1][1],s['battery'],f'{len(s["displays"])} connected displays'],'green')
    c.put(2,35,'▲ interaction     ▼ abstraction     sidecar: visual pipeline','dim')

def radar(c,s,rng,seed):
    header(c,s,'04 / OBSERVATORY                          RADIAL SYSTEM INDEX',seed)
    # Concentric instrument rings with spokes at random bearings, never fake sensor readings.
    cx,cy=38,16
    for rx,ry in [(12,5),(22,9)]:
        for i in range(200):
            a=i*math.tau/200;c.put(round(cx+rx*math.cos(a)),round(cy+ry*math.sin(a)),'·','dim')
    for a in rng.sample(range(0,360,30),5):
        rad=math.radians(a)
        for t in range(4,20):c.put(round(cx+t*math.cos(rad)),round(cy+t*.4*math.sin(rad)),'·','blue')
    c.box(27,14,24,5,'SYSTEM CORE',[s['host'],s['cores'],s['uptime']],'gold')
    for points in [[(16,7),(24,7),(24,16),(27,16)],[(61,7),(53,7),(53,16),(50,16)],[(16,25),(24,25),(24,17)],[(61,25),(53,25),(53,17)]]:c.wire(points,'blue')
    c.box(1,3,30,6,'COMPUTE',[s['cpu'],s['freq'],'load '+s['load'],s['march']],'blue')
    c.box(47,3,30,6,'MEMORY',[s['mem'],bar(s['mr'],18)+f" {s['mr']:.0%}",'swap '+s['swap'],f"{s['tasks']} processes"],'green')
    c.box(1,23,30,6,'STORAGE',[s['dev'],s['fs']+' '+s['disk'],bar(s['dr'],18)+f" {s['dr']:.0%}",'available '+s['free']],'gold')
    gpu,displays=details(s)
    c.box(47,23,30,6,'VISUAL',[gpu[0][0],f'{len(s["gpus"])} GPU / {len(s["displays"])} screens',displays[0][1],s['protocol']],'pink')
    c.put(2,11,'KERNEL','blue');c.put(2,12,fit(s['kernel'],24));c.put(54,11,'SESSION','pink');c.put(54,12,fit(s['wm'],23))
    c.put(2,19,'PACKAGES '+str(s['packages']),'gold');c.put(54,19,'SHELL '+s['shell'],'green')
    c.put(2,31,s['terminal']+'  |  power '+s['battery']);c.put(2,32,s['model'],'dim')

def fabric(c,s,rng,seed):
    header(c,s,'05 / FABRIC                               PROCEDURAL NODE GRAPH',seed)
    gpu,displays=details(s)
    cards=[('CPU',[s['cpu'],s['cores'],s['freq']],'blue'),('RAM',[s['mem'],bar(s['mr'],15)+f" {s['mr']:.0%}",'swap '+s['swap']],'green'),
           ('ROOT',[s['dev'],s['fs']+' '+s['disk'],'available '+s['free']],'gold'),('GPU',[g[0] for g in gpu]+[gpu[0][2]],'pink'),
           ('SESSION',[s['wm'],s['protocol'],s['terminal']],'pink'),('DISPLAY',[displays[0][1],displays[-1][1],f'{len(s["displays"])} outputs'],'blue')]
    rng.shuffle(cards)
    c.box(20,3,38,5,'HOST / ROOT NODE',[s['host']+' / '+s['os'],s['kernel'],s['uptime']+' / '+str(s['packages'])+' pkgs'],'gold')
    c.wire([(38,7),(38,32)],'dim')
    for i,(title,lines,color) in enumerate(cards):
        left=i%2==0;x=1 if left else 44;y=10+(i//2)*8+rng.randrange(2)
        near=33 if left else 44;c.wire([(38,y+2),(near,y+2)],color)
        c.put(38,y+2,'◆',color)
        c.box(x,y,33,6,f'{i+1:02} / {title}',lines,color)
    c.box(1,33,76,4,'HEALTH / INVENTORY',[f"load {s['load']}   tasks {s['tasks']}   shell {s['shell']}",f"power {s['battery']}   arch {s['arch']}   root {s['dr']:.0%}"],'green')

def scroll(c,s,rng,seed):
    def centered(y,text,color='paper'):
        width=sum(2 if unicodedata.east_asian_width(ch) in ('W','F') else 1 for ch in text)
        c.put((42-width)//2,y,text,color)
    # A narrow, open sheet: curled left corners, no right edge or heavy rollers.
    c.put(3,0,'╭'+'─'*35+'╴','scrollgold')
    c.put(1,1,'╭─╯','scrollgold')
    for y in range(2,23):c.put(1,y,'│','scrollgold')
    centered(2,getpass.getuser()+'@'+s['host'],'scrollgold')
    gpu,_=details(s)
    hardware=[(' CPU',s['cpu']),
              ('󰾲 GPU',next((g[0] for g in gpu if g[1]=='Discrete'),'not detected')),
              ('󰾲 iGPU',next((g[0] for g in gpu if g[1]=='Integrated'),'not detected')),
              (' Memory',s['mem']+f"  ({s['mr']:.0%})"),
              (' Disk',s['disk']+f"  ({s['dr']:.0%})"),
              ('󰓡 Swap',s['swap']),(' Battery',s['battery'])]
    software=[(' OS',s['os']),(' Kernel',s['kernel']),(' WM',s['wm']),
              (' Shell',s['shell']),(' Terminal',s['terminal']),
              ('󰏖 Packages',str(s['packages'])),(' Uptime',s['uptime'])]
    for y,title,values in [(4,'Hardware',hardware),(13,'Software',software)]:
        c.put(5,y,'── '+title+' '+'─'*(24-len(title)),'scrollgold')
        for row,(label,value) in enumerate(values,y+1):
            c.put(5,row,label,'faded');c.put(19,row,'·','wood');c.put(22,row,value,'paper')
    centered(22,'─ · ❦ · ─','wood')
    c.put(1,23,'╰─╮','scrollgold')
    c.put(3,24,'╰'+'─'*29+' · ❦','scrollgold')

DRAW={'circuit':circuit,'metro':metro,'strata':strata,'radar':radar,'fabric':fabric,'scroll':scroll}

def render(name,seed=None):
    seed=secrets.randbits(32) if seed is None else seed
    c=Canvas(*SIZES[name]);DRAW[name](c,snapshot(),random.Random(seed),seed)
    return {'plain':c.rows(False),'ansi':c.rows(True)}

def ancestor_key():
    pid=os.getppid()
    for _ in range(10):
        try:
            stat=Path(f'/proc/{pid}/stat').read_text();tail=stat[stat.rfind(')')+2:].split()
            if Path(f'/proc/{pid}/comm').read_text().strip()=='fastfetch':return f'{pid}-{tail[19]}'
            pid=int(tail[1])
        except (OSError,ValueError,IndexError):break
    return None

def shared_rows(name):
    key=ancestor_key()
    if key is None:return render(name)
    root=Path(os.environ.get('XDG_RUNTIME_DIR',tempfile.gettempdir()))/f'fastfetch-diagrams-{os.getuid()}'
    root.mkdir(mode=0o700,exist_ok=True)
    st=root.lstat()
    if root.is_symlink() or st.st_uid!=os.getuid() or st.st_mode&0o077:raise RuntimeError('Unsafe runtime cache directory')
    # All row commands of one fastfetch invocation share one snapshot and random seed.
    with (root/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        file=root/f'{key}-{name}.json'
        if file.exists():return json.loads(file.read_text())
        rows=render(name);file.write_text(json.dumps(rows))
        for old in root.glob('*.json'):
            if time.time()-old.stat().st_mtime>3600:old.unlink(missing_ok=True)
        return rows

def main():
    p=argparse.ArgumentParser();p.add_argument('scene',choices=DRAW);p.add_argument('row',nargs='?',type=int);p.add_argument('--plain',action='store_true');p.add_argument('--seed',type=lambda s:int(s,0))
    args=p.parse_args()
    rows=(shared_rows(args.scene) if args.row is not None else render(args.scene,args.seed))['plain' if args.plain else 'ansi']
    print(rows[args.row] if args.row is not None else '\n'.join(rows))
if __name__=='__main__':main()
