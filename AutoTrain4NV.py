"""Auto Train Mil và Cổ Mộ — 4 nhân vật, cấu hình trực tiếp, không kích hoạt.
Chỉ Windows; đọc ảnh cửa sổ đang hiển thị; không đọc bộ nhớ game.
"""
import ctypes
import atexit
from ctypes import wintypes
import json
import random
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageGrab, ImageTk
import numpy as np

if sys.platform != 'win32':
    raise SystemExit('Ứng dụng này chỉ hỗ trợ Windows 10/11.')

u = ctypes.windll.user32
u.SetProcessDPIAware()
WM_KEYDOWN, WM_KEYUP, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x100, 0x101, 0x201, 0x202
VK = {'Tab': 0x09, 'Space': 0x20, **{str(i): 0x30+i for i in range(10)}}
ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
CONFIG = Path.home() / 'AutoTrainMil4NV_config.json'

class RECT(ctypes.Structure):
    _fields_ = [('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]

class POINT(ctypes.Structure):
    _fields_ = [('x',ctypes.c_long),('y',ctypes.c_long)]

ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
u.EnumWindows.argtypes = [ENUMPROC, wintypes.LPARAM]
u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
u.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
u.PostMessageW.restype = wintypes.BOOL

def windows():
    out = []
    def cb(hwnd, unused):
        if not u.IsWindowVisible(hwnd): return True
        n = u.GetWindowTextLengthW(hwnd)
        if not n: return True
        buf = ctypes.create_unicode_buffer(n+1)
        u.GetWindowTextW(hwnd, buf, n+1)
        name = buf.value.strip()
        if name and 'Auto Train Mil' not in name:
            out.append((f'{name[:42]} [HWND {int(hwnd)}]', int(hwnd)))
        return True
    callback = ENUMPROC(cb)
    u.EnumWindows(callback, 0)
    return out

def client_box(hwnd):
    r=RECT()
    if not u.IsWindow(hwnd) or not u.GetClientRect(hwnd, ctypes.byref(r)): return None
    p=POINT(0,0)
    if not u.ClientToScreen(hwnd, ctypes.byref(p)): return None
    w,h=r.right-r.left,r.bottom-r.top
    if w<150 or h<150: return None
    return (p.x,p.y,p.x+w,p.y+h)

def screenshot(hwnd):
    box=client_box(hwnd)
    if box is None or u.IsIconic(hwnd): return None
    try: return ImageGrab.grab(bbox=box, all_screens=True).convert('RGB')
    except Exception: return None

def key(hwnd, v):
    if not u.IsWindow(hwnd): return False
    # Lparam contains a basic scan code; some games ignore background messages.
    scan = u.MapVirtualKeyW(v,0)
    return bool(u.PostMessageW(hwnd,WM_KEYDOWN,v,1 | (scan<<16)) and
                u.PostMessageW(hwnd,WM_KEYUP,v,0xC0000001 | (scan<<16)))

def click(hwnd, x, y):
    box=client_box(hwnd)
    if not box: return False
    w,h=box[2]-box[0],box[3]-box[1]
    x=max(1,min(w-2,int(x))); y=max(1,min(h-2,int(y)))
    lp=(y<<16)|(x & 0xffff)
    return bool(u.PostMessageW(hwnd,WM_LBUTTONDOWN,1,lp) and u.PostMessageW(hwnd,WM_LBUTTONUP,0,lp))


# Single-instance Windows mutex. Only the first process keeps running.
MUTEX_NAME = 'Local\\AutoTrainMilCoMo_4NV_SingleInstance_v1'
kernel32 = ctypes.windll.kernel32
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetLastError.restype = wintypes.DWORD
mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
if not mutex_handle:
    raise SystemExit('Không thể tạo khóa chạy một phiên bản.')
if kernel32.GetLastError() == 183:
    ctypes.windll.user32.MessageBoxW(None, 'Auto đang chạy trên máy này. Không thể mở thêm bản thứ hai!', 'Auto Train 4 nhân vật', 0x30)
    kernel32.CloseHandle(mutex_handle)
    raise SystemExit(0)
atexit.register(lambda: kernel32.CloseHandle(mutex_handle))

POINTS={'A':(112/864,145/648),'B':(778/864,139/648),'C':(138/864,527/648),
        'D':(766/864,536/648),'E':(457/864,540/648),'F':(432/864,135/648),
        'G':(112/864,351/648),'H':(785/864,351/648)}
PATTERNS=['BB DD CC AA','DD CC DD CC','EE DD BB HH','AA BB DD CC',
          'HH DD EE CC','GG AA FF BB','BB HH DD EE','CC GG AA FF',
          'FF BB HH DD','EE CC GG AA','DD EE CC GG','AA FF BB HH',
          'HH BB FF AA','CC EE DD HH','GG CC EE DD','BB DD EE CC',
          'FF AA GG CC','DD HH BB FF','EE GG CC AA','AA CC DD BB']
SKILLS=[f'Skill {i}' for i in range(1,6)]
TIMED=SKILLS+['Bay chỗ khác (8)','Bơm Máu (9)','Bơm Mana (0)']

class Character:
    def __init__(self, app, index, parent, saved):
        self.app=app; self.index=index; self.running=threading.Event(); self.thread=None
        self.hwnds={}; self.checks={}; self.intervals={}
        # Toàn bộ tùy chọn nằm ngay trong ô nhân vật, không mở cửa sổ phụ.
        self.frame=tk.LabelFrame(parent,text=f'NHÂN VẬT {index}',font=('Segoe UI',8,'bold'),
                                 bg='white',fg='#075bb7',padx=3,pady=1,bd=2,relief='groove',highlightthickness=2,highlightbackground='#00a72c')
        self.frame.grid(row=(index-1)//2,column=(index-1)%2,sticky='nsew',padx=2,pady=1)
        # Nút BẬT/TẮT AUTO luôn ở vị trí đầu tiên trong mỗi ô nhân vật.
        self.button=tk.Button(self.frame,text='▶ BẬT AUTO',command=self.toggle,bg='#078d28',fg='white',
                              activebackground='#0b9e34',activeforeground='white',font=('Segoe UI',9,'bold'),height=1)
        self.button.pack(fill='x',pady=(0,2))
        self.window=tk.StringVar(value=saved.get('window',''))
        self.combo=ttk.Combobox(self.frame,textvariable=self.window,state='readonly',width=15,
                                font=('Segoe UI',8))
        self.combo.pack(fill='x',pady=(0,1))
        for label,key in [('Bật lùa quái Cự Thú, Thiên Ngoại Ảo','move'),
                          ('Tìm Quái và Đi Cổ Mộ','find')]:
            self.checks[key]=tk.BooleanVar(value=saved.get(key,False))
        for label in ['Nhặt Đồ']+TIMED:
            self.checks[label]=tk.BooleanVar(value=saved.get('enabled',{}).get(label,label=='Nhặt Đồ'))
            if label in TIMED:
                default='180' if label.startswith('Bay') else '2'
                self.intervals[label]=tk.StringVar(value=str(saved.get('intervals',{}).get(label,default)))
        for label,key in [('Lùa quái Cự Thú / Thiên Ngoại Ảo','move'),
                          ('Tìm quái / Cổ Mộ','find')]:
            tk.Checkbutton(self.frame,text=label,variable=self.checks[key],bg='white',
                           fg='#9d2921',font=('Segoe UI',7,'bold'),anchor='w',
                           padx=0,pady=0).pack(fill='x')
        tk.Frame(self.frame,height=1,bg='#c4ddf4').pack(fill='x',pady=1)
        skills=tk.Frame(self.frame,bg='white');skills.pack(fill='x')
        skills.columnconfigure(0,weight=1)
        tk.Label(skills,text='Tính năng',bg='white',fg='#43698e',font=('Segoe UI',7,'bold')).grid(row=0,column=0,sticky='w')
        tk.Label(skills,text='Giây',bg='white',fg='#43698e',font=('Segoe UI',7,'bold')).grid(row=0,column=1)
        for ri,label in enumerate(['Nhặt Đồ']+TIMED,1):
            tk.Checkbutton(skills,text=label,variable=self.checks[label],bg='white',
                           font=('Segoe UI',7),anchor='w',padx=0,pady=0).grid(row=ri,column=0,sticky='w',pady=0)
            if label in TIMED:
                tk.Entry(skills,textvariable=self.intervals[label],width=6,
                         font=('Segoe UI',7),justify='center',relief='solid',bd=1).grid(row=ri,column=1,sticky='e',padx=(2,1),pady=0)
        self.status=tk.StringVar(value='Sẵn sàng')
        tk.Label(self.frame,textvariable=self.status,bg='white',fg='#44617a',
                 font=('Segoe UI',7,'bold'),wraplength=230,anchor='w').pack(fill='x',pady=(1,0))

    def refresh(self,options=None):
        if options is None: options=windows()
        self.hwnds=dict(options)
        self.combo['values']=list(self.hwnds)
        if self.window.get() not in self.hwnds:
            self.window.set(options[min(self.index-1,len(options)-1)][0] if options else '')

    def settings(self):
        return {'window':self.window.get(),'move':self.checks['move'].get(),
                'find':self.checks['find'].get(),
                'enabled':{k:v.get() for k,v in self.checks.items() if k not in ('move','find')},
                'intervals':{k:v.get() for k,v in self.intervals.items()}}

    def toggle(self):
        if self.running.is_set(): self.stop();return
        hwnd=self.hwnds.get(self.window.get())
        if not hwnd or not u.IsWindow(hwnd):
            messagebox.showwarning('Thiếu cửa sổ game',f'Nhân vật {self.index}: chọn cửa sổ game hợp lệ.')
            return
        for other in self.app.characters:
            if other is not self and other.running.is_set() and other.active_hwnd==hwnd:
                messagebox.showwarning('Trùng cửa sổ',f'Cửa sổ này đang được nhân vật {other.index} điều khiển.')
                return
        intervals={}
        for name,var in self.intervals.items():
            try:
                seconds=float(var.get().replace(',','.'))
                if not 0.1<=seconds<=10800:raise ValueError()
            except ValueError:
                messagebox.showwarning('Thời gian không hợp lệ',f'Nhân vật {self.index} - {name}: nhập 0,1 đến 10800 giây.')
                return
            intervals[name]=seconds
        flags={k:v.get() for k,v in self.checks.items()}
        self.active_hwnd=hwnd
        self.running.set()
        self.button.config(text='■ TẮT AUTO',bg='#d44444')
        self.status.set('Đang chạy')
        self.app.save()
        self.thread=threading.Thread(target=self.worker,args=(hwnd,flags,intervals),daemon=True)
        self.thread.start()

    def stop(self):
        self.running.clear()
        self.button.config(text='▶ BẬT AUTO',bg='#078d28')
        self.status.set('Đã yêu cầu dừng')

    def report(self,msg):
        try:self.app.root.after(0,lambda:self.status.set(msg))
        except RuntimeError:pass

    def worker(self,hwnd,flags,intervals):
        now=time.monotonic()
        due={}
        if flags['find']:due['find']=now
        for label in ['Nhặt Đồ']+TIMED:
            if flags[label]:due[label]=now
        moving=flags['move']
        sequence=random.choice(PATTERNS).replace(' ','')
        index=0;phase='moving';next_move=now+2
        while self.running.is_set() and u.IsWindow(hwnd):
            now=time.monotonic()
            for label in list(due):
                period=3.5 if label=='find' else 0.1 if label=='Nhặt Đồ' else intervals[label]
                if now>=due[label]:
                    if label=='find':vk=VK['Tab']
                    elif label=='Nhặt Đồ':vk=VK['Space']
                    elif label.startswith('Skill'):vk=VK[label.split()[-1]]
                    else:vk=VK[label.split()[-1].strip('()')]
                    key(hwnd,vk)
                    due[label]=now+period
            if moving and now>=next_move:
                if phase=='fighting':
                    sequence=random.choice(PATTERNS).replace(' ','');index=0;phase='moving'
                if phase=='moving':
                    letter=sequence[index]
                    box=client_box(hwnd)
                    if box:
                        w,h=box[2]-box[0],box[3]-box[1]
                        x,y=POINTS[letter]
                        click(hwnd,w*x,h*y)
                    index+=1
                    if index==len(sequence):phase='fighting';next_move=now+10
                    else:next_move=now+2
            time.sleep(0.02)
        self.running.clear()
        try:self.app.root.after(0,lambda:self._ended())
        except RuntimeError:pass

    def _ended(self):
        self.button.config(text='▶ BẬT AUTO',bg='#078d28')
        self.status.set('Đã dừng')

class App:
    def __init__(self):
        self.root=tk.Tk()
        self.root.title('Auto Train Mil và Cổ Mộ — 4 nhân vật')
        # Cố định tỷ lệ 9:16, 540x960px; không có cuộn dọc.
        # Skill 1-5, giao diện thu gọn.
        self.root.geometry('540x960')
        self.root.resizable(False,False)
        self.root.configure(bg='#e7f3ff')
        self.data={}
        try:self.data=json.loads(CONFIG.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        self.characters=[]
        self.build()
        self.root.protocol('WM_DELETE_WINDOW',self.close)

    def build(self):
        # Nền cây xanh hoạt hình phủ toàn bộ cửa sổ, bao quanh bốn bảng chức năng.
        self.canvas=tk.Canvas(self.root,bg='#bdf5df',highlightthickness=0,bd=0)
        self.canvas.pack(side='left',fill='both',expand=True)
        bg_file=ROOT/'forest_background.png'
        self.bg_original=Image.open(bg_file).convert('RGB') if bg_file.exists() else None
        self.bg_photo=None
        self.bg_id=self.canvas.create_image(0,0,anchor='nw') if self.bg_original else None
        self.content=tk.Frame(self.canvas,bg='#d8f6e7')
        self.content_id=self.canvas.create_window(15,8,window=self.content,anchor='nw')
        self.canvas.bind('<Configure>',self._resize_background)

        # Banner hai nhân vật tràn toàn bộ bề ngang phần nội dung.
        banner=ROOT/'header.png'
        if banner.exists():
            im=Image.open(banner).convert('RGB')
            # Ảnh banner gốc đã có chữ vàng nổi, Zalo và hai nhân vật.
            # Không vẽ thêm chữ trắng che lên ảnh.
            self.banner_source=im
            self.banner_width=0
            b=tk.Canvas(self.content,width=535,height=112,highlightthickness=0,bd=0)
            b.pack(fill='x',padx=0,pady=(0,1))
            self.banner_canvas=b
            self.banner_image_id=b.create_image(0,0,anchor='nw')
            b.bind('<Configure>',self._resize_banner)
        else:
            tk.Label(self.content,text='Auto Train Mil và Cổ Mộ',font=('Segoe UI',14,'bold'),
                     bg='#0875d4',fg='white').pack(fill='x',ipady=6)
        toolbar=tk.Frame(self.content,bg='#d8f6e7');toolbar.pack(fill='x',padx=6,pady=(1,1))
        ttk.Button(toolbar,text='⟳ Làm mới',command=self.refresh_all).pack(side='left')
        tk.Label(toolbar,text='Zalo: TuanHo – 0398438721',font=('Segoe UI',8,'bold'),
                 bg='#d8f6e7',fg='#064d9b').pack(side='right',padx=3)
        grid=tk.Frame(self.content,bg='#d8f6e7');grid.pack(fill='both',expand=True,padx=3,pady=1)
        for c in range(2):grid.columnconfigure(c,weight=1,uniform='characters')
        for r in range(2):grid.rowconfigure(r,weight=1,uniform='characters')
        saved=self.data.get('characters',[{}]*4)
        for i in range(4):
            self.characters.append(Character(self,i+1,grid,saved[i] if i<len(saved) else {}))
        self.refresh_all()
        self.sync=tk.BooleanVar(value=False)


    def _resize_banner(self,event):
        width=max(1,event.width)
        if width==self.banner_width:return
        self.banner_width=width
        height=112  # Thu gọn banner để cả 4 trạng thái Sẵn sàng hiển thị
        self.banner_canvas.configure(height=height)
        self.banner=ImageTk.PhotoImage(self.banner_source.resize((width,height),Image.Resampling.LANCZOS))
        self.banner_canvas.itemconfigure(self.banner_image_id,image=self.banner)

    def _resize_background(self,event):
        if self.bg_original is not None:
            im=self.bg_original.resize((max(1,event.width),max(1,event.height)),Image.Resampling.LANCZOS)
            self.bg_photo=ImageTk.PhotoImage(im)
            self.canvas.itemconfig(self.bg_id,image=self.bg_photo)
            self.canvas.tag_lower(self.bg_id)
        # Giữ lá cây tràn viền, chừa 15px ở hai cạnh.
        self.canvas.itemconfig(self.content_id,width=max(480,event.width-20))

    def refresh_all(self):
        opts=windows()
        for c in self.characters:c.refresh(opts)

    def sync_settings(self):
        if not self.sync.get():return
        src=self.characters[0]
        for dst in self.characters[1:]:
            for name,var in src.checks.items():dst.checks[name].set(var.get())
            for name,var in src.intervals.items():dst.intervals[name].set(var.get())
        self.save()

    def start_all(self):
        for c in self.characters:
            if not c.running.is_set():c.toggle()

    def stop_all(self):
        for c in self.characters:c.stop()

    def save(self):
        data={'characters':[c.settings() for c in self.characters], 'sync':self.sync.get() if hasattr(self,'sync') else False}
        try:CONFIG.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:pass

    def close(self):
        self.stop_all();self.save();self.root.destroy()

if __name__=='__main__':
    App().root.mainloop()
