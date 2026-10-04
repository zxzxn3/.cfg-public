#!/usr/bin/env python3
"""Procedural fastfetch scenes. Standard library only; read-only local metrics."""
import datetime as dt
import hashlib
import math
import os
from pathlib import Path
import platform
import random
import shutil
import sys

W, H = 57, 25
COLORS = {'dim': '106;119;143', 'ink': '219;226;235', 'blue': '127;178;240',
          'gold': '227;191;126', 'green': '128;209;173', 'rose': '220;150;186'}

class Canvas:
    def __init__(self):
        self.grid = [[(' ', 'ink') for _ in range(W)] for _ in range(H)]

    def text(self, x, y, text, color='ink'):
        if 0 <= y < H:
            for i, ch in enumerate(str(text)):
                if 0 <= x+i < W:
                    self.grid[y][x+i] = (ch, color)

    def line(self, x1, y1, x2, y2, color='dim', char='·'):
        n = max(abs(x2-x1), abs(y2-y1), 1)
        for i in range(n+1):
            self.text(round(x1+(x2-x1)*i/n), round(y1+(y2-y1)*i/n), char, color)

    def ring(self, cx, cy, rx, ry, color='dim', char='·'):
        for i in range(180):
            a = i*math.tau/180
            self.text(round(cx+rx*math.cos(a)), round(cy+ry*math.sin(a)), char, color)

    def rows(self, ansi=True):
        out=[]
        for row in self.grid:
            text=''; prev=None
            for ch,color in row:
                if ansi and color != prev:
                    text += '\033[38;2;'+COLORS[color]+'m'; prev=color
                text += ch
            out.append(text.rstrip() + ('\033[0m' if ansi else ''))
        return out

