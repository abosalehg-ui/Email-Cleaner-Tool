#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║                  🧹 Email Cleaner Tool 🧹                        ║
║          أداة تنظيف البريد الإلكتروني من الرسائل الدعائية          ║
╠══════════════════════════════════════════════════════════════════╣
║  تطوير: عبدالكريم العبود | abo.saleh.g@gmail.com                 ║
║  GitHub: https://github.com/abosalehg-ui                         ║
║  © 2026 [Email_Cleaner] - All Rights Reserved                    ║
╚══════════════════════════════════════════════════════════════════╝
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import imaplib
import email
from email.header import decode_header
import re
import threading
from datetime import datetime, timedelta
from collections import defaultdict
import json
import webbrowser
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import time

# محاولة استيراد requests
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# معلومات البرنامج
__title__ = "Email Cleaner"
__version__ = "1.0.0"
__author__ = "عبدالكريم العبود"
__email__ = "abo.saleh.g@gmail.com"
__github__ = "https://github.com/abosalehg-ui"
__copyright__ = "© 2026 [Email_Cleaner] - All Rights Reserved"


@dataclass
class EmailMessage:
    """تمثيل رسالة بريد إلكتروني"""
    uid: str
    subject: str
    sender: str
    sender_email: str
    date: str
    unsubscribe_link: Optional[str]
    unsubscribe_email: Optional[str]
    is_promotional: bool
    folder: str


