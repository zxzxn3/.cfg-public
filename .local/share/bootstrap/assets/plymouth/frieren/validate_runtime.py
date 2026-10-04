import ctypes as C
from pathlib import Path
R=Path(__file__).resolve().parent
lib=C.CDLL('/usr/lib/plymouth/script.so');ply=C.CDLL('libply.so.5')
class Return(C.Structure):_fields_=[('type',C.c_int),('object',C.c_void_p)]
class State(C.Structure):_fields_=[('userdata',C.c_void_p),('global_',C.c_void_p),('local',C.c_void_p),('this',C.c_void_p)]
def fn(lib,name,ret,args):
 f=getattr(lib,name);f.restype=ret;f.argtypes=args;return f
ptr=C.c_void_p
state=fn(lib,'script_state_new',ptr,[ptr])(None)
fn(lib,'script_lib_math_setup',ptr,[ptr])(state)
fn(lib,'script_lib_string_setup',ptr,[ptr])(state)
fn(lib,'script_lib_image_setup',ptr,[ptr,C.c_char_p])(state,str(R/'theme').encode())
ls=fn(ply,'ply_list_new',ptr,[])();buf=fn(ply,'ply_buffer_new',ptr,[])()
fn(lib,'script_lib_sprite_setup',ptr,[ptr,ptr,ptr,C.c_char_p,C.c_uint32,C.c_uint32])(state,ls,buf,b'monospace',0xffffffff,0xff111111)
fn(lib,'script_lib_plymouth_setup',ptr,[ptr,C.c_int,C.c_int,ptr])(state,0,100,None)
parse=fn(lib,'script_parse_string',ptr,[C.c_char_p,C.c_char_p]);execute=fn(lib,'script_execute',Return,[ptr,ptr])
def run(s,label):
 op=parse(s.encode(),label.encode());assert op,'Parse failed: '+label
 ret=execute(state,op);assert ret.type!=2,'Execution failed: '+label
run('''Window.GetWidth = fun(i) { if (i == 0) return 1280; if (i == 1) return 1920; return NULL; };
Window.GetHeight = fun(i) { if (i == 0) return 800; if (i == 1) return 1080; return NULL; };''','virtual-displays')
run((R/'theme/frieren.script').read_text(),'frieren.script')
get=fn(lib,'script_obj_hash_get_number',C.c_double,[ptr,C.c_char_p])
global_=C.cast(state,C.POINTER(State)).contents.global_
assert get(global_,b'view_count')==2
run('''test_size0 = views[0].portrait.GetImage().GetWidth();
test_size1 = views[1].portrait.GetImage().GetWidth();
test_x0 = views[0].portrait.GetX();
test_x1 = views[1].portrait.GetX();
for (local.n = 0; local.n < 1191; local.n++) refresh();
password("Unlock disk", 5);
test_prompt_width = views[0].prompt.GetImage().GetWidth();
question("Test question", "example");
normal(); message("Test message"); hide_message(""); hotplug();''','callbacks')
for k,want in [('test_size0',800),('test_size1',1080),('test_x0',240),('test_x1',16804),('tick_ms',0),('frame_index',0)]:
 actual=get(global_,k.encode());assert actual==want,(k,actual,want)
assert get(global_,b'test_prompt_width')>0
print('PASS: installed Plymouth parser/interpreter; 2 displays; fit/centering; exact 1191 ticks per cycle; password/question/message/hotplug callbacks.')
