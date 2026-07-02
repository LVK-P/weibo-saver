"""Weibo Saver GUI v6 — UI/后端深度整合."""
from __future__ import annotations
import json, os, re, sys, time, threading, queue
from pathlib import Path
from datetime import datetime, timedelta
from typing import Callable

try: import customtkinter as ctk; HAS_CTK = True
except ImportError: HAS_CTK = False

if not HAS_CTK:
    def run_gui(*a,**kw): print("pip install customtkinter"); return 1
else:
    C={"bg":"#262626","sidebar":"#1E1E1E","card":"#383838","card_border":"#555555",
       "text":"#BFBFBF","text_sec":"#999999","accent":"#FFD700","accent2":"#FFC107",
       "success":"#2BA471","warn":"#E37318","danger":"#D54941","divider":"#444444",
       "input_bg":"#2A2A2A","input_bd":"#555555","hover":"#4A4A4A","sel":"#3A3A3A",
       "toggle_on":"#FFD700","toggle_off":"#666666","disabled":"#555555","dark_gray":"#777777"}
    FONT="Microsoft YaHei"; FS={"title":30,"sub":20,"body":16,"small":16,"cap":16}
    LOGO_PATH=Path(__file__).parent.parent/"resources"/"logo.png"
    _uiq=queue.Queue()
    def ui(fn): _uiq.put(fn)

    # ═══════════════════════════════════════════
    # FlashButton — 替代 CTkButton：悬停不变色 + 单击闪烁反馈
    # ═══════════════════════════════════════════
    _OrigButton = ctk.CTkButton
    _FLASH_EXEMPT_KEYS = {"login", "users", "crawl", "settings", "logs", "dlogs"}

    class _FB(_OrigButton):
        """自定义按钮：禁用悬停效果，非豁免按钮单击时出现换色闪烁。

        通过 _flash_key 参数标记侧边栏导航按钮来豁免闪烁。
        所有按钮的 hover_color 强制与 fg_color 相同，悬停无视觉变化。
        """
        def __init__(self, master=None, **kwargs):
            # 提取自定义参数
            self._flash_key = kwargs.pop('_flash_key', None)
            # 强制 hover_color == fg_color（禁用悬停效果）
            fg = kwargs.get('fg_color', None)
            if fg is not None:
                kwargs['hover_color'] = fg
            super().__init__(master, **kwargs)
            self._fb_orig_fg = fg
            self._fb_flashing = False
            # 非豁免按钮绑定单击闪烁
            if self._flash_key not in _FLASH_EXEMPT_KEYS:
                self.bind('<Button-1>', self._fb_flash, add='+')

        def _fb_flash(self, event=None):
            """单击时短暂换色（~120ms），然后恢复原色."""
            if not self.winfo_exists():
                return
            fg = self._fb_orig_fg
            # 选择闪烁色：深色按钮→白，浅色/透明→蓝灰
            if not fg or fg in ("transparent", C["card"], C["bg"], C["sidebar"], C["hover"]):
                fc = C["accent"]
            elif fg in (C["success"], C["danger"], C["warn"], C["dark_gray"]):
                fc = "#FFFFFF"
            else:
                fc = "#FFFFFF"
            self._fb_flashing = True
            self.configure(fg_color=fc)
            self._fb_flashing = False
            self.after(120, self._fb_restore)

        def _fb_restore(self):
            """恢复按钮原色."""
            if self.winfo_exists():
                self._fb_flashing = True
                self.configure(fg_color=self._fb_orig_fg)
                self._fb_flashing = False

        def configure(self, **kwargs):
            """重写 configure：同步 hover_color 与 fg_color，维持无悬停效果."""
            if 'fg_color' in kwargs:
                fg = kwargs['fg_color']
                if not getattr(self, '_fb_flashing', False):
                    self._fb_orig_fg = fg
                if 'hover_color' not in kwargs:
                    kwargs['hover_color'] = fg
            super().configure(**kwargs)

    # 全局替换 CTkButton
    ctk.CTkButton = _FB

    from .gui_config import GuiSettings

    class LogCard(ctk.CTkFrame):
        def __init__(self,master,entry:dict):
            super().__init__(master,fg_color=C["card"],border_width=0,corner_radius=6)
            self._e=entry; self._expanded=False; self._build()
        def _build(self):
            e=self._e; row=ctk.CTkFrame(self,fg_color="transparent"); row.pack(fill="x",padx=8,pady=(3,3))
            cat_color_map={"错误":C["danger"],"抓取错误":C["danger"],"抓取":C["accent"],"变更":C["warn"],"用户":C["accent"],"设置":C["accent"]}
            badge_color=cat_color_map.get(e.get("cat",""),C["text_sec"])
            # 时间
            ctk.CTkLabel(row,text=e["time"],width=50,font=(FONT,10),text_color=C["text_sec"]).pack(side="left")
            # 类别标签
            badge=ctk.CTkFrame(row,fg_color=badge_color,corner_radius=3); badge.pack(side="left",padx=(4,4))
            ctk.CTkLabel(badge,text=e.get("cat","")[:8],font=(FONT,9),text_color="#1A1A1A").pack(padx=3,pady=1)
            # 摘要
            ctk.CTkLabel(row,text=e["msg"][:60],font=(FONT,10),text_color=C["text"],anchor="w").pack(side="left",fill="x",expand=True)
            # 展开按钮
            self._eb=ctk.CTkButton(row,text="展开",width=38,height=18,corner_radius=3,font=(FONT,9),
                fg_color="transparent",text_color=C["accent"],command=self._toggle); self._eb.pack(side="right",padx=(2,0))
            # 复制按钮
            ctk.CTkButton(row,text="复制",width=34,height=18,corner_radius=3,font=(FONT,9),
                fg_color="transparent",text_color=C["text_sec"],command=self._copy).pack(side="right")
            # 详细内容（默认隐藏）
            self._det=ctk.CTkFrame(self,fg_color=C["hover"],corner_radius=4)
            ctk.CTkLabel(self._det,text=e.get("detail",e["msg"]),font=(FONT,9),text_color=C["text"],
                         wraplength=600,justify="left").pack(padx=8,pady=4,anchor="w")
        def _toggle(self):
            self._expanded=not self._expanded
            if self._expanded: self._det.pack(fill="x",padx=8,pady=(0,4)); self._eb.configure(text="收起")
            else: self._det.pack_forget(); self._eb.configure(text="展开")
        def _copy(self):
            self.clipboard_clear()
            self.clipboard_append(f"[{self._e.get('time','')}] [{self._e.get('cat','')}]\n{self._e.get('detail',self._e.get('msg',''))}")

    # ═══════════════════════════════════════════
    # 详细日志卡片（可展开 + 复制 + 环境信息）
    # ═══════════════════════════════════════════
    class DetailLogCard(ctk.CTkFrame):
        def __init__(self,master,entry:dict):
            super().__init__(master,fg_color=C["card"],border_width=0,corner_radius=6)
            self._e=entry; self._expanded=False; self._build()
        def _build(self):
            e=self._e; row=ctk.CTkFrame(self,fg_color="transparent"); row.pack(fill="x",padx=8,pady=(3,3))
            cat_color_map={"致命":C["danger"],"错误":C["danger"],"超时":C["warn"],"Cookie失效":C["danger"],"API异常":C["warn"],"异常":C["warn"]}
            badge_color=cat_color_map.get(e.get("cat",""),C["text_sec"])
            # 时间 + UID
            header=f"{e['time']}  UID={e.get('uid','?')}"
            ctk.CTkLabel(row,text=header,width=120,font=(FONT,10),text_color=C["text_sec"]).pack(side="left")
            # 类别标签
            badge=ctk.CTkFrame(row,fg_color=badge_color,corner_radius=3); badge.pack(side="left",padx=(4,4))
            ctk.CTkLabel(badge,text=e.get("cat","")[:8],font=(FONT,9),text_color="#1A1A1A").pack(padx=3,pady=1)
            # 摘要
            ctk.CTkLabel(row,text=e["msg"][:60],font=(FONT,10),text_color=C["text"],anchor="w").pack(side="left",fill="x",expand=True)
            # 展开按钮
            self._eb=ctk.CTkButton(row,text="展开",width=38,height=18,corner_radius=3,font=(FONT,9),
                fg_color="transparent",text_color=C["accent"],command=self._toggle); self._eb.pack(side="right",padx=(2,0))
            # 复制按钮
            ctk.CTkButton(row,text="复制",width=34,height=18,corner_radius=3,font=(FONT,9),
                fg_color="transparent",text_color=C["text_sec"],command=self._copy).pack(side="right")
            # 详细内容（默认隐藏）
            self._det=ctk.CTkFrame(self,fg_color=C["hover"],corner_radius=4)
            det_text = e.get("detail",e["msg"])
            det_text += f"\n\n环境: {e.get('env','?')}"
            det_text += f"\nCookie: {e.get('cookie_status','?')}"
            if e.get("raw_data"):
                det_text += f"\n\n返回数据:\n{e['raw_data'][:2000]}"
            ctk.CTkLabel(self._det,text=det_text,font=(FONT,9),text_color=C["text"],
                         wraplength=600,justify="left").pack(padx=8,pady=4,anchor="w")
        def _toggle(self):
            self._expanded=not self._expanded
            if self._expanded: self._det.pack(fill="x",padx=8,pady=(0,4)); self._eb.configure(text="收起")
            else: self._det.pack_forget(); self._eb.configure(text="展开")
        def _copy(self):
            self.clipboard_clear()
            e=self._e
            txt=f"[{e.get('time','')}] [{e.get('cat','')}] UID={e.get('uid','?')}\n{e.get('detail',e.get('msg',''))}"
            txt+=f"\n环境: {e.get('env','?')}\nCookie: {e.get('cookie_status','?')}"
            if e.get("raw_data"): txt+=f"\n返回数据:\n{e['raw_data'][:2000]}"
            self.clipboard_append(txt)

    # ═══════════════════════════════════════════
    # Cookie 页（纯内联，无弹窗）
    # ═══════════════════════════════════════════
    class CookiePage(ctk.CTkFrame):
        def __init__(self,master,app):
            super().__init__(master,fg_color=C["bg"]); self._app=app; self._running=False; self._build()
        def _build(self):
            ctk.CTkLabel(self,text="Cookie 设置",font=(FONT,FS["title"],"bold"),text_color=C["text"]).pack(anchor="w",padx=20,pady=(16,8))
            # 状态条
            sf=ctk.CTkFrame(self,fg_color=C["card"],corner_radius=8,border_width=0)
            sf.pack(fill="x",padx=20,pady=(0,12))
            ctk.CTkLabel(sf,text="状态:",font=(FONT,FS["small"]),text_color=C["text_sec"]).pack(side="left",padx=(12,6),pady=10)
            self._dot=ctk.CTkLabel(sf,text="●",font=(FONT,FS["body"]),text_color=C["text_sec"],width=20); self._dot.pack(side="left")
            self._sl=ctk.CTkLabel(sf,text="未设置",font=(FONT,FS["small"]),text_color=C["text_sec"]); self._sl.pack(side="left")
            bf=ctk.CTkFrame(sf,fg_color="transparent"); bf.pack(side="right",padx=8,pady=6)
            self._clr=ctk.CTkButton(bf,text="清除",width=50,height=24,corner_radius=4,font=(FONT,FS["cap"]),fg_color=C["dark_gray"],command=self._clear)
            self._clr.pack(side="left")

            # 扫码登录区域
            qrf=ctk.CTkFrame(self,fg_color=C["card"],corner_radius=8,border_width=0)
            qrf.pack(fill="x",padx=20,pady=(0,12))
            qrr=ctk.CTkFrame(qrf,fg_color="transparent"); qrr.pack(fill="x",padx=12,pady=8)
            ctk.CTkLabel(qrr,text="📱",font=(FONT,24)).pack(side="left",padx=(0,8))
            ctk.CTkLabel(qrr,text="手机微博扫码登录，无需手动粘贴 Cookie",
                         font=(FONT,FS["small"]),text_color=C["text"],anchor="w").pack(side="left",fill="x",expand=True)
            self._qr_btn=ctk.CTkButton(qrr,text="扫码登录",width=80,height=28,corner_radius=4,
                font=(FONT,FS["small"]),fg_color=C["success"],
                command=self._start_qr_login)
            self._qr_btn.pack(side="right",padx=(8,0))

            ctk.CTkFrame(self,height=1,fg_color=C["divider"]).pack(fill="x",padx=20,pady=(0,8))

            ctk.CTkLabel(self,text="或手动粘贴 Cookie 字符串",font=(FONT,FS["body"],"bold"),text_color=C["text"]).pack(anchor="w",padx=20,pady=(0,4))
            ctk.CTkLabel(self,text="详见使用指南",font=(FONT,FS["cap"]),text_color=C["text_sec"],justify="left").pack(anchor="w",padx=20,pady=(0,6))
            self._entry=ctk.CTkTextbox(self,height=70,font=(FONT,FS["small"]),fg_color=C["input_bg"],border_width=0,corner_radius=6,text_color=C["text"])
            self._entry.pack(fill="x",padx=20,pady=(0,6))
            row=ctk.CTkFrame(self,fg_color="transparent"); row.pack(fill="x",padx=20)
            self._vbtn=ctk.CTkButton(row,text="验证 Cookie",height=30,font=(FONT,FS["small"]),fg_color=C["accent"],command=self._validate)
            self._vbtn.pack(side="left",fill="x",expand=True)
            self._cancel=ctk.CTkButton(row,text="取消",width=50,height=30,font=(FONT,FS["cap"]),fg_color=C["disabled"],state="disabled",command=self._do_cancel)
            self._cancel.pack(side="left",padx=(8,0))
            self._msg=ctk.CTkLabel(self,text="",font=(FONT,FS["cap"]),text_color=C["text_sec"]); self._msg.pack(anchor="w",padx=20,pady=(4,0))

        def _set_state(self,status,msg=""):
            colors={"valid":C["success"],"invalid":C["danger"],"none":C["text_sec"]}
            texts={"valid":"有效","invalid":"失效","none":"未设置"}
            self._dot.configure(text_color=colors.get(status,C["text_sec"]))
            self._sl.configure(text=texts.get(status,status),text_color=colors.get(status,C["text_sec"]))
            if msg: self._msg.configure(text=msg)

        def _set_running(self,on:bool):
            self._running=on
            self._vbtn.configure(state="disabled" if on else "normal",fg_color=C["disabled"] if on else C["accent"])
            self._cancel.configure(state="normal" if on else "disabled",fg_color=C["danger"] if on else C["disabled"])

        def _do_cancel(self):
            if self._running:
                self._do_cancel_qr()
            self._running=False; self._set_running(False)

        def _clear(self):
            # Phase 4: 确认框 + 强制暂停所有监控
            dlg=ctk.CTkToplevel(self); dlg.title("确认"); dlg.geometry("360x160")
            dlg.update_idletasks(); sw,sh=dlg.winfo_screenwidth(),dlg.winfo_screenheight()
            dlg.geometry(f"360x160+{(sw-360)//2}+{(sh-160)//2}")
            dlg.attributes("-topmost",True); dlg.grab_set()
            try: self._app._set_dlg_icon(dlg)
            except: pass
            dlg.configure(fg_color=C["bg"])
            ctk.CTkLabel(dlg,text="清除 Cookie 将暂停所有监控任务",font=(FONT,FS["small"]),
                         text_color=C["text"]).pack(pady=(20,4))
            ctk.CTkLabel(dlg,text="是否继续？",font=(FONT,FS["body"],"bold"),
                         text_color=C["danger"]).pack(pady=(0,16))
            bf=ctk.CTkFrame(dlg,fg_color="transparent"); bf.pack()
            ctk.CTkButton(bf,text="确认清除",width=80,height=30,font=(FONT,FS["small"]),
                          fg_color=C["danger"],
                          command=lambda:[dlg.destroy(),self._do_clear()]).pack(side="left",padx=6)
            ctk.CTkButton(bf,text="取消",width=60,height=30,font=(FONT,FS["small"]),
                          fg_color=C["disabled"],text_color=C["text"],
                          command=dlg.destroy).pack(side="left",padx=6)

        def _do_clear(self):
            self._app._pause_all()
            self._app._cookie=""; self._app._save_cookie(); self._set_state("none","已清除，监控已暂停")
            self._app._glog("INFO", "cookie", "已清除Cookie")

        def auto_validate(self, cookie_str: str):
            """启动时自动验证已缓存的 Cookie."""
            self._entry.insert("1.0", "正在验证缓存的 Cookie...")
            self._set_running(True); self._msg.configure(text="自动验证中...",text_color=C["warn"])
            threading.Thread(target=self._v_thread, args=(cookie_str,), daemon=True).start()

        def _validate(self):
            s=self._entry.get("1.0","end-1c").strip()
            if not s: self._msg.configure(text="请输入 Cookie",text_color=C["danger"]); return
            self._set_running(True); self._msg.configure(text="验证中...",text_color=C["warn"])
            threading.Thread(target=self._v_thread,args=(s,),daemon=True).start()

        def _v_thread(self,s):
            import asyncio,httpx
            loop=None
            try:
                async def ck():
                    async with httpx.AsyncClient(timeout=8) as cl:
                        cl.headers.update({"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15","Accept":"application/json"})
                        for it in s.split(";"):
                            if "=" in it.strip(): k,v=it.strip().split("=",1); cl.cookies.set(k.strip(),v.strip(),domain=".weibo.cn")
                        r=await cl.get("https://m.weibo.cn/api/config")
                        return (r.status_code==200 and r.json().get("data",{}).get("login",False), r.headers.get("Date",""))
                loop=asyncio.new_event_loop()
                ok, server_date = loop.run_until_complete(asyncio.wait_for(ck(),timeout=20))
                if ok:
                    self._app.set_cookie(s)
                    self._app._sync_time(server_date)
                    self._app._glog("INFO", "cookie", "验证成功")
                    ui(lambda:[self._set_state("valid","Cookie 有效"), self._entry.delete("1.0","end")])
                else:
                    self._app._glog("WARN", "cookie", "验证失败")
                    ui(lambda:[self._set_state("invalid","Cookie 无效，请重新获取"), self._entry.delete("1.0","end")])
            except asyncio.TimeoutError:
                ui(lambda:[self._entry.delete("1.0","end"), self._msg.configure(text="超时！检查网络",text_color=C["danger"])])
            except Exception as e:
                _err = str(e)[:50]
                ui(lambda _e=_err: [self._entry.delete("1.0","end"), self._msg.configure(text=f"失败: {_e}", text_color=C["danger"])])
            finally:
                if loop:
                    try: loop.close()
                    except: pass
                ui(lambda: self._set_running(False))


        # ---- 扫码登录 ----

        def _start_qr_login(self):
            self._set_running(True)
            self._qr_btn.configure(state="disabled",text="登录中...")
            self._msg.configure(text="启动扫码登录...",text_color=C["warn"])
            import asyncio
            self._cancel_event = asyncio.Event()
            self._cancel.configure(state="normal",fg_color=C["danger"])
            threading.Thread(target=self._qr_thread, daemon=True).start()

        def _do_cancel_qr(self):
            """取消扫码登录."""
            if hasattr(self, '_cancel_event') and self._cancel_event is not None:
                self._cancel_event.set()
            try:
                self._cancel.configure(state="disabled",fg_color=C["disabled"])
            except Exception:
                pass

        def _qr_thread(self):
            """后台线程：执行 httpx 扫码登录."""
            dlg_ref = [None]
            import threading, asyncio
            cancel_event = getattr(self, '_cancel_event', None)
            def cancel_cb():
                if cancel_event and not cancel_event.is_set():
                    cancel_event.set()
            def show_qr(data: bytes):
                ui(lambda: self._show_qr_dlg(data, dlg_ref, cancel_cb))
            def update_status(msg: str):
                ui(lambda m=msg: [self._msg.configure(text=m, text_color=C["warn"]),
                                  self._qr_status_label.configure(text=m) if hasattr(self, '_qr_status_label') and self._qr_status_label.winfo_exists() else None])
            from weibo_saver.core.qr_login import qr_login
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(qr_login(
                    qr_callback=show_qr, status_callback=update_status,
                    cancel_event=cancel_event, timeout=120,
                ))
            finally:
                loop.close()
            ui(lambda: self._set_running(False))
            ui(lambda: self._cancel.configure(state="disabled",fg_color=C["disabled"]))
            if result.success and result.cookie_string:
                ui(lambda r=dlg_ref: (r[0].destroy() if r[0] and r[0].winfo_exists() else None))
                ui(lambda: [self._entry.delete("1.0","end"),
                            self._entry.insert("1.0", result.cookie_string)])
                ui(lambda: self._validate())
                ui(lambda: [self._qr_btn.configure(state="normal",text="扫码登录"),
                            self._msg.configure(text="扫码登录成功，正在验证...",text_color=C["success"])])
            else:
                ui(lambda: [self._msg.configure(text=result.message,text_color=C["danger"]),
                            self._qr_btn.configure(state="normal",text="扫码登录")])
                ui(lambda r=dlg_ref: (r[0].destroy() if r[0] and r[0].winfo_exists() else None))
                if result.message != "已取消":
                    self._app._glog("WARN", "cookie", f"扫码登录失败: {result.message}")

        def _show_qr_dlg(self, img_data: bytes, dlg_ref: list, cancel_cb=None):
            """显示二维码弹窗."""
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(img_data))
                max_size = 280
                w, h = img.size
                if w > max_size or h > max_size:
                    ratio = min(max_size / w, max_size / h)
                    img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            except Exception:
                return
            dlg = ctk.CTkToplevel(self)
            dlg.title("微博扫码登录")
            dlg.configure(fg_color=C["bg"])
            dlg.resizable(False, False)
            dlg.attributes("-topmost", True)
            dlg.transient(self.winfo_toplevel())
            dlg.grab_set()
            dlg_ref[0] = dlg
            dlg.update_idletasks()
            sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
            dlg.geometry(f"360x460+{(sw-360)//2}+{(sh-460)//2}")
            # 设置弹窗图标
            try: self._app._set_dlg_icon(dlg)
            except Exception: pass
            from PIL.ImageTk import PhotoImage as PILPhoto
            ctk_img = CTkImage(img, size=img.size)
            mf = ctk.CTkFrame(dlg,fg_color="transparent"); mf.pack(expand=True,fill="both",padx=20,pady=(20,10))
            ctk.CTkLabel(mf,text="请使用手机微博扫描二维码",
                         font=(FONT,FS["body"],"bold"),text_color=C["text"]).pack(pady=(0,8))
            ql = ctk.CTkLabel(mf,image=ctk_img,text=""); ql.image=ctk_img; ql.pack(pady=(0,10))
            ctk.CTkLabel(mf,text="打开手机微博 App → 扫一扫",
                         font=(FONT,FS["cap"]),text_color=C["text_sec"]).pack()
            self._qr_status_label = ctk.CTkLabel(mf,text="等待扫码...",font=(FONT,FS["small"]),text_color=C["warn"])
            self._qr_status_label.pack(pady=(8,0))
            bf=ctk.CTkFrame(mf,fg_color="transparent"); bf.pack(pady=(12,0))
            ctk.CTkButton(bf,text="取消",width=80,height=28,font=(FONT,FS["small"]),
                          fg_color=C["dark_gray"],text_color="white",
                          command=lambda: [cancel_cb() if cancel_cb else None, dlg.destroy(),
                            ui(lambda: self._msg.configure(text="已取消",text_color=C["text_sec"])),
                            ui(lambda: self._qr_btn.configure(state="normal",text="扫码登录"))]
                          ).pack()
            dlg.protocol("WM_DELETE_WINDOW", lambda: [cancel_cb() if cancel_cb else None, dlg.destroy(),
                         ui(lambda: self._msg.configure(text="已取消",text_color=C["text_sec"])),
                         ui(lambda: self._qr_btn.configure(state="normal",text="扫码登录"))])



    # ═══════════════════════════════════════════
    # 用户卡片 v5
    # ═══════════════════════════════════════════
    class UserCard(ctk.CTkFrame):
        def __init__(self,master,user:dict,app,idx:int,on_move:Callable,on_sel:Callable):
            super().__init__(master,fg_color=C["card"],border_width=0,corner_radius=8)
            self._app=app; self._u=user; self._idx=idx; self._on_move=on_move; self._on_sel=on_sel
            self._open=False; self._build()

        @property
        def uid(self): return self._u.get("uid","")
        @property
        def selected(self): return self._cb_var.get()
        @property
        def display_name(self):
            cn=self._u.get("custom_name","")
            sn=self._u.get("screen_name","")
            return cn or sn or f"user_{self.uid}"

        def _build(self):
            u=self._u; uid=u.get("uid",""); sn=u.get("screen_name","")
            pc=u.get("post_count",0); mon=u.get("monitoring",True)
            crawl_state=u.get("_crawl_state","idle")

            r=ctk.CTkFrame(self,fg_color="transparent"); r.pack(fill="x",padx=10,pady=(6,3))
            # 勾选框
            self._cb_var=ctk.BooleanVar(value=u.get("_selected",False))
            ctk.CTkCheckBox(r,text="",variable=self._cb_var,width=20,height=20,border_width=2,fg_color=C["accent"],command=lambda:self._on_sel(self)).pack(side="left",padx=(0,6))
            # 拖拽
            dh=ctk.CTkFrame(r,width=8,fg_color="transparent"); dh.pack(side="left",padx=(0,4))
            ctk.CTkLabel(dh,text="⋮⋮",font=(FONT,12),text_color=C["text_sec"]).pack()
            dh.bind("<Button-1>",lambda e:setattr(self,"_dy",e.y))
            dh.bind("<B1-Motion>",lambda e:self._drag(e))
            dh.bind("<ButtonRelease-1>",lambda e:setattr(self,"_moved",False))
            # 头像
            av=ctk.CTkFrame(r,width=28,height=28,fg_color=C["accent"],corner_radius=14); av.pack(side="left",padx=(0,8))
            ctk.CTkLabel(av,text=self.display_name[0].upper(),font=(FONT,12,"bold"),text_color="#1A1A1A").place(relx=.5,rely=.5,anchor="center")
            # 昵称（微博名，优先于UID）
            inf=ctk.CTkFrame(r,fg_color="transparent"); inf.pack(side="left",fill="x",expand=True)
            ctk.CTkLabel(inf,text=self.display_name,font=(FONT,FS["sub"],"bold"),text_color=C["text"]).pack(anchor="w")
            # 状态
            sf=ctk.CTkFrame(inf,fg_color="transparent"); sf.pack(anchor="w")
            sc="#2BA471" if mon else "#424A57"
            self._ml=ctk.CTkLabel(sf,text="● 监控中  " if mon else "○ 已暂停  ",font=(FONT,FS["cap"]),text_color=sc)
            self._ml.pack(side="left")
            cs_map={"idle":"未开始","running":"抓取中...","done":"已完成"}
            cc_map={"idle":"#424A57","running":"#E37318","done":"#2BA471"}
            self._cl=ctk.CTkLabel(sf,text=cs_map.get(crawl_state,""),font=(FONT,FS["cap"]),text_color=cc_map.get(crawl_state,"#424A57"))
            self._cl.pack(side="left")

            rt=ctk.CTkFrame(r,fg_color="transparent"); rt.pack(side="right")
            total=u.get("statuses_count",0)
            count_text=f"{pc}/{total} 篇" if total else f"{pc} 篇"
            self._pc=ctk.CTkLabel(rt,text=count_text,font=(FONT,FS["body"]),text_color=C["text_sec"])
            self._pc.pack(side="left",padx=(0,6))
            ctk.CTkButton(rt,text="↑",width=22,height=22,corner_radius=4,font=(FONT,10),fg_color=C["hover"],text_color=C["text"],command=lambda:self._on_move(self._idx,-1)).pack(side="left",padx=(0,2))
            ctk.CTkButton(rt,text="↓",width=22,height=22,corner_radius=4,font=(FONT,10),fg_color=C["hover"],text_color=C["text"],command=lambda:self._on_move(self._idx,1)).pack(side="left",padx=(0,6))
            self._eb=ctk.CTkButton(rt,text="展开",width=48,height=24,corner_radius=5,font=(FONT,FS["cap"]),fg_color=C["accent"],command=self._toggle)
            self._eb.pack(side="left")

            # 展开区
            self._det=ctk.CTkFrame(self,fg_color="transparent")
            ig=ctk.CTkFrame(self._det,fg_color=C["hover"],corner_radius=6); ig.pack(fill="x",padx=10,pady=(0,4))
            # UID
            rw=ctk.CTkFrame(ig,fg_color="transparent"); rw.pack(fill="x",padx=8,pady=1)
            ctk.CTkLabel(rw,text="UID: ",width=50,font=(FONT,FS["cap"]),text_color=C["text_sec"]).pack(side="left")
            ctk.CTkLabel(rw,text=uid,font=(FONT,FS["cap"],"bold"),text_color=C["text"]).pack(side="left")
            if sn and sn!=uid:
                rw2=ctk.CTkFrame(ig,fg_color="transparent"); rw2.pack(fill="x",padx=8,pady=1)
                ctk.CTkLabel(rw2,text="微博名: ",width=50,font=(FONT,FS["cap"]),text_color=C["text_sec"]).pack(side="left")
                ctk.CTkLabel(rw2,text=sn,font=(FONT,FS["cap"]),text_color=C["text"]).pack(side="left")

            # 自定义昵称
            nr=ctk.CTkFrame(self._det,fg_color="transparent"); nr.pack(fill="x",padx=10,pady=2)
            ctk.CTkLabel(nr,text="昵称: ",font=(FONT,FS["cap"]),text_color=C["text_sec"]).pack(side="left")
            self._name_entry=ctk.CTkEntry(nr,height=22,font=(FONT,FS["cap"]),fg_color=C["input_bg"],border_width=0,text_color=C["text"])
            self._name_entry.insert(0,u.get("custom_name",""))
            self._name_entry.pack(side="left",fill="x",expand=True,padx=(4,0))

            # 独立路径
            pr=ctk.CTkFrame(self._det,fg_color="transparent"); pr.pack(fill="x",padx=10,pady=3)
            po=u.get("custom_path_enabled",False)
            self._path_var=ctk.BooleanVar(value=po)
            ctk.CTkCheckBox(pr,text="独立路径",variable=self._path_var,width=20,height=20,border_width=2,font=(FONT,FS["cap"]),fg_color=C["accent"],command=self._tp).pack(side="left")
            self._pf=ctk.CTkFrame(pr,fg_color="transparent")
            if po: self._pf.pack(side="left",fill="x",expand=True,padx=(6,0))
            self._pe=ctk.CTkEntry(self._pf,height=22,font=(FONT,FS["cap"]),fg_color=C["input_bg"],border_width=0,text_color=C["text"])
            self._pe.insert(0,u.get("archive_path",str(self._app._ar))); self._pe.pack(side="left",fill="x",expand=True)
            ctk.CTkButton(self._pf,text="浏览",width=96,height=22,font=(FONT,FS["cap"]),fg_color=C["accent"],command=self._browse).pack(side="left",padx=(10,0))

            # 抓取上限
            lr=ctk.CTkFrame(self._det,fg_color="transparent"); lr.pack(fill="x",padx=10,pady=3)
            lo=u.get("custom_limit_enabled",False)
            self._lim_var=ctk.BooleanVar(value=lo)
            ctk.CTkCheckBox(lr,text="独立上限",variable=self._lim_var,width=20,height=20,border_width=2,font=(FONT,FS["cap"]),fg_color=C["accent"],command=self._tl).pack(side="left")
            self._le=ctk.CTkEntry(lr,width=55,height=22,font=(FONT,FS["cap"]),fg_color=C["input_bg"],border_width=0,text_color=C["text"])
            self._le.insert(0,str(u.get("max_cards","")))
            if lo: self._le.pack(side="left",padx=(6,0))
            ctk.CTkLabel(lr,text="条",font=(FONT,FS["cap"]),text_color=C["text_sec"]).pack(side="left",padx=(4,0))

            # 内容类型
            tr=ctk.CTkFrame(self._det,fg_color="transparent"); tr.pack(fill="x",padx=10,pady=(6,3))
            ctk.CTkLabel(tr,text="内容:",font=(FONT,FS["cap"]),text_color=C["text_sec"]).pack(side="left",padx=(0,6))
            self._tv=ctk.BooleanVar(value=u.get("content_text",True))
            self._iv=ctk.BooleanVar(value=u.get("content_images",True))
            self._vv=ctk.BooleanVar(value=u.get("content_videos",True))
            st="disabled" if mon else "normal"
            ctk.CTkCheckBox(tr,text="文字",variable=self._tv,width=20,height=20,border_width=2,font=(FONT,FS["cap"]),fg_color=C["accent"],state=st).pack(side="left",padx=(0,4))
            ctk.CTkCheckBox(tr,text="图片",variable=self._iv,width=20,height=20,border_width=2,font=(FONT,FS["cap"]),fg_color=C["accent"],state=st).pack(side="left",padx=(0,4))
            ctk.CTkCheckBox(tr,text="视频",variable=self._vv,width=20,height=20,border_width=2,font=(FONT,FS["cap"]),fg_color=C["accent"],state=st).pack(side="left",padx=(0,4))

            # 操作按钮
            br=ctk.CTkFrame(self._det,fg_color="transparent"); br.pack(fill="x",padx=10,pady=(8,10))
            bw=96  # 统宽
            ctk.CTkButton(br,text="打开存档",width=bw,height=22,corner_radius=4,font=(FONT,FS["cap"]),fg_color=C["accent"],command=lambda:self._open_archive()).pack(side="left",padx=(0,5))
            ctk.CTkButton(br,text="抓取",width=bw,height=22,corner_radius=4,font=(FONT,FS["cap"]),fg_color=C["accent"],command=lambda:self._app.run_crawl(uid)).pack(side="left",padx=5)
            self._pb=ctk.CTkButton(br,text="暂停" if mon else "恢复",width=bw,height=22,corner_radius=4,font=(FONT,FS["cap"]),fg_color=C["warn"] if mon else C["accent"],command=lambda:self._tmon(uid))
            self._pb.pack(side="left",padx=5)
            ctk.CTkButton(br,text="保存设置",width=bw,height=22,corner_radius=4,font=(FONT,FS["cap"]),fg_color=C["accent"],command=self._save).pack(side="left",padx=5)

        def _browse(self):
            from tkinter import filedialog
            p=filedialog.askdirectory(title="选择存档目录")
            if p: self._pe.delete(0,"end"); self._pe.insert(0,p)
        def _tp(self):
            if self._path_var.get(): self._pf.pack(side="left",fill="x",expand=True,padx=(6,0))
            else: self._pf.pack_forget()
        def _tl(self):
            if self._lim_var.get(): self._le.pack(side="left",padx=(6,0))
            else: self._le.pack_forget()
        def _drag(self, e):
            """拖拽排序：拖动超过 15px 触发位置交换."""
            if abs(e.y - self._dy) > 15 and not getattr(self, '_moved', False):
                self._moved = True
                self._on_move(self._idx, 1 if e.y > self._dy else -1)

        def _open_archive(self):
            """打开此用户的存档目录（使用有效路径）."""
            import subprocess
            root = self._app._effective_archive(self._u)
            user_dir = root / "users" / f"{self.display_name}_{self.uid}"
            if user_dir.exists():
                os.startfile(str(user_dir))
            elif root.exists():
                os.startfile(str(root))

        def _tmon(self,uid):
            self._u["monitoring"]=not self._u.get("monitoring",True); self._app._save_users()
            mon=self._u.get("monitoring",True)
            if not self._open:
                self._rebuild()
            else:
                if hasattr(self,'_pb'):
                    self._pb.configure(text="暂停" if mon else "恢复",
                                       fg_color=C["warn"] if mon else C["accent"])
                self.update_display()
        def _toggle(self):
            self._open=not self._open
            if self._open: self._det.pack(fill="x"); self._eb.configure(text="收起")
            else: self._det.pack_forget(); self._eb.configure(text="展开")
        def _save(self):
            self._u["custom_name"]=self._name_entry.get()
            self._u["custom_limit_enabled"]=self._lim_var.get()
            try: self._u["max_cards"]=int(self._le.get())
            except: self._u["max_cards"]=0
            self._u["custom_path_enabled"]=self._path_var.get()
            self._u["archive_path"]=self._pe.get()
            self._u["content_text"]=self._tv.get()
            self._u["content_images"]=self._iv.get()
            self._u["content_videos"]=self._vv.get()
            self._app._save_users(); self._app._log("设置",f"{self.display_name} 已保存")
            self.update_display()
        def _rebuild(self):
            for w in self.winfo_children(): w.destroy()
            self._open=False; self._build()
        def update_display(self):
            """仅更新卡片上的动态文字（使用 _build() 时存储的引用）."""
            try:
                u=self._u
                mon = u.get("monitoring", True)
                crawl_state = u.get("_crawl_state", "idle")
                pc = u.get("post_count", 0)
                total = u.get("statuses_count", 0)
                count_text = f"{pc}/{total} 篇" if total else f"{pc} 篇"
                sc = "#2BA471" if mon else "#424A57"
                st = "● 监控中  " if mon else "○ 已暂停  "
                cs_map = {"idle": "未开始", "running": "抓取中...", "done": "已完成"}
                cc_map = {"idle": "#424A57", "running": "#E37318", "done": "#2BA471"}
                if hasattr(self, '_ml'):
                    self._ml.configure(text=st, text_color=sc)
                if hasattr(self, '_cl'):
                    self._cl.configure(text=cs_map.get(crawl_state, ""),
                                       text_color=cc_map.get(crawl_state, "#424A57"))
                if hasattr(self, '_pc'):
                    self._pc.configure(text=count_text)
            except Exception:
                pass
        def up_idx(self,i): self._idx=i


    # ═══════════════════════════════════════════
    # 用户信息卡片（用户管理页）
    # ═══════════════════════════════════════════
    class UserInfoCard(ctk.CTkFrame):
        """用户信息展示卡片：圆形头像 + 统计数据."""
        AVATAR_S = 100  # 头像直径

        def __init__(self, master, user: dict, app):
            super().__init__(master, fg_color=C["card"], border_width=0, corner_radius=8)
            self._u = user; self._app = app; self._build()

        @property
        def uid(self): return self._u.get("uid", "")

        def _build(self):
            u = self._u; uid = u.get("uid", ""); sn = u.get("screen_name", "")
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(12, 8))

            # 左侧：圆形头像
            av_frame = ctk.CTkFrame(row, width=self.AVATAR_S, height=self.AVATAR_S,
                                     fg_color=C["avatar_bg"] if "avatar_bg" in C else C["hover"],
                                     corner_radius=self.AVATAR_S // 2)
            av_frame.pack(side="left", padx=(0, 14))
            self._av_label = ctk.CTkLabel(av_frame, text="", font=(FONT, 28, "bold"),
                                           text_color="#1A1A1A")
            self._av_label.place(relx=0.5, rely=0.5, anchor="center")
            self._av_img_ref = None  # CTkImage 引用
            self._load_avatar(av_frame, u.get("avatar_url", ""))

            # 右侧信息区
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)

            # 昵称 + UID
            name_row = ctk.CTkFrame(info, fg_color="transparent")
            name_row.pack(fill="x")
            display = sn or uid or "?"
            ctk.CTkLabel(name_row, text=display, font=(FONT, FS["sub"], "bold"),
                         text_color=C["text"]).pack(side="left")
            ctk.CTkLabel(name_row, text=f"UID: {uid}", font=(FONT, FS["cap"]),
                         text_color=C["text_sec"]).pack(side="left", padx=(8, 0))

            # 统计行
            stats_frame = ctk.CTkFrame(info, fg_color="transparent")
            stats_frame.pack(fill="x", pady=(4, 0))
            followers = u.get("followers_count", 0) or 0
            total_posts = u.get("statuses_count", 0) or 0
            saved = u.get("post_count", 0) or 0

            # 获取磁盘统计
            img_n, vid_n, folder_size = self._app._get_user_disk_stats(uid)

            stats_texts = [
                f"粉丝: {followers:,}",
                f"微博: {total_posts:,}",
                f"已保存: {saved} 篇",
                f"图片: {img_n} 张",
                f"视频: {vid_n} 个",
                f"大小: {self._fmt_size(folder_size)}",
            ]
            # 第一行
            r1 = ctk.CTkFrame(stats_frame, fg_color="transparent")
            r1.pack(fill="x")
            for t in stats_texts[:3]:
                ctk.CTkLabel(r1, text=t, font=(FONT, FS["cap"]),
                            text_color=C["text_sec"]).pack(side="left", padx=(0, 16))
            # 第二行
            r2 = ctk.CTkFrame(stats_frame, fg_color="transparent")
            r2.pack(fill="x")
            for t in stats_texts[3:]:
                ctk.CTkLabel(r2, text=t, font=(FONT, FS["cap"]),
                            text_color=C["text_sec"]).pack(side="left", padx=(0, 16))

        def _load_avatar(self, parent_frame, avatar_url: str):
            """下载并显示圆形头像."""
            if not avatar_url:
                initial = (self._u.get("screen_name", "") or self.uid)[0].upper()
                self._av_label.configure(text=initial)
                return
            import hashlib, io
            try:
                import httpx
                # 缓存到 WeiboArchive/avatars/
                cache_dir = self._app._ar / "avatars"
                cache_dir.mkdir(parents=True, exist_ok=True)
                url_hash = hashlib.md5(avatar_url.encode()).hexdigest()[:12]
                cache_path = cache_dir / f"{url_hash}.jpg"
                if not cache_path.exists():
                    # 同步下载（用户管理页打开时触发，快速）
                    try:
                        with httpx.Client(timeout=8) as cl:
                            r = cl.get(avatar_url)
                            if r.status_code == 200:
                                cache_path.write_bytes(r.content)
                    except Exception:
                        pass
                if cache_path.exists():
                    from PIL import Image, ImageDraw
                    img = Image.open(cache_path).resize((self.AVATAR_S, self.AVATAR_S), Image.LANCZOS)
                    # 圆形裁切
                    mask = Image.new("L", (self.AVATAR_S, self.AVATAR_S), 0)
                    ImageDraw.Draw(mask).ellipse([0, 0, self.AVATAR_S, self.AVATAR_S], fill=255)
                    # 转为 CTkImage
                    from customtkinter import CTkImage as CTKI
                    ctk_img = CTKI(img.resize((self.AVATAR_S, self.AVATAR_S), Image.LANCZOS),
                                   size=(self.AVATAR_S, self.AVATAR_S))
                    self._av_img_ref = ctk_img
                    self._av_label.configure(image=ctk_img, text="")
            except Exception:
                initial = (self._u.get("screen_name", "") or self.uid)[0].upper()
                self._av_label.configure(text=initial)

        @staticmethod
        def _fmt_size(size_bytes: int) -> str:
            if size_bytes < 1024:
                return f"{size_bytes}B"
            elif size_bytes < 1024 * 1024:
                return f"{size_bytes / 1024:.1f}KB"
            elif size_bytes < 1024 * 1024 * 1024:
                return f"{size_bytes / 1024 / 1024:.1f}MB"
            return f"{size_bytes / 1024 / 1024 / 1024:.2f}GB"

        def update_display(self):
            """更新统计数据（不重建控件）."""
            # 简化：标记脏数据，下次切换到用户管理页时完全重建
            pass


    # ═══════════════════════════════════════════
    # 主应用 v5
    # ═══════════════════════════════════════════
    class WeiboSaverGUI(ctk.CTk):
        def __init__(self,archive_root:Path, log_path:Path=None):
            super().__init__(); self._ar=archive_root; self._users:list[dict]=[]; self._cards:list[UserCard]=[]
            self._cookie=""; self._logs:list[dict]=[]; self._detail_logs:list[dict]=[]; self._crawling_uids:set=set()
            self._api_hourly_count=0    # 本小时累计 API 次数
            self._api_last_hour=-1       # 上次计数的小时
            self._users_lock=threading.Lock()  # 多线程保护 self._users
            self._crawl_lock=threading.Lock()   # 保护 self._crawling_uids
            self._crawl_sem=threading.Semaphore(1)  # 串行化抓取（每次只跑一个）
            self._logs_lock=threading.Lock()    # 保护 self._logs
            self._dlogs_lock=threading.Lock()   # 保护 self._detail_logs
            self._nav_buttons: dict = {}          # 侧边栏按钮引用
            self._current_page = "crawl"           # 当前页面
            # 全局日志 + 时间修正
            self._log_path = log_path or (archive_root.parent / "weibo_saver_all.log")
            self._log_lock = threading.Lock()
            self._time_offset = 0.0  # 服务器时间 - 系统时间（秒），0=未修正
            self._time_checked = False
            self.title("Weibo Saver"); self.geometry("1020x700"); self.minsize(840,540)
            # 窗口居中
            self.update_idletasks()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"1020x700+{(sw-1020)//2}+{(sh-700)//2}")
            ctk.set_appearance_mode("dark"); ctk.set_default_color_theme("dark-blue"); self.configure(fg_color=C["bg"])
            # 设置窗口图标（任务栏 + 标题栏）
            ico = Path(__file__).parent.parent / "resources" / "logo.ico"
            if ico.exists():
                try:
                    self.iconbitmap(str(ico))
                except Exception:
                    # ico 不存在时用 png 降级
                    png = ico.with_suffix(".png")
                    if png.exists():
                        from PIL import Image, ImageTk
                        img = ImageTk.PhotoImage(Image.open(png))
                        self.iconphoto(True, img)
            self._gui_settings=GuiSettings.load(self._ar)  # 必须在 _build() 之前加载
            self._licensed = True
            self._build()
            self._load_users(); self._load_cookie()
            self._start_monitor()  # 启动后台定时监测
            self._poll()
            self.bind("<F11>",self._toggle_fullscreen)  # Phase 2: 全屏切换
            self.protocol("WM_DELETE_WINDOW",self._close)

        def _toggle_fullscreen(self,event=None):
            self.attributes("-fullscreen",not self.attributes("-fullscreen"))

        def _set_dlg_icon(self, dlg):
            """给弹窗设置应用图标."""
            try:
                ico = Path(__file__).parent.parent / "resources" / "logo.ico"
                if ico.exists():
                    dlg.iconbitmap(str(ico))
            except Exception:
                pass

        def set_cookie(self,s):
            self._cookie=s; self._save_cookie()
        def _load_cookie(self):
            p=self._ar/"config"/"cookie.json"
            if p.exists():
                try: self._cookie=json.loads(p.read_text(encoding="utf-8")).get("cookie","")
                except: pass
            # 启动时自动验证已缓存的 Cookie
            if self._cookie:
                cp=self._pages.get("login")
                if cp: cp.auto_validate(self._cookie)
        def _save_cookie(self):
            p=self._ar/"config"; p.mkdir(parents=True,exist_ok=True)
            (p/"cookie.json").write_text(json.dumps({"cookie":self._cookie,"updated":datetime.now().isoformat()},ensure_ascii=False,indent=2),encoding="utf-8")

        def _log(self,cat,msg,detail=None):
            t=datetime.now().strftime("%H:%M:%S")
            with self._logs_lock:
                self._logs.append({"time":t,"cat":cat,"msg":msg,"detail":detail or msg})
                if len(self._logs)>200: self._logs=self._logs[-200:]
            ui(self._refresh_logs)

        # ---- 持久化全局日志 ----
        def _now(self):
            """返回修正后的当前时间（UTC+8 ISO格式）."""
            return (datetime.now() + timedelta(seconds=self._time_offset)).isoformat()

        def _ts(self):
            """返回修正后的时间戳字符串 [YYYY-MM-DD HH:MM:SS]."""
            return (datetime.now() + timedelta(seconds=self._time_offset)).strftime("[%Y-%m-%d %H:%M:%S]")

        def _glog(self, level, cat, msg, detail=""):
            """写入全局持久化日志（exe同级目录，追加，自动轮转）."""
            try:
                ts = self._ts()
                line = f"{ts} [{level}] [{cat}] {msg}"
                if detail:
                    line += f" | {detail}"
                line += "\n"
                with self._log_lock:
                    p = self._log_path
                    if p.exists() and p.stat().st_size > 10 * 1024 * 1024:
                        # 轮转：保留最近3个
                        for i in range(2, 0, -1):
                            src = p.with_suffix(f".log.{i}")
                            dst = p.with_suffix(f".log.{i + 1}")
                            if src.exists():
                                if i == 2:
                                    src.unlink(missing_ok=True)
                                else:
                                    src.replace(dst)
                        p.rename(p.with_suffix(".log.1"))
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with open(p, "a", encoding="utf-8") as f:
                        f.write(line)
            except Exception:
                pass

        # ---- 时间戳修正 ----
        def _sync_time(self, server_date_str: str):
            """用 HTTP Date 头修正本地时间偏移（仅首次验证 cookie 后执行）."""
            if self._time_checked or not server_date_str:
                return
            self._time_checked = True
            try:
                from email.utils import parsedate_to_datetime
                server_dt = parsedate_to_datetime(server_date_str)
                server_ts = server_dt.timestamp()
                local_ts = datetime.now().timestamp()
                offset = server_ts - local_ts
                if abs(offset) > 300:
                    self._time_offset = offset
                    self._glog("INFO", "time", f"时间戳修正 | offset={offset:.0f}s | server={server_date_str}")
            except Exception:
                pass

        # ---- 推荐监测间隔 ----
        def _recalc_interval(self):
            """根据监测对象数+内容类型+上限综合计算推荐间隔，更新仪表盘展示."""
            with self._users_lock:
                load = 0.0
                for u in self._users:
                    if not u.get("monitoring", True):
                        continue
                    if not u.get("_initial_crawl_done", False):
                        continue
                    factor = 1.0
                    if u.get("content_images", True):
                        factor += 0.3
                    if u.get("content_videos", True):
                        factor += 0.5
                    max_c = u.get("max_cards", 0) if u.get("custom_limit_enabled") else 0
                    if max_c > 50:
                        factor *= 1.5
                    elif max_c > 20:
                        factor *= 1.2
                    load += factor
            rec = max(60, min(3600, round(load * 12))) if load > 0 else 0
            ui(lambda r=rec: self._interval_label.configure(
                text=f"推荐间隔: {r}s" if r > 0 else "推荐间隔: —"
            ) if hasattr(self, "_interval_label") and self._interval_label.winfo_exists() else None)
            return rec

        def _open_guide(self):
            """打开使用指南 PDF（系统默认应用）."""
            import os as _os, ctypes
            # 尝试 MEIPASS 内嵌路径
            if getattr(sys, 'frozen', False):
                pdf = Path(sys._MEIPASS) / "Weibo Saver使用指南.pdf"
            else:
                pdf = Path(__file__).parent.parent.parent / "Weibo Saver使用指南.pdf"
            # 回退：exe 同级目录
            if not pdf.exists() and getattr(sys, 'frozen', False):
                pdf = Path(sys.executable).parent / "Weibo Saver使用指南.pdf"
            if pdf.exists():
                _os.startfile(str(pdf))
            else:
                ctypes.windll.user32.MessageBoxW(0, "未找到使用指南文件",
                                                   "Weibo Saver", 0x30)

        def _build(self):
            self.grid_columnconfigure(1,weight=1); self.grid_rowconfigure(0,weight=1)
            # 侧边栏：始终显示 Logo + 标题 + 导航按钮
            self._sidebar = ctk.CTkFrame(self,width=180,corner_radius=0,fg_color=C["sidebar"])
            self._sidebar.grid(row=0,column=0,sticky="ns"); self._sidebar.grid_propagate(False)
            lo=ctk.CTkFrame(self._sidebar,fg_color="transparent"); lo.pack(fill="x",padx=12,pady=(16,6))
            if LOGO_PATH.exists():
                try:
                    from PIL import Image; from customtkinter import CTkImage
                    img = CTkImage(Image.open(LOGO_PATH), size=(48, 48))
                    ctk.CTkLabel(lo, image=img, text="").pack(pady=(0, 6))
                except Exception:
                    pass
            ctk.CTkLabel(lo,text="Weibo Saver",font=(FONT,FS["title"],"bold"),text_color=C["accent"]).pack()
            ctk.CTkLabel(lo,text="微博图文保存工具",font=(FONT,FS["cap"]),text_color=C["text_sec"]).pack()
            ctk.CTkFrame(self._sidebar,height=1,fg_color=C["divider"]).pack(fill="x",padx=8,pady=6)
            # 导航按钮容器
            self._nav_container = ctk.CTkFrame(self._sidebar,fg_color="transparent")
            self._nav_container.pack(fill="x",padx=8,pady=0)
            self._nav_buttons: dict = {}
            self._build_nav_buttons()
            # 使用指南按钮
            ctk.CTkButton(self._sidebar,text="使用指南",height=36,corner_radius=8,
                font=(FONT,FS["body"]),fg_color=C["success"],
                text_color="white",border_width=0,
                command=self._open_guide).pack(fill="x",padx=8,pady=3)
            ctk.CTkFrame(self._sidebar,height=1,fg_color=C["divider"]).pack(fill="x",padx=8,pady=(18,6),side="bottom")

            # 内容区域
            self._ct=ctk.CTkFrame(self,corner_radius=0,fg_color=C["bg"]); self._ct.grid(row=0,column=1,sticky="nsew")
            self._pages={}
            self._build_pages()
            self._nav("crawl")  # 初始选中抓取设置

            # 状态栏（始终显示）
            stb=ctk.CTkFrame(self,height=22,corner_radius=0,fg_color=C["sidebar"]); stb.grid(row=1,column=0,columnspan=2,sticky="ew")
            self._status=ctk.CTkLabel(stb,text="就绪",font=(FONT,FS["cap"]),text_color=C["text_sec"]); self._status.pack(side="left",padx=8)
            self._api_counter=ctk.CTkLabel(stb,text="API: --/1000",font=(FONT,FS["cap"]),text_color=C["accent"])
            self._api_counter.pack(side="right",padx=12)

        def _build_nav_buttons(self):
            """在侧边栏导航容器中创建导航按钮."""
            for w in self._nav_container.winfo_children():
                w.destroy()
            self._nav_buttons.clear()
            for txt,pg in [("登录","login"),("用户管理","users"),("抓取设置","crawl"),("全局设置","settings"),("操作记录","logs"),("详细日志","dlogs")]:
                btn = ctk.CTkButton(self._nav_container,text=txt,height=40,corner_radius=8,
                    font=(FONT,FS["body"]),fg_color=C["card"],
                    text_color=C["text"],border_width=0,
                    _flash_key=pg,
                    command=lambda p=pg:self._nav(p))
                btn.pack(fill="x",pady=3)
                self._nav_buttons[pg] = btn

        def _build_pages(self):
            """创建所有功能页面."""
            self._pages["crawl"]=self._mk_crawl()
            self._pages["users"]=self._mk_users()
            self._pages["login"]=CookiePage(self._ct,self)
            self._pages["settings"]=self._mk_settings()
            self._pages["logs"]=self._mk_logs()
            self._pages["dlogs"]=self._mk_dlogs()

        def _mk_crawl(self):
            """抓取设置页：全局操作栏 + 用户管理卡片."""
            f=ctk.CTkFrame(self._ct,fg_color=C["bg"])
            bar=ctk.CTkFrame(f,fg_color=C["card"],corner_radius=6,border_width=0)
            bar.pack(fill="x",padx=12,pady=(10,6))
            ctk.CTkLabel(bar,text="全局操作:",font=(FONT,FS["small"]),text_color=C["text_sec"]).pack(side="left",padx=(12,8),pady=8)
            ctk.CTkButton(bar,text="抓取选中",width=80,height=28,corner_radius=5,font=(FONT,FS["small"]),fg_color=C["accent"],command=self._crawl_sel).pack(side="left",padx=(0,6))
            ctk.CTkButton(bar,text="暂停选中",width=80,height=28,corner_radius=5,font=(FONT,FS["small"]),fg_color=C["warn"],command=self._pause_sel).pack(side="left",padx=(0,6))
            ctk.CTkButton(bar,text="删除选中",width=80,height=28,corner_radius=5,font=(FONT,FS["small"]),fg_color=C["dark_gray"],command=self._del_sel).pack(side="left")
            self._interval_label=ctk.CTkLabel(bar,text="推荐间隔: —",font=(FONT,FS["cap"]),text_color=C["text_sec"])
            self._interval_label.pack(side="right",padx=(0,12),pady=8)
            # 用户管理卡片（展开/抓取/暂停等）
            self._scr=ctk.CTkScrollableFrame(f,fg_color=C["bg"]); self._scr.pack(fill="both",expand=True,padx=10,pady=(2,6))
            return f

        def _mk_users(self):
            """用户管理页：添加用户 + 用户信息卡片（头像/粉丝/统计）."""
            f=ctk.CTkFrame(self._ct,fg_color=C["bg"])
            # 添加用户区域
            add_bar=ctk.CTkFrame(f,fg_color=C["card"],corner_radius=6,border_width=0)
            add_bar.pack(fill="x",padx=12,pady=(10,6))
            ctk.CTkLabel(add_bar,text="添加目标用户:",font=(FONT,FS["small"]),text_color=C["text_sec"]).pack(side="left",padx=(12,8),pady=8)
            self._add_entry=ctk.CTkEntry(add_bar,font=(FONT,FS["small"]),fg_color=C["input_bg"],
                                          border_width=0,text_color=C["text"],width=220)
            self._add_entry.pack(side="left",padx=(0,8))
            self._add_entry.bind("<Return>", lambda e: self._add_user_inline())
            ctk.CTkButton(add_bar,text="确认添加",width=80,height=28,corner_radius=5,
                          font=(FONT,FS["small"]),fg_color=C["accent"],
                          command=self._add_user_inline).pack(side="left")
            # 用户信息卡片滚动区
            self._info_scr=ctk.CTkScrollableFrame(f,fg_color=C["bg"])
            self._info_scr.pack(fill="both",expand=True,padx=10,pady=(2,6))
            return f

        def _mk_settings(self):
            f=ctk.CTkFrame(self._ct,fg_color=C["bg"])
            ctk.CTkLabel(f,text="全局设置",font=(FONT,FS["title"],"bold"),text_color=C["text"]).pack(anchor="w",padx=20,pady=(14,8))
            sf=ctk.CTkFrame(f,fg_color="transparent"); sf.pack(fill="x",padx=20)

            # 默认存档目录
            ctk.CTkLabel(sf,text="默认存档目录",font=(FONT,FS["body"]),text_color=C["text"]).pack(anchor="w")
            ctk.CTkLabel(sf,text="未设置独立目录时，所有抓取目标文档放在本目录",font=(FONT,FS["cap"]),text_color=C["text_sec"]).pack(anchor="w",pady=(0,4))
            pr=ctk.CTkFrame(sf,fg_color="transparent"); pr.pack(fill="x")
            default_path = self._gui_settings.default_archive_root or str(self._ar)
            self._set_pe=ctk.CTkEntry(pr,font=(FONT,FS["small"]),fg_color=C["input_bg"],border_width=0,text_color=C["text"])
            self._set_pe.insert(0,default_path); self._set_pe.pack(side="left",fill="x",expand=True)
            ctk.CTkButton(pr,text="浏览",width=60,height=30,font=(FONT,FS["small"]),fg_color=C["accent"],command=lambda:self._browse(self._set_pe)).pack(side="left",padx=(8,0))

            # 监控间隔
            ctk.CTkLabel(sf,text="默认监控间隔（秒）",font=(FONT,FS["body"]),text_color=C["text"]).pack(anchor="w",pady=(20,4))
            ctk.CTkLabel(sf,text="推荐 60~3600 秒，对所有监控中的对象同步生效",font=(FONT,FS["cap"]),text_color=C["text_sec"]).pack(anchor="w",pady=(0,4))
            self._set_ie=ctk.CTkEntry(sf,font=(FONT,FS["small"]),fg_color=C["input_bg"],border_width=0,text_color=C["text"])
            self._set_ie.insert(0,str(self._gui_settings.monitor_interval_seconds)); self._set_ie.pack(fill="x")

            ctk.CTkButton(sf,text="保存设置",width=100,height=32,font=(FONT,FS["small"]),fg_color=C["accent"],command=self._save_settings).pack(pady=(18,8))
            return f

        def _save_settings(self):
            """持久化全局设置到 gui_settings.json."""
            self._glog("INFO", "settings", f"保存设置 | interval={self._gui_settings.monitor_interval_seconds}s")
            try:
                new_path = self._set_pe.get().strip()
                if new_path:
                    self._gui_settings.default_archive_root = new_path
                new_interval = int(self._set_ie.get().strip())
                self._gui_settings.monitor_interval_seconds = GuiSettings._clamp_interval(new_interval)
            except ValueError:
                self._status.configure(text="间隔值无效，请输入整数")
                self._log("设置","保存失败: 间隔值无效")
                return
            self._gui_settings.save(self._ar)
            self._restart_monitor()  # 间隔可能已变更
            self._status.configure(text="设置已保存")
            self._log("设置",f"存档目录={self._gui_settings.default_archive_root or self._ar} | 间隔={self._gui_settings.monitor_interval_seconds}s")
        def _browse(self,entry):
            from tkinter import filedialog
            p=filedialog.askdirectory(title="选择存档目录")
            if p: entry.delete(0,"end"); entry.insert(0,p)

        def _mk_logs(self):
            f=ctk.CTkFrame(self._ct,fg_color=C["bg"])
            hf=ctk.CTkFrame(f,fg_color="transparent"); hf.pack(fill="x",padx=16,pady=(14,4))
            ctk.CTkLabel(hf,text="日志与错误信息",font=(FONT,FS["title"],"bold"),text_color=C["text"]).pack(side="left")
            self._log_list=ctk.CTkScrollableFrame(f,fg_color=C["bg"]); self._log_list.pack(fill="both",expand=True,padx=10,pady=(4,8))
            self._refresh_logs(); return f
        def _refresh_logs(self):
            if not hasattr(self,"_log_list") or not self._log_list.winfo_exists(): return
            for w in self._log_list.winfo_children(): w.destroy()
            for e in reversed(self._logs[-50:]):
                LogCard(self._log_list,e).pack(fill="x",pady=2)

        def _mk_dlogs(self):
            f=ctk.CTkFrame(self._ct,fg_color=C["bg"])
            hf=ctk.CTkFrame(f,fg_color="transparent"); hf.pack(fill="x",padx=16,pady=(14,4))
            ctk.CTkLabel(hf,text="详细日志（仅失败/异常）",font=(FONT,FS["title"],"bold"),text_color=C["text"]).pack(side="left")
            ctk.CTkButton(hf,text="清空",width=46,height=22,font=(FONT,10),fg_color=C["dark_gray"],
                         command=self._clear_dlogs).pack(side="right")
            self._dlog_list=ctk.CTkScrollableFrame(f,fg_color=C["bg"]); self._dlog_list.pack(fill="both",expand=True,padx=10,pady=(4,8))
            self._refresh_dlogs(); return f
        def _clear_dlogs(self):
            """清空详细日志（异常安全锁）."""
            with self._dlogs_lock:
                self._detail_logs.clear()
            self._refresh_dlogs()
        def _refresh_dlogs(self):
            if not hasattr(self,"_dlog_list") or not self._dlog_list.winfo_exists(): return
            for w in self._dlog_list.winfo_children(): w.destroy()
            if not self._detail_logs:
                ctk.CTkLabel(self._dlog_list,text="（暂无记录）",font=(FONT,FS["cap"]),text_color=C["text_sec"]).pack(pady=20)
            for e in reversed(self._detail_logs[-50:]):
                DetailLogCard(self._dlog_list,e).pack(fill="x",pady=2)

        def _detail_log(self, cat, uid, msg, detail="", raw_data="", env=""):
            t=datetime.now().strftime("%m-%d %H:%M:%S")
            cookie_status = "有效" if self._cookie else "未设置"
            entry = {"time":t,"cat":cat,"uid":uid,"msg":msg,"detail":detail or msg,
                     "raw_data":raw_data,"env":env,"cookie_status":cookie_status}
            with self._dlogs_lock:
                self._detail_logs.append(entry)
                if len(self._detail_logs)>100: self._detail_logs=self._detail_logs[-100:]
            ui(self._refresh_dlogs)

        def _nav(self,name):
            # 恢复上一页按钮颜色，高亮当前页
            prev = self._current_page
            if prev in self._nav_buttons and self._nav_buttons[prev].winfo_exists():
                self._nav_buttons[prev].configure(fg_color=C["card"])
            if name in self._nav_buttons and self._nav_buttons[name].winfo_exists():
                self._nav_buttons[name].configure(fg_color=C["hover"])
            self._current_page = name
            # 切换页面
            for p in self._pages.values(): p.pack_forget()
            self._pages[name].pack(fill="both",expand=True)
            if name == "logs": self._refresh_logs()
            if name == "dlogs": self._refresh_dlogs()
            if name == "users": self._refresh_info_cards()

        # ---- 用户管理 ----
        def _add_user_inline(self):
            """从用户管理页的输入框添加用户（内联，无弹窗）."""
            raw = self._add_entry.get().strip()
            if not raw:
                self._status.configure(text="请输入 UID 或链接")
                return
            m = re.search(r'(\d{7,11})', raw)
            uid = m.group(1) if m else raw.strip()
            if not uid:
                self._status.configure(text="无法识别 UID")
                return
            with self._users_lock:
                if uid in [u["uid"] for u in self._users]:
                    self._status.configure(text=f"用户 {uid} 已存在")
                    return
                self._users.append({"uid":uid,"screen_name":"","custom_name":"","monitoring":True,
                    "custom_limit_enabled":False,"max_cards":0,"custom_path_enabled":False,"_initial_crawl_done":False,
                    "archive_path":str(self._ar),"content_text":True,"content_images":True,
                    "content_videos":True,"post_count":0,"_crawl_state":"idle","_selected":False})
            self._save_users(); self._refresh()
            self._add_entry.delete(0, "end")
            self._log("用户", f"添加 {uid}")
            self._status.configure(text=f"已添加 {uid}")
            self._glog("INFO", "user", f"添加对象 | uid={uid}")
            threading.Thread(target=self._fetch_screen_name, args=(uid,), daemon=True).start()

        def _add_user(self):
            dlg=ctk.CTkToplevel(self); dlg.title("添加目标用户")
            dlg.attributes("-topmost",True); dlg.configure(fg_color=C["bg"])
            dlg.update_idletasks(); sw,sh=dlg.winfo_screenwidth(),dlg.winfo_screenheight()
            dlg.geometry(f"400x170+{(sw-400)//2}+{(sh-170)//2}")
            self._set_dlg_icon(dlg)
            dlg.grab_set()
            ctk.CTkLabel(dlg,text="输入微博用户 UID 或主页链接:",font=(FONT,FS["body"]),
                         text_color=C["text"]).pack(pady=(20,8))
            entry=ctk.CTkEntry(dlg,font=(FONT,FS["small"]),fg_color=C["input_bg"],border_width=0,text_color=C["text"])
            entry.pack(fill="x",padx=20,pady=(0,12))
            def _confirm():
                uid=entry.get().strip()
                if uid:
                    m=re.search(r'(\d{7,11})',uid); uid=m.group(1) if m else uid.strip()
                    if uid and uid not in [u["uid"] for u in self._users]:
                        self._users.append({"uid":uid,"screen_name":"","custom_name":"","monitoring":True,
                            "custom_limit_enabled":False,"max_cards":0,"custom_path_enabled":False,"_initial_crawl_done":False,
                            "archive_path":str(self._ar),"content_text":True,"content_images":True,
                            "content_videos":True,"post_count":0,"_crawl_state":"idle","_selected":False})
                        self._save_users(); self._refresh(); self._log("用户",f"添加 {uid}")
                        self._status.configure(text=f"已添加 {uid}")
                        self._glog("INFO", "user", f"添加对象 | uid={uid}")
                        threading.Thread(target=self._fetch_screen_name,args=(uid,),daemon=True).start()
                dlg.destroy()
            entry.bind("<Return>",lambda e:_confirm())
            ctk.CTkButton(dlg,text="确认",width=80,height=32,font=(FONT,FS["small"]),
                          fg_color=C["accent"],command=_confirm).pack()

        def _fetch_screen_name(self,uid):
            """后台获取用户微博名、头像、粉丝数、发博总量."""
            if not self._cookie: return
            try:
                import asyncio,httpx
                async def f():
                    async with httpx.AsyncClient(timeout=10) as c:
                        c.headers.update({"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15","Accept":"application/json","X-Requested-With":"XMLHttpRequest"})
                        for it in self._cookie.split(";"):
                            if "=" in it.strip(): k,v=it.strip().split("=",1); c.cookies.set(k.strip(),v.strip(),domain=".weibo.cn")
                        r0=await c.get("https://m.weibo.cn/api/config"); st=r0.json()["data"]["st"]; c.headers["X-XSRF-TOKEN"]=st
                        # 用户信息 API（含粉丝数、关注数、头像大图）
                        r=await c.get("https://m.weibo.cn/api/container/getIndex",params={"type":"uid","value":uid,"containerid":f"100505{uid}"})
                        ui_data=r.json().get("data",{}).get("userInfo",{})
                        if ui_data:
                            sc=ui_data.get("statuses_count",0)
                            if isinstance(sc,str):
                                try: sc=int(float(sc.replace("万","0000").replace("亿","00000000")))
                                except: sc=0
                            fc=ui_data.get("followers_count",0)
                            if isinstance(fc,str):
                                try: fc=int(float(fc.replace("万","0000").replace("亿","00000000")))
                                except: fc=0
                            av=ui_data.get("avatar_hd","") or ui_data.get("avatar_large","") or ui_data.get("profile_image_url","")
                            sn=ui_data.get("screen_name","")
                            return sn,av,int(sc) if sc else 0,int(fc) if fc else 0
                        # 回退：时间线 API
                        r2=await c.get("https://m.weibo.cn/api/container/getIndex",params={"type":"uid","value":uid,"containerid":f"107603{uid}","page":"1"})
                        cards=r2.json().get("data",{}).get("cards",[])
                        for card in cards:
                            mblog=card.get("mblog",card)
                            u=mblog.get("user",{})
                            if u.get("screen_name"):
                                sc2=u.get("statuses_count",0)
                                if isinstance(sc2,str):
                                    try: sc2=int(float(sc2.replace("万","0000").replace("亿","00000000")))
                                    except: sc2=0
                                return u.get("screen_name",""), u.get("profile_image_url",""), int(sc2) if sc2 else 0,0
                        return None,None,0,0
                loop=asyncio.new_event_loop(); sn,av,sc,fc=loop.run_until_complete(f()); loop.close()
                if sn:
                    with self._users_lock:
                        for u in self._users:
                            if u["uid"]==uid:
                                old_sn=u.get("screen_name","")
                                if old_sn and old_sn!=sn:
                                    self._log("变更",f"{old_sn} → {sn}")
                                u["screen_name"]=sn
                                if av: u["avatar_url"]=av
                                if sc: u["statuses_count"]=sc
                                if fc: u["followers_count"]=fc
                    self._save_users(); ui(self._refresh)
            except Exception:
                pass

        def remove_user(self,uid,delete_data=False):
            """移除用户，可选择删除抓取数据（使用有效存档路径）."""
            with self._users_lock:
                ucfg = next((u for u in self._users if u["uid"]==uid), None)
                self._users=[u for u in self._users if u["uid"]!=uid]
            if delete_data:
                import shutil
                if ucfg:  # ucfg 在锁内获取
                    root = self._effective_archive(ucfg)
                    for d in (root/"users").glob(f"*_{uid}"): shutil.rmtree(d,ignore_errors=True)
            self._save_users(); self._glog("INFO", "user", f"移除对象 | uid={uid}"); self._refresh()
            self._log("用户",f"移除 {uid}" + ("(含数据)" if delete_data else ""))

        def _del_sel(self):
            selected=[c for c in self._cards if c.selected]
            if not selected: return
            # 确认对话框
            dlg=ctk.CTkToplevel(self); dlg.title("确认删除"); dlg.geometry("380x180")
            dlg.attributes('-topmost',True); dlg.configure(fg_color=C["card"])
            self._set_dlg_icon(dlg)
            ctk.CTkLabel(dlg,text=f"确认删除 {len(selected)} 个目标用户？",font=(FONT,FS["body"]),text_color=C["text"]).pack(pady=(16,8))
            del_data=ctk.BooleanVar(value=False)
            ctk.CTkCheckBox(dlg,text="将目标抓取内容一并删除",variable=del_data,width=20,height=20,border_width=2,font=(FONT,FS["small"]),fg_color=C["danger"]).pack(pady=(0,12))
            bf=ctk.CTkFrame(dlg,fg_color="transparent"); bf.pack()
            ctk.CTkButton(bf,text="确认删除",font=(FONT,FS["small"]),fg_color=C["danger"],command=lambda:[dlg.destroy(),self._do_del([c.uid for c in selected],del_data.get())]).pack(side="left",padx=(0,8))
            ctk.CTkButton(bf,text="取消",font=(FONT,FS["small"]),fg_color=C["dark_gray"],command=dlg.destroy).pack(side="left")

        def _do_del(self,uids,del_data):
            for uid in uids: self.remove_user(uid,del_data)

        def _move(self,idx,d):
            n=idx+d
            if 0<=n<len(self._users): self._users[idx],self._users[n]=self._users[n],self._users[idx]; self._save_users(); self._refresh()
        def _on_sel(self,card):
            card._u["_selected"]=card._cb_var.get()

        def _refresh(self):
            """完全重建卡片（仅在增删改用户列表时使用）."""
            for w in self._scr.winfo_children(): w.destroy()
            self._cards.clear()
            with self._users_lock:
                users_snapshot = list(enumerate(self._users))
            if not users_snapshot:
                ef=ctk.CTkFrame(self._scr,fg_color="transparent"); ef.place(relx=0.5,rely=0.5,anchor="center")
                ctk.CTkLabel(ef,text="暂无目标用户",font=(FONT,FS["sub"]),text_color=C["text_sec"]).pack()
                ctk.CTkLabel(ef,text="在「用户管理」页面添加",font=(FONT,FS["body"]),text_color=C["text_sec"]).pack()
            else:
                for i,u in users_snapshot:
                    c=UserCard(self._scr,u,self,i,self._move,self._on_sel); c.pack(fill="x",pady=3); self._cards.append(c)
            # 同步刷新信息卡片
            self._refresh_info_cards()

        def _update_cards(self):
            """仅刷新卡片显示文字（不重建控件，消除闪烁）."""
            for c in self._cards:
                try:
                    c.update_display()
                except Exception:
                    pass
            # 信息卡片数据变化频繁，完全重建
            self._refresh_info_cards()

        def _load_users(self):
            p=self._ar/"config"/"users.json"
            if p.exists():
                try: self._users=json.loads(p.read_text(encoding="utf-8"))
                except: self._users=[]
            # 重置上次退出时残留的运行状态
            for u in self._users:
                if u.get("_crawl_state")=="running":
                    u["_crawl_state"]="idle"
            self._refresh()
        def _save_users(self):
            """原子写入 users.json（先写临时文件再 rename，防并发损坏）."""
            p=self._ar/"config"; p.mkdir(parents=True,exist_ok=True)
            import tempfile
            tmp_path = None
            with self._users_lock:
                snapshot = json.dumps(self._users, ensure_ascii=False, indent=2)
            try:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False,
                                                 dir=str(p), encoding='utf-8') as tmp:
                    tmp.write(snapshot)
                    tmp_path = tmp.name
                Path(tmp_path).replace(p/"users.json")
            except Exception:
                if tmp_path:
                    try: Path(tmp_path).unlink(missing_ok=True)
                    except: pass
                (p/"users.json").write_text(snapshot, encoding="utf-8")
            self._recalc_interval()

        # ---- 全局操作 ----
        def _crawl_sel(self):
            for c in self._cards:
                if c.selected: self.run_crawl(c.uid)

        def _pause_sel(self):
            with self._users_lock:
                for c in self._cards:
                    if c.selected:
                        c._u["monitoring"]=False
                        c._u["_crawl_state"]="idle"
            for c in self._cards:
                if c.selected:
                    c._rebuild()
            self._save_users()

        def _pause_all(self):
            """Phase 4: 暂停所有用户的监控."""
            for u in self._users:
                u["monitoring"]=False
                u["_crawl_state"]="idle"
            self._save_users()
            self._refresh()
            self._log("系统","所有监控已暂停")

        # ---- 后台定时监测 ----
        def _start_monitor(self):
            """启动后台定时器，按设定间隔对所有监控中的用户执行增量抓取."""
            self._monitor_id = self.after(
                self._gui_settings.monitor_interval_seconds * 1000,
                self._monitor_tick
            )

        def _restart_monitor(self):
            """设置变更后重启监测定时器."""
            if hasattr(self, '_monitor_id') and self._monitor_id is not None:
                self.after_cancel(self._monitor_id)
            self._start_monitor()

        def _monitor_tick(self):
            """定时器触发：对每个监控中的用户独立排队抓取（信号量串行执行）."""
            try:
                # 非阻塞获取锁，避免因后台线程持有锁而卡住 UI
                if not self._crawl_lock.acquire(blocking=False):
                    return
                try:
                    crawling = set(self._crawling_uids)
                finally:
                    self._crawl_lock.release()
                if not self._users_lock.acquire(blocking=False):
                    return
                try:
                    monitored = [u for u in self._users
                                 if u.get("monitoring", True)
                                 and u.get("_initial_crawl_done", False)
                                 and u["uid"] not in crawling]
                finally:
                    self._users_lock.release()
                for u in monitored:
                    self._log("监测", f"排队检查: {u.get('screen_name', u['uid'])}")
                    self.run_crawl(u["uid"])
            except Exception:
                pass
            finally:
                self._monitor_id = self.after(
                    self._gui_settings.monitor_interval_seconds * 1000,
                    self._monitor_tick
                )

        def _set_user_mark(self, uid, suffix, keep_monitoring):
            """标记用户特殊状态（已注销/私密/错误）."""
            with self._users_lock:
                for u in self._users:
                    if u["uid"]==uid:
                        u["_crawl_state"]="done"
                        if not keep_monitoring:
                            u["monitoring"]=False
                        sn = u.get("screen_name","")
                        if suffix not in sn:
                            u["screen_name"]=f"{sn}({suffix})" if sn else f"user_{uid}({suffix})"
            self._save_users()
            ui(self._update_cards)
            ui(lambda:self._log("抓取",f"{uid}: {suffix}"))
            ui(lambda:self._status.configure(text=suffix))

        def _effective_archive(self, ucfg: dict | None = None) -> Path:
            """计算有效存档目录：用户独立目录 > 全局设置 > 默认."""
            if ucfg and ucfg.get("custom_path_enabled") and ucfg.get("archive_path"):
                return Path(ucfg["archive_path"])
            if self._gui_settings.default_archive_root:
                return Path(self._gui_settings.default_archive_root)
            return self._ar

        # ---- 磁盘博文计数器（文件系统为唯一真实来源） ----
        def _disk_post_count(self, uid: str, archive_root: Path | None = None) -> int:
            """遍历本地存档目录统计实际已保存的博文数。

            目录结构: users/{name}_{uid}/posts/{year}/{month}/{bid_or_date}/
            每个含 post.json 的末级目录计为 1 篇（排除 versions/ 和 resource/）。
            此方法不依赖任何内存状态，永远返回磁盘真实数量。

            Args:
                uid: 用户 UID
                archive_root: 存档根目录（可选，后台线程调用时必须传入以避免锁竞争）
            """
            if archive_root is None:
                # 主线程场景：安全读取 self._users（调用方已持锁）
                ucfg = next((u for u in self._users if u["uid"] == uid), None)
                archive_root = self._effective_archive(ucfg)
            user_dir = archive_root / "users"
            if not user_dir.exists():
                return 0
            try:
                for ud in user_dir.iterdir():
                    if not ud.is_dir():
                        continue
                    if f"_{uid}" not in ud.name:
                        continue
                    posts_dir = ud / "posts"
                    if not posts_dir.exists():
                        return 0
                    count = 0
                    for year_dir in posts_dir.iterdir():
                        if not year_dir.is_dir():
                            continue
                        for month_dir in year_dir.iterdir():
                            if not month_dir.is_dir():
                                continue
                            for bid_dir in month_dir.iterdir():
                                if not bid_dir.is_dir():
                                    continue
                                if bid_dir.name in ("resource", "versions", "__pycache__"):
                                    continue
                                if (bid_dir / "post.json").exists():
                                    count += 1
                    return count
            except OSError:
                pass
            return 0

        # ---- 用户内容统计（图片/视频数 + 文件夹大小，不含 resource/） ----
        def _get_user_disk_stats(self, uid: str, archive_root: Path | None = None):
            """返回 (图片数, 视频数, 文件夹总字节数)，排除 resource/ 和 versions/."""
            if archive_root is None:
                # 主线程持锁时调用，安全读取
                ucfg = next((u for u in self._users if u["uid"] == uid), None)
                archive_root = self._effective_archive(ucfg)
            user_dir = archive_root / "users"
            img_n = vid_n = total_size = 0
            if not user_dir.exists():
                return (0, 0, 0)
            try:
                for ud in user_dir.iterdir():
                    if not ud.is_dir() or f"_{uid}" not in ud.name:
                        continue
                    for item in ud.rglob("*"):
                        if not item.is_file():
                            continue
                        pstr = str(item)
                        # 排除 resource/、versions/ 和 __pycache__/
                        if "/resource/" in pstr or "\\resource\\" in pstr:
                            continue
                        if "/versions/" in pstr or "\\versions\\" in pstr:
                            continue
                        if "/__pycache__/" in pstr or "\\__pycache__\\" in pstr:
                            continue
                        total_size += item.stat().st_size
                        parent = item.parent.name
                        if parent == "images":
                            img_n += 1
                        elif parent == "videos":
                            vid_n += 1
                    break  # 只处理第一个匹配的用户目录
            except OSError:
                pass
            return (img_n, vid_n, total_size)

        def _refresh_info_cards(self):
            """重建用户管理页的信息卡片（仅在页面可见时执行，避免无效重建）."""
            if not hasattr(self, "_info_scr") or not self._info_scr.winfo_exists():
                return
            # 仅在用户管理页可见时重建（节省 I/O）
            if self._current_page != "users":
                return
            for w in self._info_scr.winfo_children():
                w.destroy()
            with self._users_lock:
                users_snapshot = list(self._users)
            if not users_snapshot:
                ef = ctk.CTkFrame(self._info_scr, fg_color="transparent")
                ef.pack(pady=40)
                ctk.CTkLabel(ef, text="暂无目标用户", font=(FONT, FS["sub"]),
                            text_color=C["text_sec"]).pack()
                return
            for u in users_snapshot:
                UserInfoCard(self._info_scr, u, self).pack(fill="x", pady=3)

        def _sync_disk_counts(self):
            """同步所有用户的 post_count 到磁盘实际值（仅当锁可立即获取时执行）."""
            if not self._users_lock.acquire(blocking=False):
                return
            try:
                changed = False
                for u in self._users:
                    if not u.get("_initial_crawl_done", False):
                        continue
                    disk_n = self._disk_post_count(u["uid"])
                    if disk_n > 0 and u.get("post_count", 0) != disk_n:
                        u["post_count"] = disk_n
                        changed = True
                if changed:
                    self._save_users()
                    ui(self._update_cards)
            finally:
                self._users_lock.release()

        # ---- 抓取（直接调 run_full_crawl.py 子进程，已验证可靠） ----
        def run_crawl(self,uid):
            with self._crawl_lock:
                if uid in self._crawling_uids: return
                self._crawling_uids.add(uid)
            if not self._cookie:
                self._status.configure(text="请先设置 Cookie")
                self._log("抓取错误","缺少 Cookie，请先设置")
                with self._crawl_lock:
                    self._crawling_uids.discard(uid)
                return
            # 查找用户配置用于状态更新
            with self._users_lock:
                ucfg=next((u for u in self._users if u["uid"]==uid),None)
                if ucfg:
                    ucfg["_crawl_state"]="running"
                    ucfg["_crawl_started"] = time.time()
                    # 不修改 monitoring——手动抓取和自动监测是两个独立开关
            self._update_cards(); self._log("抓取",f"开始 {uid}")
            self._status.configure(text=f"抓取 {uid}...")
            self._glog("INFO", "crawl", f"开始抓取 | uid={uid}")
            threading.Thread(target=self._crawl_subprocess,args=(uid,),daemon=True).start()

        def _crawl_subprocess(self,uid):
            saved=0; sn=""; used_api=0
            try:
                with self._users_lock:
                    ucfg=next((u for u in self._users if u["uid"]==uid),None)
                    # 快照配置（避免后续无锁访问修改中的 dict）
                    ucfg_snapshot = dict(ucfg) if ucfg else None
                ucfg = ucfg_snapshot
                # 勾选上限 → 用自定义数值；未勾选 → 0 表示全抓不限量
                if ucfg and ucfg.get("custom_limit_enabled"):
                    max_c = str(ucfg.get("max_cards", 0) or 0)
                else:
                    max_c = "0"  # 0 = 不限量，抓取全部可见博文

                env=os.environ.copy(); env["PYTHONIOENCODING"]="utf-8"
                env["WEIBO_COOKIE_STRING"]=self._cookie
                env["WEIBO_TARGET_UID"]=uid
                env["WEIBO_MAX_CARDS"]=max_c
                # 存档目录优先级：用户独立目录 > 全局设置 > 默认
                env["WEIBO_ARCHIVE_ROOT"]=str(self._effective_archive(ucfg))

                # Phase 3: 内容类型过滤
                if ucfg:
                    env["WEIBO_CONTENT_TEXT"]="1" if ucfg.get("content_text",True) else "0"
                    env["WEIBO_CONTENT_IMAGES"]="1" if ucfg.get("content_images",True) else "0"
                    env["WEIBO_CONTENT_VIDEOS"]="1" if ucfg.get("content_videos",True) else "0"

                    # Phase 8: 补采检测
                    newly_enabled=[]
                    for ct,key in [("images","_crawled_images"),("videos","_crawled_videos")]:
                        if ucfg.get(f"content_{ct}",True) and not ucfg.get(key,True):
                            newly_enabled.append(ct)
                    if newly_enabled:
                        env["WEIBO_BACKFILL"]="1"
                        env["WEIBO_BACKFILL_TYPES"]=",".join(newly_enabled)
                        env["WEIBO_BACKFILL_MAX_POSTS"]="50"
                        self._log("补采",f"{uid}: 补充 {','.join(newly_enabled)}")

                # === 抓取执行 ===
                if getattr(sys,'frozen',False):
                    # exe 模式：进程内执行（子进程 stdout 不可靠）
                    output = self._crawl_inprocess(env)
                else:
                    # 源码模式：子进程隔离执行
                    script=Path(__file__).parent.parent.parent/"run_full_crawl.py"
                    proc=subprocess.run(
                        [sys.executable,str(script)],
                        capture_output=True,text=True,timeout=300,
                        cwd=str(script.parent),env=env,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0,
                    )
                    output=proc.stdout+proc.stderr

                # 从输出解析结果
                import re
                m=re.search(r'新增:\s*(\d+)',output)
                if m: saved=int(m.group(1))
                m2=re.search(r'编辑:\s*(\d+)',output)
                if m2: saved+=int(m2.group(1))
                # Phase 5: API 计数
                m3=re.search(r'\[计数\]\s*(?:最终:?\s*)?已用\s*(\d+)/(\d+)',output)
                if m3:
                    used_api=int(m3.group(1))
                    # 整点重置，小时内累加
                    now_hour = datetime.now().hour
                    if self._api_last_hour != now_hour:
                        self._api_hourly_count = 0
                        self._api_last_hour = now_hour
                    self._api_hourly_count += used_api
                    total = self._api_hourly_count
                    ui(lambda t=total: self._api_counter.configure(text=f"API: {t}/1000"))
                # 匹配用户名（必须精确匹配 "[OK] XXX | 粉丝:" 行，排除 "[OK] 登录有效"）
                m4=re.search(r'\[OK\]\s+(\S+)\s+\|\s*粉丝',output)
                if m4: sn=m4.group(1)

                # 环境摘要（用于详细日志）
                env_summary = f"MAX_CARDS={max_c} ROOT={self._effective_archive(ucfg)}"
                if ucfg:
                    env_summary += f" TEXT={ucfg.get('content_text',True)} IMG={ucfg.get('content_images',True)} VID={ucfg.get('content_videos',True)}"

                # 检测特殊输出信号
                if re.search(r'\[DEACTIVATED\]', output):
                    self._detail_log("致命",uid,"账号已注销",output,env=env_summary)
                    self._set_user_mark(uid, "已注销", False)
                    return
                if re.search(r'\[PRIVATE\]', output):
                    self._detail_log("异常",uid,"私密账号",output,env=env_summary)
                    self._set_user_mark(uid, "私密账号", True)
                    return
                if re.search(r'\[FATAL\]|Cookie.*失效|COOKIE_EXPIRED', output):
                    self._detail_log("Cookie失效",uid,"Cookie失效或致命错误",output[:2000],env=env_summary)
                    self._set_user_mark(uid, "错误", False)
                    self._glog("ERROR", "crawl", f"Cookie失效 | uid={uid}")
                    ui(lambda:self._log("抓取错误",f"{uid}: Cookie失效或致命错误"))
                    return
                if re.search(r'\[WARN\].*API.*异常', output):
                    self._detail_log("API异常",uid,"API结构异常，部分卡片为空",output[:2000],env=env_summary)
                    ui(lambda:self._log("抓取",f"{uid}: 检测到API结构异常，部分卡片内容为空"))
                if re.search(r'\[限流\]', output):
                    ui(lambda:self._log("抓取",f"{uid}: API频率限制触发，已自动等待恢复"))
                    ui(lambda:self._status.configure(text=f"{uid}: 限流等待中..."))

                self._log("抓取输出",output.splitlines()[-1] if output.splitlines() else "无输出")

                if sn:
                    with self._users_lock:
                        for u in self._users:
                            if u["uid"]==uid:
                                old=u.get("screen_name","")
                                if old and old!=sn: self._log("变更",f"{old} → {sn}")
                                u["screen_name"]=sn; break
                    self._save_users()

                # 以磁盘实际文件数为准（文件系统是唯一真实来源）
                disk_total = self._disk_post_count(uid, self._effective_archive(ucfg))

                with self._users_lock:
                    for u in self._users:
                        if u["uid"]==uid:
                            u["_crawl_state"]="done"
                            u["_initial_crawl_done"]=True  # 首次手动抓取后解锁监测
                            u["post_count"]=disk_total
                            if ucfg:
                                u["_crawled_text"]=ucfg.get("content_text",True)
                                u["_crawled_images"]=ucfg.get("content_images",True)
                                u["_crawled_videos"]=ucfg.get("content_videos",True)
                self._save_users()
                self._glog("INFO", "crawl", f"抓取完成 | uid={uid} | new={saved} | disk_total={disk_total}")
                ui(self._update_cards); ui(lambda s=saved: self._log("抓取",f"{uid} 完成: +{s}篇"))
                ui(lambda s=saved: self._status.configure(text=f"完成: +{s}篇"))
            except subprocess.TimeoutExpired:
                self._glog("ERROR", "crawl", f"抓取超时 | uid={uid}")
                env_summary2 = f"MAX_CARDS={max_c} ROOT={self._effective_archive(ucfg)}"
                self._detail_log("超时", uid, "抓取超时(300s)", "", env=env_summary2)
                ui(lambda:self._log("抓取错误",f"{uid}: 超时"))
                ui(lambda:self._status.configure(text="超时"))
                with self._users_lock:
                    for u in self._users:
                        if u["uid"]==uid: u["_crawl_state"]="idle"; break
                ui(self._update_cards)
            except Exception as e:
                import traceback
                tb=traceback.format_exc()
                _err_msg = str(e)[:200]
                env_summary3 = f"MAX_CARDS={max_c} ROOT={self._effective_archive(ucfg)}"
                self._detail_log("异常", uid, _err_msg, tb[:3000], env=env_summary3)
                self._glog("ERROR", "crawl", f"抓取异常 | uid={uid} | {_err_msg}")
                ui(lambda _e=_err_msg, _t=tb: self._log("抓取错误", f"{uid}: {_e}", _t))
                ui(lambda: self._status.configure(text="失败"))
                # 磁盘文件可能已写入，从实际文件数兜底更新 post_count
                disk_count = self._disk_post_count(uid, self._effective_archive(ucfg))
                with self._users_lock:
                    for u in self._users:
                        if u["uid"]==uid:
                            u["_crawl_state"]="done"
                            if disk_count > 0:
                                u["post_count"]=disk_count
                            break
                self._save_users()
                ui(self._update_cards)
            finally:
                with self._crawl_lock:
                    self._crawling_uids.discard(uid)

        def _crawl_inprocess(self, env: dict) -> str:
            """exe 模式：进程内串行执行（信号量保护，避免并发竞态）."""
            import io, runpy
            # 获取信号量——阻塞等待直到上一个抓取完成
            acquired = self._crawl_sem.acquire(timeout=300)
            if not acquired:
                raise RuntimeError("[TIMEOUT] 等待上一个抓取完成超时")
            try:
                # 设置环境变量（串行执行，无竞态）
                for k, v in env.items():
                    if k != 'PYTHONIOENCODING':
                        os.environ[k] = v
                # 重定向 stdout
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                old_argv = sys.argv
                try:
                    crawl_path = Path(sys._MEIPASS) / "run_full_crawl.py"
                    sys.argv = [str(crawl_path)]
                    if sys.platform == "win32":
                        import asyncio as aio
                        aio.set_event_loop_policy(aio.WindowsSelectorEventLoopPolicy())
                    runpy.run_path(str(crawl_path), run_name="__main__")
                except SystemExit:
                    pass
                finally:
                    output = sys.stdout.getvalue()
                    sys.stdout = old_stdout
                    sys.argv = old_argv
                return output
            finally:
                self._crawl_sem.release()

        def _poll(self):
            try:
                self._poll_body()
            except Exception:
                # 任何异常都不能中断 _poll 循环，否则 UI 永久冻结
                try:
                    (Path(self._ar)/"ui_errors.log").write_text(
                        f"[{datetime.now().isoformat()}] _poll crashed: \n", encoding="utf-8")
                except: pass
            finally:
                self.after(100, self._poll)

        def _poll_body(self):
            try:
                while True:
                    fn = _uiq.get_nowait()
                    try: fn()
                    except Exception as _e:
                        try:
                            (Path(self._ar)/"ui_errors.log").write_text(
                                f"[{datetime.now().isoformat()}] ui callback failed: {_e}\n", encoding="utf-8")
                        except: pass
            except queue.Empty: pass
            # 整点清零 API 计数器
            now_hour = datetime.now().hour
            if self._api_last_hour >= 0 and self._api_last_hour != now_hour:
                self._api_hourly_count = 0
                if hasattr(self, '_api_counter') and self._api_counter.winfo_exists():
                    self._api_counter.configure(text="API: 0/1000")
            if self._api_last_hour < 0:
                self._api_last_hour = now_hour
            # 看门狗：非阻塞检查，避免因 _users_lock 被其他线程持有而卡住 _poll
            stuck = []
            if self._users_lock.acquire(blocking=False):
                try:
                    for u in self._users:
                        if u.get("_crawl_state") == "running":
                            started = u.get("_crawl_started", 0)
                            if started and time.time() - started > 600:
                                u["_crawl_state"] = "idle"
                                stuck.append(u["uid"])
                finally:
                    self._users_lock.release()
            for uid in stuck:
                self._log("抓取错误", f"{uid}: 状态超时自动复位")
            if stuck:
                with self._crawl_lock:
                    for uid in stuck:
                        self._crawling_uids.discard(uid)
            # 持续同步磁盘博文计数（每 3 秒检查一次，仅当锁空闲时执行）
            self._poll_tick = getattr(self, '_poll_tick', 0) + 1
            if self._poll_tick % 30 == 0:
                self._sync_disk_counts()
            if self._poll_tick % 10 == 0:
                try: self._status.configure(text=self._status.cget("text").split(" |")[0])
                except: pass

        def _close(self):
            if hasattr(self, '_monitor_id') and self._monitor_id is not None:
                self.after_cancel(self._monitor_id)
            self._glog("INFO", "app", "应用关闭")
            self._save_users(); self.destroy()
            # 确保无残留：daemon 线程随主线程退出，无子进程需要清理
            os._exit(0)


def _ym2(created_at:str)->str:
    from weibo_saver.storage.layout import Layout
    dt=Layout._parse_created_at(created_at)
    return dt.strftime("%Y/%m") if dt else "unknown"

def run_gui(archive_root:Path, log_path:Path=None):
    app=WeiboSaverGUI(archive_root, log_path); app.mainloop(); return 0