class EmailCleanerCore:
    """المحرك الأساسي لتنظيف البريد"""
    
    IMAP_SERVERS = {
        'gmail.com': ('imap.gmail.com', 993),
        'outlook.com': ('outlook.office365.com', 993),
        'hotmail.com': ('outlook.office365.com', 993),
        'live.com': ('outlook.office365.com', 993),
        'yahoo.com': ('imap.mail.yahoo.com', 993),
        'icloud.com': ('imap.mail.me.com', 993),
        'me.com': ('imap.mail.me.com', 993),
    }
    
    PROMOTIONAL_KEYWORDS = [
        'unsubscribe', 'newsletter', 'promotional', 'marketing',
        'sale', 'discount', 'offer', 'deal', 'promo', 'subscribe',
        'opt-out', 'opt out', 'weekly digest', 'daily digest',
        'notification settings', 'email preferences', 'manage subscriptions',
        'limited time', 'exclusive', 'free shipping', 'buy now',
        'إلغاء الاشتراك', 'نشرة إخبارية', 'عرض خاص', 'تخفيضات',
        'خصم', 'عروض', 'اشترك', 'تسوق الآن', 'لفترة محدودة',
    ]
    
    TRUSTED_PATTERNS = [
        'noreply@google.com', 'security@', 'account@', 'support@',
        'billing@', 'invoice@', 'receipt@', 'order@', 'shipping@',
    ]
    
    def __init__(self):
        self.connection: Optional[imaplib.IMAP4_SSL] = None
        self.messages: List[EmailMessage] = []
        self.stats = defaultdict(int)
        self.unsubscribe_results = {}
        
    def get_server_info(self, email_address: str) -> Tuple[str, int]:
        """الحصول على معلومات الخادم"""
        domain = email_address.split('@')[1].lower()
        return self.IMAP_SERVERS.get(domain, (f'imap.{domain}', 993))
    
    def connect(self, email_address: str, password: str) -> Tuple[bool, str]:
        """الاتصال بالبريد"""
        try:
            server, port = self.get_server_info(email_address)
            self.connection = imaplib.IMAP4_SSL(server, port)
            self.connection.login(email_address, password)
            return True, "تم الاتصال بنجاح ✅"
        except imaplib.IMAP4.error as e:
            return False, f"خطأ في تسجيل الدخول: {str(e)}"
        except Exception as e:
            return False, f"خطأ في الاتصال: {str(e)}"
    
    def disconnect(self):
        """قطع الاتصال"""
        if self.connection:
            try:
                self.connection.logout()
            except:
                pass
            self.connection = None
    
    def _decode_header_value(self, value) -> str:
        """فك تشفير العنوان"""
        if not value:
            return ""
        decoded_parts = decode_header(value)
        result = []
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                try:
                    result.append(part.decode(encoding or 'utf-8', errors='replace'))
                except:
                    result.append(part.decode('utf-8', errors='replace'))
            else:
                result.append(part)
        return ' '.join(result)
    
    def _extract_email_address(self, from_header: str) -> Tuple[str, str]:
        """استخراج البريد والاسم"""
        if not from_header:
            return "", ""
        match = re.search(r'<([^>]+)>', from_header)
        if match:
            email_addr = match.group(1)
            name = from_header.replace(f'<{email_addr}>', '').strip(' "\'')
        else:
            email_addr = from_header.strip()
            name = email_addr.split('@')[0]
        return self._decode_header_value(name), email_addr.lower()
    
    def _extract_unsubscribe_link(self, msg) -> Optional[str]:
        """استخراج رابط إلغاء الاشتراك"""
        list_unsub = msg.get('List-Unsubscribe', '')
        if list_unsub:
            match = re.search(r'<(https?://[^>]+)>', list_unsub)
            if match:
                return match.group(1)
        return None
    
    def _is_promotional(self, msg, subject: str, sender_email: str) -> bool:
        """تحديد إذا كانت الرسالة دعائية"""
        for trusted in self.TRUSTED_PATTERNS:
            if trusted in sender_email:
                return False
        
        if msg.get('Precedence', '').lower() in ['bulk', 'list', 'junk']:
            return True
        if msg.get('List-Unsubscribe'):
            return True
        
        text = f"{subject} {sender_email}".lower()
        for keyword in self.PROMOTIONAL_KEYWORDS:
            if keyword.lower() in text:
                return True
        return False
    
    def scan_inbox(self, days_back: int = 30, limit: int = 500, 
                   callback=None) -> List[EmailMessage]:
        """فحص صندوق الوارد"""
        if not self.connection:
            return []
        
        self.messages.clear()
        self.stats.clear()
        
        try:
            self.connection.select('INBOX')
            since_date = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')
            _, message_ids = self.connection.search(None, f'(SINCE "{since_date}")')
            
            ids = message_ids[0].split()
            total = min(len(ids), limit)
            
            if callback:
                callback(f"جاري فحص {total} رسالة...", 0)
            
            for i, msg_id in enumerate(ids[-limit:], 1):
                try:
                    _, msg_data = self.connection.fetch(msg_id, '(RFC822)')
                    if msg_data[0] is None:
                        continue
                    
                    msg = email.message_from_bytes(msg_data[0][1])
                    subject = self._decode_header_value(msg.get('Subject', ''))
                    sender_name, sender_email = self._extract_email_address(msg.get('From', ''))
                    
                    if self._is_promotional(msg, subject, sender_email):
                        unsub_link = self._extract_unsubscribe_link(msg)
                        
                        email_msg = EmailMessage(
                            uid=msg_id.decode(),
                            subject=subject[:80] if subject else "(بدون عنوان)",
                            sender=sender_name,
                            sender_email=sender_email,
                            date=msg.get('Date', ''),
                            unsubscribe_link=unsub_link,
                            unsubscribe_email=None,
                            is_promotional=True,
                            folder='INBOX'
                        )
                        self.messages.append(email_msg)
                        self.stats[sender_email] += 1
                    
                    if callback and i % 20 == 0:
                        progress = int((i / total) * 100)
                        callback(f"تم فحص {i}/{total} رسالة ({len(self.messages)} دعائية)", progress)
                        
                except Exception:
                    continue
            
            if callback:
                callback(f"اكتمل الفحص: {len(self.messages)} رسالة دعائية", 100)
            
            return self.messages
            
        except Exception as e:
            if callback:
                callback(f"خطأ: {str(e)}", 0)
            return []
    
    def delete_messages(self, messages: List[EmailMessage] = None) -> Tuple[int, str]:
        """حذف الرسائل"""
        if not self.connection:
            return 0, "غير متصل"
        
        to_delete = messages or self.messages
        if not to_delete:
            return 0, "لا توجد رسائل للحذف"
        
        try:
            self.connection.select('INBOX')
            deleted = 0
            
            for msg in to_delete:
                try:
                    self.connection.store(msg.uid.encode(), '+FLAGS', '\\Deleted')
                    deleted += 1
                except:
                    pass
            
            self.connection.expunge()
            return deleted, f"تم حذف {deleted} رسالة بنجاح ✅"
            
        except Exception as e:
            return 0, f"خطأ في الحذف: {str(e)}"
    
    def get_senders_summary(self) -> Dict[str, int]:
        """ملخص المرسلين"""
        return dict(sorted(self.stats.items(), key=lambda x: x[1], reverse=True))
    
    def get_unique_unsubscribe_links(self) -> Dict[str, str]:
        """الحصول على روابط إلغاء الاشتراك الفريدة"""
        links = {}
        for msg in self.messages:
            if msg.unsubscribe_link and msg.sender_email not in links:
                links[msg.sender_email] = msg.unsubscribe_link
        return links
    
    def auto_unsubscribe(self, callback=None) -> Dict[str, str]:
        """إلغاء الاشتراك تلقائياً من جميع القوائم البريدية"""
        if not REQUESTS_AVAILABLE:
            return {"error": "مكتبة requests غير مثبتة. قم بتثبيتها: pip install requests"}
        
        links = self.get_unique_unsubscribe_links()
        
        if not links:
            return {"info": "لا توجد روابط إلغاء اشتراك"}
        
        results = {}
        total = len(links)
        
        if callback:
            callback(f"جاري إلغاء الاشتراك من {total} قائمة...", 0)
        
        for i, (sender, link) in enumerate(links.items(), 1):
            try:
                response = requests.get(
                    link,
                    timeout=10,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    },
                    allow_redirects=True
                )
                
                if response.status_code == 200:
                    results[sender] = "✅ تم إلغاء الاشتراك"
                elif response.status_code in [301, 302, 303, 307, 308]:
                    results[sender] = "✅ تم (إعادة توجيه)"
                else:
                    results[sender] = f"⚠️ كود: {response.status_code}"
                
                time.sleep(0.5)
                
            except requests.Timeout:
                results[sender] = "⏱️ انتهت المهلة"
            except requests.RequestException as e:
                results[sender] = f"❌ خطأ: {str(e)[:30]}"
            except Exception as e:
                results[sender] = f"❌ {str(e)[:30]}"
            
            if callback:
                progress = int((i / total) * 100)
                callback(f"تم معالجة {i}/{total}", progress)
        
        self.unsubscribe_results = results
        
        if callback:
            success = sum(1 for v in results.values() if '✅' in v)
            callback(f"اكتمل: {success}/{total} نجح", 100)
        
        return results
    
    def export_results(self, filepath: str) -> int:
        """تصدير النتائج لملف"""
        data = {
            "scan_date": datetime.now().isoformat(),
            "total_promotional": len(self.messages),
            "unique_senders": len(self.stats),
            "senders_summary": dict(self.get_senders_summary()),
            "unsubscribe_results": self.unsubscribe_results,
            "links": [
                {
                    "sender": msg.sender,
                    "email": msg.sender_email,
                    "link": msg.unsubscribe_link
                }
                for msg in self.messages if msg.unsubscribe_link
            ]
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        return len(data['links'])


class EmailCleanerGUI:
    """الواجهة الرسومية للتطبيق"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{__title__} v{__version__}")
        self.root.geometry("850x700")
        self.root.minsize(750, 600)
        
        self.colors = {
            'bg': '#1a1a2e',
            'fg': '#eaeaea',
            'accent': '#0f3460',
            'button': '#e94560',
            'entry_bg': '#16213e',
            'success': '#00d26a',
            'warning': '#ffc107',
            'info': '#17a2b8',
        }
        
        self.root.configure(bg=self.colors['bg'])
        
        self.core = EmailCleanerCore()
        self.is_connected = False
        
        self._setup_styles()
        self._create_widgets()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
    
    def _setup_styles(self):
        """إعداد الأنماط"""
        style = ttk.Style()
        style.theme_use('clam')
        
        style.configure('TFrame', background=self.colors['bg'])
        style.configure('TLabel', background=self.colors['bg'], 
                       foreground=self.colors['fg'], font=('Segoe UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 16, 'bold'),
                       foreground=self.colors['button'])
        style.configure('TProgressbar', background=self.colors['button'],
                       troughcolor=self.colors['accent'])
        style.configure('TLabelframe', background=self.colors['bg'])
        style.configure('TLabelframe.Label', background=self.colors['bg'],
                       foreground=self.colors['fg'], font=('Segoe UI', 10, 'bold'))
    
    def _create_widgets(self):
        """إنشاء العناصر"""
        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # العنوان
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 15))
        
        ttk.Label(title_frame, text="🧹 أداة تنظيف البريد الإلكتروني",
                 style='Title.TLabel').pack()
        ttk.Label(title_frame, text="Email Cleaner Tool",
                 font=('Segoe UI', 10)).pack()
        
        # تسجيل الدخول
        login_frame = ttk.LabelFrame(main_frame, text=" 🔐 تسجيل الدخول ", padding=15)
        login_frame.pack(fill=tk.X, pady=(0, 10))
        
        email_frame = ttk.Frame(login_frame)
        email_frame.pack(fill=tk.X, pady=5)
        ttk.Label(email_frame, text="📧 البريد:", width=15, anchor='e').pack(side=tk.LEFT)
        self.email_var = tk.StringVar()
        self.email_entry = ttk.Entry(email_frame, textvariable=self.email_var,
                                     width=45, font=('Consolas', 11))
        self.email_entry.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
        
        pass_frame = ttk.Frame(login_frame)
        pass_frame.pack(fill=tk.X, pady=5)
        ttk.Label(pass_frame, text="🔑 كلمة المرور:", width=15, anchor='e').pack(side=tk.LEFT)
        self.pass_var = tk.StringVar()
        self.pass_entry = ttk.Entry(pass_frame, textvariable=self.pass_var,
                                    width=45, show='●', font=('Consolas', 11))
        self.pass_entry.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
        
        btn_frame = ttk.Frame(login_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        self.connect_btn = tk.Button(btn_frame, text="🔗 اتصال", command=self._connect,
                                     bg=self.colors['button'], fg='white',
                                     font=('Segoe UI', 10, 'bold'), relief=tk.FLAT,
                                     padx=20, pady=5, cursor='hand2')
        self.connect_btn.pack(side=tk.LEFT, padx=5)
        
        self.disconnect_btn = tk.Button(btn_frame, text="🔌 قطع", command=self._disconnect,
                                        bg=self.colors['accent'], fg='white',
                                        font=('Segoe UI', 10, 'bold'), relief=tk.FLAT,
                                        padx=20, pady=5, state=tk.DISABLED, cursor='hand2')
        self.disconnect_btn.pack(side=tk.LEFT, padx=5)
        
        self.status_label = ttk.Label(btn_frame, text="⚪ غير متصل", font=('Segoe UI', 9))
        self.status_label.pack(side=tk.RIGHT, padx=10)
        
        # الإعدادات
        settings_frame = ttk.LabelFrame(main_frame, text=" ⚙️ الإعدادات ", padding=10)
        settings_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(settings_frame, text="📅 الأيام:").pack(side=tk.LEFT)
        self.days_var = tk.StringVar(value="30")
        ttk.Spinbox(settings_frame, from_=1, to=365, textvariable=self.days_var,
                   width=6, font=('Segoe UI', 10)).pack(side=tk.LEFT, padx=(5, 20))
        
        ttk.Label(settings_frame, text="📊 الحد:").pack(side=tk.LEFT)
        self.limit_var = tk.StringVar(value="500")
        ttk.Spinbox(settings_frame, from_=50, to=2000, textvariable=self.limit_var,
                   width=6, increment=50, font=('Segoe UI', 10)).pack(side=tk.LEFT, padx=5)
        
        # الأزرار
        actions_frame = ttk.Frame(main_frame)
        actions_frame.pack(fill=tk.X, pady=10)
        
        self.scan_btn = tk.Button(actions_frame, text="🔍 فحص", command=self._start_scan,
                                  bg=self.colors['success'], fg='white',
                                  font=('Segoe UI', 11, 'bold'), relief=tk.FLAT,
                                  padx=18, pady=8, state=tk.DISABLED, cursor='hand2')
        self.scan_btn.pack(side=tk.LEFT, padx=3)
        
        self.delete_btn = tk.Button(actions_frame, text="🗑️ حذف", command=self._delete_messages,
                                    bg=self.colors['button'], fg='white',
                                    font=('Segoe UI', 11, 'bold'), relief=tk.FLAT,
                                    padx=18, pady=8, state=tk.DISABLED, cursor='hand2')
        self.delete_btn.pack(side=tk.LEFT, padx=3)
        
        self.unsub_btn = tk.Button(actions_frame, text="🚫 إلغاء الاشتراكات",
                                   command=self._auto_unsubscribe,
                                   bg=self.colors['info'], fg='white',
                                   font=('Segoe UI', 11, 'bold'), relief=tk.FLAT,
                                   padx=18, pady=8, state=tk.DISABLED, cursor='hand2')
        self.unsub_btn.pack(side=tk.LEFT, padx=3)
        
        self.export_btn = tk.Button(actions_frame, text="📄 تصدير", command=self._export_report,
                                    bg=self.colors['accent'], fg='white',
                                    font=('Segoe UI', 11, 'bold'), relief=tk.FLAT,
                                    padx=18, pady=8, state=tk.DISABLED, cursor='hand2')
        self.export_btn.pack(side=tk.LEFT, padx=3)
        
        # شريط التقدم
        progress_frame = ttk.Frame(main_frame)
        progress_frame.pack(fill=tk.X, pady=5)
        
        self.progress_var = tk.IntVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var,
                                            maximum=100)
        self.progress_bar.pack(fill=tk.X)
        self.progress_label = ttk.Label(progress_frame, text="")
        self.progress_label.pack(pady=5)
        
        # النتائج
        results_frame = ttk.LabelFrame(main_frame, text=" 📋 النتائج ", padding=10)
        results_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        self.results_text = scrolledtext.ScrolledText(
            results_frame, wrap=tk.WORD, font=('Consolas', 10),
            bg=self.colors['entry_bg'], fg=self.colors['fg'],
            insertbackground=self.colors['fg'], height=12
        )
        self.results_text.pack(fill=tk.BOTH, expand=True)
        
        # التذييل
        footer_frame = ttk.Frame(main_frame)
        footer_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Label(footer_frame, text=f"تطوير: {__author__} | {__email__}",
                 font=('Segoe UI', 9), foreground='#888').pack()
        ttk.Label(footer_frame, text=__copyright__,
                 font=('Segoe UI', 8), foreground='#666').pack()
        
        links_frame = ttk.Frame(footer_frame)
        links_frame.pack(pady=3)
        
        github_link = ttk.Label(links_frame, text="GitHub", font=('Segoe UI', 9, 'underline'),
                               foreground=self.colors['info'], cursor='hand2')
        github_link.pack(side=tk.LEFT, padx=10)
        github_link.bind('<Button-1>', lambda e: webbrowser.open(__github__))
        
        email_link = ttk.Label(links_frame, text="📧 تواصل", font=('Segoe UI', 9, 'underline'),
                              foreground=self.colors['button'], cursor='hand2')
        email_link.pack(side=tk.LEFT, padx=10)
        email_link.bind('<Button-1>', lambda e: webbrowser.open(f'mailto:{__email__}'))
    
    def _log(self, message: str, clear: bool = False):
        if clear:
            self.results_text.delete(1.0, tk.END)
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.results_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.results_text.see(tk.END)
    
    def _update_progress(self, message: str, progress: int):
        self.progress_var.set(progress)
        self.progress_label.config(text=message)
        self.root.update_idletasks()
    
    def _connect(self):
        email_addr = self.email_var.get().strip()
        password = self.pass_var.get().strip()
        
        if not email_addr or not password:
            messagebox.showwarning("تنبيه", "يرجى إدخال البريد وكلمة المرور")
            return
        
        self._log("جاري الاتصال...", clear=True)
        self.connect_btn.config(state=tk.DISABLED)
        
        def do_connect():
            success, message = self.core.connect(email_addr, password)
            self.root.after(0, lambda: self._on_connect_result(success, message))
        
        threading.Thread(target=do_connect, daemon=True).start()
    
    def _on_connect_result(self, success: bool, message: str):
        self._log(message)
        
        if success:
            self.is_connected = True
            self.status_label.config(text="🟢 متصل", foreground=self.colors['success'])
            self.connect_btn.config(state=tk.DISABLED)
            self.disconnect_btn.config(state=tk.NORMAL)
            self.scan_btn.config(state=tk.NORMAL)
            self.email_entry.config(state=tk.DISABLED)
            self.pass_entry.config(state=tk.DISABLED)
            self._log("✅ يمكنك الآن فحص البريد")
        else:
            self.connect_btn.config(state=tk.NORMAL)
            self._log("❌ فشل الاتصال")
            self._log("💡 لـ Gmail استخدم 'كلمة مرور التطبيق':")
            self._log("   https://myaccount.google.com/apppasswords")
    
    def _disconnect(self):
        self.core.disconnect()
        self.is_connected = False
        self.status_label.config(text="⚪ غير متصل", foreground=self.colors['fg'])
        self.connect_btn.config(state=tk.NORMAL)
        self.disconnect_btn.config(state=tk.DISABLED)
        self.scan_btn.config(state=tk.DISABLED)
        self.delete_btn.config(state=tk.DISABLED)
        self.unsub_btn.config(state=tk.DISABLED)
        self.export_btn.config(state=tk.DISABLED)
        self.email_entry.config(state=tk.NORMAL)
        self.pass_entry.config(state=tk.NORMAL)
        self._log("🔌 تم قطع الاتصال")
    
    def _start_scan(self):
        if not self.is_connected:
            return
        
        days = int(self.days_var.get())
        limit = int(self.limit_var.get())
        
        self._log(f"🔍 فحص البريد (آخر {days} يوم)...", clear=True)
        self.scan_btn.config(state=tk.DISABLED)
        
        def do_scan():
            messages = self.core.scan_inbox(
                days_back=days, limit=limit,
                callback=lambda msg, prog: self.root.after(0, lambda: self._update_progress(msg, prog))
            )
            self.root.after(0, lambda: self._on_scan_complete(messages))
        
        threading.Thread(target=do_scan, daemon=True).start()
    
    def _on_scan_complete(self, messages: List[EmailMessage]):
        self.scan_btn.config(state=tk.NORMAL)
        
        if messages:
            self._log(f"\n{'='*50}")
            self._log(f"📊 {len(messages)} رسالة دعائية")
            self._log(f"{'='*50}\n")
            
            summary = self.core.get_senders_summary()
            self._log("📈 أكثر المرسلين:")
            for i, (sender, count) in enumerate(list(summary.items())[:10], 1):
                self._log(f"   {i:2}. {sender}: {count}")
            
            links = self.core.get_unique_unsubscribe_links()
            self._log(f"\n🔗 روابط إلغاء اشتراك: {len(links)}")
            
            self.delete_btn.config(state=tk.NORMAL)
            self.unsub_btn.config(state=tk.NORMAL if links else tk.DISABLED)
            self.export_btn.config(state=tk.NORMAL)
        else:
            self._log("📭 لا توجد رسائل دعائية")
    
    def _delete_messages(self):
        if not self.core.messages:
            return
        
        count = len(self.core.messages)
        if not messagebox.askyesno("تأكيد", f"⚠️ حذف {count} رسالة؟", icon='warning'):
            return
        
        self._log("\n🗑️ جاري الحذف...")
        self.delete_btn.config(state=tk.DISABLED)
        
        def do_delete():
            deleted, message = self.core.delete_messages()
            self.root.after(0, lambda: self._log(message))
            self.root.after(0, lambda: self.delete_btn.config(state=tk.NORMAL))
        
        threading.Thread(target=do_delete, daemon=True).start()
    
    def _auto_unsubscribe(self):
        if not REQUESTS_AVAILABLE:
            messagebox.showerror("خطأ", "ثبت مكتبة requests:\npip install requests")
            return
        
        links = self.core.get_unique_unsubscribe_links()
        if not links:
            messagebox.showinfo("معلومة", "لا توجد روابط")
            return
        
        if not messagebox.askyesno("تأكيد", f"🚫 إلغاء الاشتراك من {len(links)} قائمة؟"):
            return
        
        self._log(f"\n🚫 إلغاء الاشتراك من {len(links)} قائمة...")
        self.unsub_btn.config(state=tk.DISABLED)
        
        def do_unsub():
            results = self.core.auto_unsubscribe(
                callback=lambda msg, prog: self.root.after(0, lambda: self._update_progress(msg, prog))
            )
            self.root.after(0, lambda: self._on_unsub_complete(results))
        
        threading.Thread(target=do_unsub, daemon=True).start()
    
    def _on_unsub_complete(self, results: Dict[str, str]):
        self.unsub_btn.config(state=tk.NORMAL)
        
        self._log(f"\n{'='*50}")
        self._log("📊 نتائج إلغاء الاشتراك:")
        
        success = 0
        for sender, result in results.items():
            self._log(f"   {sender[:35]}: {result}")
            if '✅' in result:
                success += 1
        
        self._log(f"\n✅ نجح: {success} | ❌ فشل: {len(results) - success}")
        messagebox.showinfo("اكتمل", f"✅ نجح: {success}\n❌ فشل: {len(results) - success}")
    
    def _export_report(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfilename=f"email_report_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        )
        
        if filepath:
            count = self.core.export_results(filepath)
            self._log(f"\n📄 تم التصدير: {filepath}")
            messagebox.showinfo("نجاح", f"تم التصدير ({count} رابط)")
    
    def _on_closing(self):
        if self.is_connected:
            self.core.disconnect()
        self.root.destroy()
    
    def run(self):
        self.root.mainloop()


def main():
    app = EmailCleanerGUI()
    app.run()


if __name__ == "__main__":
    main()
