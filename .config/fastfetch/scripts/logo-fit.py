#!/usr/bin/env python3
"""Fit a square GIF alongside a scene, using actual terminal cell dimensions."""
import fcntl
import json
import os
from pathlib import Path
import struct
import sys
import termios

path=Path(sys.argv[1]);text=path.read_text()
config=json.loads('\n'.join(x for x in text.splitlines() if not x.lstrip().startswith('//')))
logo=config.get('logo',{});width=logo.get('width',40)
# Authored vertical layouts keep their own image size and centering.
if logo.get('position')=='top':
    print(f'--logo-width\n{width}\n--logo-padding-top\n{logo.get("padding",{}).get("top",0)}\n--logo-position\ntop')
    sys.exit(0)
# The scene configurations carry layout information in an initial comment.
first=text.splitlines()[0]
if first.startswith('// layout:'):
    meta=json.loads(first.split(':',1)[1]);height=meta['rows'];content=meta['columns']
else:
    height=len(config.get('modules',[]));content=60
ratio=2.0
try:
    fd=os.open('/dev/tty',os.O_RDONLY | os.O_NOCTTY)
    try:
        rows,cols,xp,yp=struct.unpack('HHHH',fcntl.ioctl(fd,termios.TIOCGWINSZ,b'\0'*8))
    finally:
        os.close(fd)
    if cols and rows and xp and yp:ratio=(yp/rows)/(xp/cols)
except OSError:cols=0
if cols:
    available=cols-content-6
    if available>=22:
        width=min(round((height-2)*ratio),available)
        top=max(0,round((height-width/ratio)/2))
        position='left'
    else:
        width=min(width,max(16,cols-4),40);top=0;position='top'
else:
    top=logo.get('padding',{}).get('top',0);position=logo.get('position','auto')
print(f'--logo-width\n{width}\n--logo-padding-top\n{top}\n--logo-position\n{position}')