def metrics():
    mem={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key,value,*_=line.split();mem[key.rstrip(':')]=int(value)
    total=mem['MemTotal'];used=total-mem['MemAvailable']
    disk=shutil.disk_usage('/')
    uptime=float(Path('/proc/uptime').read_text().split()[0])
    load=os.getloadavg()[0]; cores=os.cpu_count() or 1
    name=platform.node()
    osname=platform.freedesktop_os_release().get('PRETTY_NAME','Linux')
    return dict(mem=used/total, ram=f'{used/1048576:.1f}/{total/1048576:.1f} GiB',
                disk=disk.used/disk.total, storage=f'{disk.used/2**30:.1f}/{disk.total/2**30:.1f} GiB',
                up=f'{int(uptime//3600)}h {int(uptime%3600//60):02d}m',
                load=load, cores=cores, host=name, os=osname,
                seed=int.from_bytes(hashlib.sha256(name.encode()).digest()[:4]), now=dt.datetime.now())

def potion(c,s):
    c.text(2,0,'A L C H E M Y  /  volatile memory','rose')
    c.text(9,3,'╭────────╮','gold'); c.text(9,4,'│ ▒▒▒▒▒▒ │','gold')
    c.text(9,5,'╰─┐    ┌─╯','gold')
    walls=[(11,16),(11,16),(10,17),(8,19),(6,21),(5,22),(4,23),(4,23),(4,23),(4,23),(5,22),(6,21)]
    filled=round(s['mem']*8)
    for i,(l,r) in enumerate(walls):
        y=i+6;c.text(l,y,'╱' if i in (2,3,4,5,6) else '│','rose');c.text(r,y,'╲' if i in (2,3,4,5,6) else '│','rose')
        if y >= 18-filled:
            c.text(l+1,y,('≈' if y==18-filled else '░')*(r-l-1),'blue')
            if y>18-filled and y%2==0:c.text(l+3,y,'°  ·   °','blue')
    c.text(6,18,'╰──────────────╯','rose')
    c.text(29,6,'MANA RESERVOIR','blue');c.text(29,8,f"{s['mem']:.0%} occupied",'ink');c.text(29,9,s['ram'],'dim')
    c.text(29,12,'ROOT INVENTORY','gold');c.text(29,14,f"{s['disk']:.0%} occupied");c.text(29,15,s['storage'],'dim')
    c.text(3,21,'Liquid height = memory in use','dim')
    c.text(3,23,f"{s['os']}  /  awake {s['up']}",'gold')

def atlas(c,s):
    c.text(2,0,'D U N G E O N  /  machine cartography','gold')
    rng=random.Random(s['seed']); rooms=[]
    labels=[('BOOT',s['up']),('MEM',f"{s['mem']:.0%}"),('ROOT',f"{s['disk']:.0%}"),('CPU',f"{s['cores']}T"),('LOAD',f"{s['load']:.2f}"),('HOME',s['host'][:9])]
    for i in range(6):
        x=2+(i%3)*18;y=4+(i//3)*10+rng.randrange(2);rooms.append((x,y))
    for a,b in [(0,1),(1,2),(0,3),(3,4),(4,5),(2,5)]:
        x,y=rooms[a];u,v=rooms[b]
        c.line(x+6,y+2,u+6,y+2,char='▪');c.line(u+6,y+2,u+6,v+2,char='▪')
    for (x,y),(label,value) in zip(rooms,labels):
        c.text(x,y,'╭───────────╮','green');c.text(x,y+1,'│           │','green');c.text(x,y+2,'│           │','green');c.text(x,y+3,'╰───────────╯','green')
        c.text(x+2,y+1,label,'gold');c.text(x+2,y+2,value)
    c.text(3,21,'@ Frieren     ▪ passage     HOME = destination','dim')
    c.text(3,23,f"World seed {s['seed']:08X} / {s['os']}",'gold')
    x,y=rooms[0];c.text(x+10,y+1,'@','rose')

def orbit(c,s):
    c.text(2,0,'O R R E R Y  /  local time','blue')
    cx,cy=26,11;c.ring(cx,cy,19,8);c.ring(cx,cy,13,6,'dim')
    for n in range(12):
        a=n*math.tau/12-math.pi/2
        c.text(round(cx+19*math.cos(a)),round(cy+8*math.sin(a)),str(n or 12),'gold')
    now=s['now']
    for value,period,rx,ry,color,char in [(now.hour%12+now.minute/60,12,10,4,'gold','━'),(now.minute+now.second/60,60,16,7,'blue','·')]:
        a=value/period*math.tau-math.pi/2;c.line(cx,cy,round(cx+rx*math.cos(a)),round(cy+ry*math.sin(a)),color,char)
    c.text(cx,cy,'✦','rose')
    c.text(3,21,now.strftime('%Y.%m.%d    %H:%M:%S')+'   /   '+now.astimezone().tzname(),'ink')
    c.text(3,23,f"UP {s['up']}   RAM {s['mem']:.0%}   ROOT {s['disk']:.0%}",'blue')

def grove(c,s):
    c.text(2,0,'G R O V E  /  a living machine','green')
    rng=random.Random(s['seed'])
    # Load average per logical CPU controls the crown size; RAM controls foliage color.
    depth=3+min(2,int(s['load']/s['cores']*5))
    leaf='green' if s['mem']<.6 else 'gold' if s['mem']<.85 else 'rose'
    def branch(x,y,length,dx,d):
        nx=max(3,min(52,round(x+dx*length)));ny=max(3,round(y-length*.48))
        c.line(round(x),round(y),nx,ny,'gold','│' if dx==0 else '╱' if dx<0 else '╲')
        if d:
            branch(nx,ny,length*.68,-1-rng.random()*.6,d-1)
            branch(nx,ny,length*.68,1+rng.random()*.6,d-1)
        else:c.text(nx-1,ny,'♣♧',''+leaf)
    branch(27,18,9,0,depth)
    c.text(24,18,'╱ │ ╲','gold');c.text(9,19,'───────┴─────────┴─────────┴────────','dim')
    c.text(3,21,f"CROWN  load {s['load']:.2f} / {s['cores']} threads",'green')
    c.text(3,22,f"LEAVES memory {s['mem']:.0%}   ROOT disk {s['disk']:.0%}",leaf)
    c.text(3,24,'Growth follows load; leaf color follows memory.','dim')

def stars(c,s):
    c.text(2,0,'C O N S T E L L A T I O N  /  '+s['host'],'blue')
    rng=random.Random(s['seed'])
    for _ in range(45):c.text(rng.randrange(1,56),rng.randrange(2,20),'·','dim')
    nodes=[(7,6),(24,3),(45,7),(34,13),(10,17)]
    for a,b in [(0,1),(1,2),(2,3),(3,4),(4,0),(0,3)]:c.line(*nodes[a],*nodes[b],color='blue')
    labels=[('KERNEL',platform.release().split('-')[0]),('CPU',f"{s['cores']} threads"),('MEM',f"{s['mem']:.0%}"),('ROOT',f"{s['disk']:.0%}"),('UP',s['up'])]
    for (x,y),(name,val) in zip(nodes,labels):
        c.text(x,y,'✦','gold');c.text(x-2,y-1,name,'gold');c.text(x+2,y+1,val)
    c.text(3,22,s['os']+' / '+s['host'],'ink')
    c.text(3,24,'Each point is a piece of this little world.','dim')

SCENES={'potion':potion,'dungeon':atlas,'orrery':orbit,'grove':grove,'constellation':stars}

def main():
    scene=sys.argv[1];c=Canvas();SCENES[scene](c,metrics())
    rows=c.rows('--plain' not in sys.argv)
    if len(sys.argv)>2 and sys.argv[2].isdigit():print(rows[int(sys.argv[2])])
    else:print('\n'.join(rows))

if __name__=='__main__':main()
