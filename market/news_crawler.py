#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
金十数据爬虫
获取财经日历、快讯和事件结果
支持Playwright浏览器登录
"""

import asyncio
import aiohttp
import re
import json
import os
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

# Playwright 为可选依赖
try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None
    Browser = None
    Page = None

from .news_store import CalendarEvent, FlashNews, get_news_store
from .event_config import (
    ECONOMIC_DATA, KEY_SPEAKERS, KEY_EVENTS,
    get_important_event_names, get_high_impact_event_names,
    DATA_IMPACT_RULES, WATCH_SYMBOLS
)
from .system_log import get_system_log


class Jin10Crawler:
    """金十数据爬虫"""

    # 金十数据API地址
    CALENDAR_API = "https://rmdex.jin10.com/data.json"
    FLASH_NEWS_API = "https://flash-api.jin10.com/get_flash_list"

    # 备用地址
    CALENDAR_PAGE = "https://www.jin10.com/rili/calendar.html"
    FLASH_PAGE = "https://www.jin10.com/flash"

    # 请求头
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://www.jin10.com/',
        'Origin': 'https://www.jin10.com',
    }

    def __init__(self, username: str = None, password: str = None):
        self.store = get_news_store()
        self.system_log = get_system_log()
        self._session = None

        # 重要事件名称（用于过滤）
        self._important_names = get_important_event_names()
        self._high_impact_names = get_high_impact_event_names()

        # Playwright相关
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context = None
        self._page: Optional[Page] = None
        self._logged_in = False

        # 登录凭据
        self._username = username or os.environ.get('JIN10_USERNAME', '18689211297')
        self._password = password or os.environ.get('JIN10_PASSWORD', 'Wangxx1234')

        print("[Jin10Crawler] 金十数据爬虫已初始化")
        self.system_log.add_log("news_crawler_start", message="金十数据爬虫已初始化")

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取HTTP会话"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                headers=self.HEADERS,
                timeout=timeout
            )
        return self._session

    # ==================== Playwright登录 ====================

    async def _init_browser(self):
        """初始化Playwright浏览器"""
        if not PLAYWRIGHT_AVAILABLE:
            print("[Jin10Crawler] Playwright未安装，跳过浏览器初始化")
            return

        if self._browser is not None:
            return

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            self._context = await self._browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            self._page = await self._context.new_page()
            print("[Jin10Crawler] Playwright浏览器已初始化")
        except Exception as e:
            print(f"[Jin10Crawler] 初始化浏览器失败: {e}")
            self.system_log.add_log("news_calendar_fetch_error", detail={
                "error": str(e),
                "step": "init_browser"
            }, message=f"初始化浏览器失败: {e}")

    async def _handle_verify_dialog(self) -> bool:
        """
        处理登录后可能出现的验证对话框

        Returns:
            是否成功处理（True表示可以继续，False表示需要人工介入）
        """
        try:
            await asyncio.sleep(1)

            # 检查是否有遮罩层
            mask = await self._page.query_selector('.user-modal-mask.active')
            if mask:
                print("[Jin10Crawler] 检测到遮罩层")

                # 检查遮罩层内是否有验证相关内容
                mask_content = await mask.inner_text()

                # 1. 滑块验证
                if '滑块' in mask_content or '滑动' in mask_content or 'verify' in mask_content.lower():
                    print("[Jin10Crawler] 检测到滑块验证，需要人工处理")
                    self.system_log.add_log("news_calendar_fetch_error", detail={
                        "type": "slider_captcha"
                    }, message="检测到滑块验证，需要人工处理")
                    return False

                # 2. 短信验证码
                if '验证码' in mask_content or '短信' in mask_content:
                    print("[Jin10Crawler] 检测到短信验证码，需要人工处理")
                    self.system_log.add_log("news_calendar_fetch_error", detail={
                        "type": "sms_captcha"
                    }, message="检测到短信验证码，需要人工处理")
                    return False

                # 3. 风险提示弹窗 - 尝试关闭
                close_btns = await self._page.query_selector_all('.modal-close, .close-btn, .icon-close, [class*="close"]')
                for btn in close_btns:
                    try:
                        if await btn.is_visible():
                            await btn.click()
                            await asyncio.sleep(1)
                            print("[Jin10Crawler] 已尝试关闭风险提示弹窗")
                            return True
                    except:
                        continue

                # 4. 尝试按ESC关闭
                await self._page.keyboard.press('Escape')
                await asyncio.sleep(1)

                # 5. 尝试点击遮罩层外部关闭
                try:
                    await self._page.mouse.click(10, 10)
                    await asyncio.sleep(1)
                except:
                    pass

            return True

        except Exception as e:
            print(f"[Jin10Crawler] 处理验证对话框异常: {e}")
            return True

    async def _check_login_success(self) -> bool:
        """
        检查登录是否成功

        Returns:
            是否登录成功
        """
        try:
            # 方法1: 检查登录弹窗是否消失
            modal_visible = await self._page.is_visible('.user-modal, .login-modal, .user-modal-mask.active')
            if not modal_visible:
                print("[Jin10Crawler] 登录弹窗已消失")
                return True

            # 方法2: 检查是否有用户信息显示
            user_selectors = [
                '.user-avatar', '.user-info', '.username', '.user-name',
                '.header-user', '.user-dropdown', '[class*="user-avatar"]'
            ]
            for selector in user_selectors:
                try:
                    user_el = await self._page.query_selector(selector)
                    if user_el:
                        visible = await user_el.is_visible()
                        if visible:
                            print(f"[Jin10Crawler] 找到用户元素: {selector}")
                            return True
                except:
                    continue

            # 方法3: 检查URL是否不再包含login
            current_url = self._page.url
            if 'login' not in current_url.lower():
                print(f"[Jin10Crawler] URL已跳转: {current_url}")
                return True

            # 方法4: 检查是否有登录状态的cookie或localStorage
            try:
                logged_in = await self._page.evaluate('''
                    () => {
                        // 检查localStorage
                        const token = localStorage.getItem('token') || localStorage.getItem('access_token');
                        if (token) return true;

                        // 检查cookie
                        const cookies = document.cookie;
                        if (cookies.includes('token=') || cookies.includes('auth=')) return true;

                        return false;
                    }
                ''')
                if logged_in:
                    print("[Jin10Crawler] 检测到登录token")
                    return True
            except:
                pass

            return False

        except Exception as e:
            print(f"[Jin10Crawler] 检查登录状态异常: {e}")
            return False

    async def login(self) -> bool:
        """
        使用Playwright登录金十数据

        Returns:
            是否登录成功
        """
        try:
            await self._init_browser()

            if self._logged_in:
                return True

            self.system_log.add_log("news_crawler_start", detail={
                "username": self._username[:3] + "****"
            }, message="开始登录金十数据...")

            print(f"[Jin10Crawler] 开始登录金十数据，用户: {self._username[:3]}****")

            # 访问首页，然后点击登录
            await self._page.goto('https://www.jin10.com/', wait_until='networkidle', timeout=60000)
            await asyncio.sleep(2)

            # 点击登录按钮打开登录弹窗
            try:
                # 尝试多种登录按钮选择器
                login_btn_selectors = [
                    '.login-wall__btn',  # 登录墙上的按钮
                    '.header-login-btn',
                    '.user-login',
                    'button:has-text("登录")',
                    'button:has-text("立即登录")',
                    'a:has-text("登录")'
                ]

                login_clicked = False
                for selector in login_btn_selectors:
                    try:
                        btn = await self._page.wait_for_selector(selector, timeout=3000, state='visible')
                        if btn:
                            await btn.click()
                            login_clicked = True
                            print(f"[Jin10Crawler] 已点击登录按钮: {selector}")
                            break
                    except:
                        continue

                if not login_clicked:
                    # 尝试查找包含登录文本的元素
                    elements = await self._page.query_selector_all('button, a, span')
                    for el in elements:
                        try:
                            text = await el.text_content()
                            if text and ('登录' in text or '登錄' in text):
                                await el.click()
                                login_clicked = True
                                print(f"[Jin10Crawler] 已点击登录元素: {text.strip()}")
                                break
                        except:
                            continue

                # 等待登录弹窗出现
                await asyncio.sleep(2)

            except Exception as e:
                print(f"[Jin10Crawler] 点击登录按钮失败: {e}")

            # 截图调试
            try:
                await self._page.screenshot(path='/tmp/jin10_after_click.png')
                print("[Jin10Crawler] 已保存点击后截图")
            except:
                pass

            # 尝试查找并填写登录表单
            try:
                # 方法1: 查找手机号/用户名输入框
                username_filled = False
                username_selectors = [
                    'input[placeholder*="手机"]',
                    'input[placeholder*="账号"]',
                    'input[placeholder*="用户名"]',
                    'input[type="tel"]',
                    'input[name="phone"]',
                    'input[name="username"]',
                    '#phone',
                    '#username',
                    '.login-phone input',
                    '.login-form input:first-child',
                    '.user-modal input[type="tel"]',
                    '.user-modal input:first-of-type'
                ]

                for selector in username_selectors:
                    try:
                        el = await self._page.wait_for_selector(selector, timeout=3000, state='visible')
                        if el:
                            await el.click()
                            await asyncio.sleep(0.3)
                            # 先清空再填写
                            await el.fill('')
                            await el.type(self._username, delay=50)
                            username_filled = True
                            print(f"[Jin10Crawler] 已通过选择器 {selector} 输入用户名")
                            break
                    except:
                        continue

                if not username_filled:
                    # 尝试查找所有可见的输入框
                    inputs = await self._page.query_selector_all('input:visible')
                    if inputs:
                        for inp in inputs:
                            try:
                                input_type = await inp.get_attribute('type')
                                name_attr = await inp.get_attribute('name') or ''
                                placeholder = await inp.get_attribute('placeholder') or ''
                                # 跳过checkbox, file, hidden等类型
                                if input_type in ['checkbox', 'file', 'hidden', 'submit']:
                                    continue
                                # 优先选择看起来像手机号输入框的
                                if '手机' in placeholder or 'phone' in name_attr.lower() or input_type == 'tel':
                                    await inp.click()
                                    await asyncio.sleep(0.2)
                                    await inp.fill('')
                                    await inp.type(self._username, delay=50)
                                    username_filled = True
                                    print(f"[Jin10Crawler] 已通过遍历输入框填写用户名 (placeholder: {placeholder})")
                                    break
                            except:
                                continue

                        # 如果还没找到，就用第一个可输入的
                        if not username_filled and inputs:
                            for inp in inputs:
                                try:
                                    input_type = await inp.get_attribute('type')
                                    if input_type not in ['checkbox', 'file', 'hidden', 'submit', 'password']:
                                        await inp.click()
                                        await asyncio.sleep(0.2)
                                        await inp.fill('')
                                        await inp.type(self._username, delay=50)
                                        username_filled = True
                                        print("[Jin10Crawler] 已通过遍历输入框填写用户名（默认）")
                                        break
                                except:
                                    continue

                if not username_filled:
                    print("[Jin10Crawler] 未找到用户名输入框")
                    # 检查页面是否有验证码登录
                    page_content = await self._page.content()
                    if '验证码' in page_content or 'code' in page_content:
                        print("[Jin10Crawler] 页面可能需要验证码登录，暂不支持")
                        self.system_log.add_log("news_calendar_fetch_error", detail={
                            "error": "验证码登录不支持",
                            "step": "login_captcha"
                        }, message="金十数据可能需要验证码登录")
                    return False

                # 填写密码
                await asyncio.sleep(0.5)
                password_filled = False
                password_selectors = [
                    'input[placeholder*="密码"]',
                    'input[type="password"]',
                    'input[name="password"]',
                    '#password',
                    '.login-password input',
                    '.login-form input[type="password"]',
                    '.user-modal input[type="password"]'
                ]

                for selector in password_selectors:
                    try:
                        el = await self._page.wait_for_selector(selector, timeout=3000, state='visible')
                        if el:
                            await el.click()
                            await asyncio.sleep(0.3)
                            await el.fill('')
                            await el.type(self._password, delay=50)
                            password_filled = True
                            print(f"[Jin10Crawler] 已通过选择器 {selector} 输入密码")
                            break
                    except:
                        continue

                if not password_filled:
                    # 查找密码输入框
                    inputs = await self._page.query_selector_all('input[type="password"]:visible')
                    if inputs:
                        await inputs[0].click()
                        await asyncio.sleep(0.2)
                        await inputs[0].fill('')
                        await inputs[0].type(self._password, delay=50)
                        password_filled = True
                        print("[Jin10Crawler] 已通过遍历密码框填写密码")

                if not password_filled:
                    print("[Jin10Crawler] 未找到密码输入框，可能需要验证码登录")
                    return False

                # 点击登录按钮
                await asyncio.sleep(0.5)
                login_clicked = False
                login_selectors = [
                    'button:has-text("登录")',
                    'button:has-text("登錄")',
                    '.login-btn',
                    '.btn-login',
                    'button[type="submit"]',
                    '.login-form button',
                    '.user-modal button[type="submit"]',
                    '.user-modal button:has-text("登")'
                ]

                for selector in login_selectors:
                    try:
                        el = await self._page.wait_for_selector(selector, timeout=2000, state='visible')
                        if el:
                            await el.click()
                            login_clicked = True
                            print(f"[Jin10Crawler] 已通过选择器 {selector} 点击登录按钮")
                            break
                    except:
                        continue

                if not login_clicked:
                    # 查找包含"登录"文本的按钮
                    buttons = await self._page.query_selector_all('button:visible')
                    for btn in buttons:
                        try:
                            text = await btn.text_content()
                            if text and ('登录' in text or '登錄' in text):
                                await btn.click()
                                login_clicked = True
                                print("[Jin10Crawler] 已通过文本查找点击登录按钮")
                                break
                        except:
                            continue

                if not login_clicked:
                    print("[Jin10Crawler] 未找到登录按钮")
                    return False

                # 等待登录处理
                await asyncio.sleep(3)

                # 处理可能出现的验证对话框
                verify_ok = await self._handle_verify_dialog()
                if not verify_ok:
                    print("[Jin10Crawler] 需要人工处理验证")
                    # 不直接返回False，继续检查登录状态

                # 再等待一下让登录完成
                await asyncio.sleep(3)

                # 使用新方法检查登录状态
                if await self._check_login_success():
                    self._logged_in = True
                    print("[Jin10Crawler] 登录成功")
                    self.system_log.add_log("news_calendar_update", detail={
                        "step": "login_success"
                    }, message="金十数据登录成功")
                    return True
                    return True

                print("[Jin10Crawler] 登录状态未知，可能需要人工处理验证")
                return False

            except Exception as e:
                print(f"[Jin10Crawler] 登录表单操作失败: {e}")
                self.system_log.add_log("news_calendar_fetch_error", detail={
                    "error": str(e),
                    "step": "login_form"
                }, message=f"登录表单操作失败: {e}")
                return False

        except Exception as e:
            print(f"[Jin10Crawler] 登录异常: {e}")
            self.system_log.add_log("news_calendar_fetch_error", detail={
                "error": str(e),
                "step": "login_exception"
            }, message=f"登录异常: {e}")
            return False

    async def fetch_calendar_with_browser(self, days: int = 7) -> Dict[str, List[CalendarEvent]]:
        """
        使用浏览器获取财经日历（登录后）

        Args:
            days: 获取未来多少天

        Returns:
            {date_str: [CalendarEvent]}
        """
        try:
            if not self._logged_in:
                success = await self.login()
                if not success:
                    print("[Jin10Crawler] 登录失败，无法获取财经日历")
                    return {}

            result = {}
            today = datetime.now()

            for i in range(days):
                date = today + timedelta(days=i)
                date_str = date.strftime("%Y%m%d")
                date_key = date.strftime("%Y-%m-%d")

                try:
                    url = f"https://www.jin10.com/rili/calendar_{date_str}.html"
                    await self._page.goto(url, wait_until='networkidle', timeout=30000)
                    await asyncio.sleep(1)

                    # 解析页面内容
                    events = await self._parse_calendar_from_page(date_key)
                    if events:
                        result[date_key] = events
                        print(f"[Jin10Crawler] 浏览器获取 {date_key} 成功: {len(events)} 条事件")

                except Exception as e:
                    print(f"[Jin10Crawler] 获取 {date_key} 失败: {e}")
                    continue

            if result:
                self.system_log.add_log("news_calendar_update", detail={
                    "source": "browser",
                    "dates": len(result),
                    "events": sum(len(v) for v in result.values())
                }, message=f"浏览器获取财经日历成功: {len(result)}天")

            return result

        except Exception as e:
            print(f"[Jin10Crawler] 浏览器获取日历失败: {e}")
            self.system_log.add_log("news_calendar_fetch_error", detail={
                "error": str(e),
                "step": "browser_fetch"
            }, message=f"浏览器获取日历失败: {e}")
            return {}

    async def _parse_calendar_from_page(self, date_key: str) -> List[CalendarEvent]:
        """从页面解析财经日历"""
        events = []

        try:
            # 等待日历数据加载
            await asyncio.sleep(3)

            # 获取页面内容
            content = await self._page.content()

            # 保存HTML用于调试
            with open('/tmp/jin10_calendar_page.html', 'w') as f:
                f.write(content)
            print("[Jin10Crawler] 已保存日历页面HTML到 /tmp/jin10_calendar_page.html")

            # 尝试从页面JSON数据中提取
            # 金十数据通常会在页面中嵌入JSON数据
            json_patterns = [
                r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});',
                r'__NEXT_DATA__\s*=\s*(\{.*?\})</script>',
            ]

            for json_pattern in json_patterns:
                match = re.search(json_pattern, content, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(1))
                        print(f"[Jin10Crawler] 找到JSON数据，keys: {list(data.keys())[:10]}")

                        # 解析数据 - 尝试不同的数据结构
                        calendar_data = data.get('calendar', data.get('rili', data.get('calendarData', {})))

                        if isinstance(calendar_data, dict):
                            event_list = calendar_data.get('events', calendar_data.get('data', calendar_data.get('list', [])))
                        else:
                            event_list = calendar_data if isinstance(calendar_data, list) else []

                        if not event_list:
                            # 尝试其他路径
                            event_list = data.get('events', data.get('data', []))

                        print(f"[Jin10Crawler] 找到 {len(event_list) if isinstance(event_list, list) else 0} 个事件")

                        for item in event_list if isinstance(event_list, list) else []:
                            try:
                                event = self._parse_calendar_item(date_key.replace('-', ''), item)
                                if event and event.importance >= 2:
                                    events.append(event)
                            except Exception as e:
                                continue

                        if events:
                            return events

                    except Exception as e:
                        print(f"[Jin10Crawler] 解析页面JSON失败: {e}")

            # 如果JSON解析失败，尝试解析HTML
            soup = BeautifulSoup(content, 'html.parser')

            # 查找事件行 - 尝试多种选择器
            row_selectors = [
                '.calendar-item', '.rili-item', 'tr[data-time]',
                '.jin-calendar__tr', '.event-row', '.calendar-row',
                '[class*="calendar"] tr', '[class*="rili"] tr'
            ]

            for selector in row_selectors:
                rows = soup.select(selector)
                if rows:
                    print(f"[Jin10Crawler] 使用选择器 {selector} 找到 {len(rows)} 行")
                    for row in rows:
                        try:
                            event = self._parse_calendar_row(row, date_key)
                            if event:
                                events.append(event)
                        except:
                            continue
                    if events:
                        break

            # 如果还是没有数据，尝试从表格解析
            if not events:
                tables = soup.find_all('table')
                for table in tables:
                    rows = table.find_all('tr')
                    for row in rows:
                        cells = row.find_all(['td', 'th'])
                        if len(cells) >= 3:
                            # 尝试从单元格提取信息
                            try:
                                # 查找包含时间的单元格
                                time_cell = None
                                name_cell = None
                                importance_cell = None

                                for i, cell in enumerate(cells):
                                    text = cell.get_text(strip=True)
                                    if re.match(r'\d{1,2}:\d{2}', text):
                                        time_cell = cell
                                    elif any(imp in text.lower() for imp in ['非农', 'cpi', '利率', 'gdp', 'pmi', 'adp', 'eia']):
                                        name_cell = cell

                                if name_cell:
                                    name = name_cell.get_text(strip=True)
                                    # 检查重要性
                                    row_classes = row.get('class', [])
                                    importance = 2  # 默认中等
                                    if any('high' in c.lower() or 'star-3' in c.lower() for c in row_classes):
                                        importance = 3

                                    time_str = time_cell.get_text(strip=True) if time_cell else ''

                                    event = CalendarEvent(
                                        id=f"{date_key}_{name}_{time_str}",
                                        name=name,
                                        name_en='',
                                        country='',
                                        importance=importance,
                                        publish_time=self._parse_datetime(date_key.replace('-', ''), time_str),
                                        forecast='',
                                        previous='',
                                        actual='',
                                        unit='',
                                        symbols=self._get_event_symbols(name)
                                    )
                                    events.append(event)
                            except:
                                continue

        except Exception as e:
            print(f"[Jin10Crawler] 解析页面失败: {e}")
            import traceback
            traceback.print_exc()

        return events

    def _parse_calendar_row(self, row, date_key: str) -> Optional[CalendarEvent]:
        """解析HTML行"""
        try:
            # 获取时间
            time_el = row.select_one('.time, .calendar-time')
            time_str = time_el.get_text(strip=True) if time_el else ''

            # 获取事件名称
            name_el = row.select_one('.event, .calendar-event, .name')
            name = name_el.get_text(strip=True) if name_el else ''

            if not name:
                return None

            # 检查重要性
            importance_el = row.select_one('.star, .importance')
            importance = 2
            if importance_el:
                star_class = importance_el.get('class', [])
                if 'star-3' in star_class or 'high' in star_class:
                    importance = 3
                elif 'star-2' in star_class or 'medium' in star_class:
                    importance = 2
                else:
                    importance = 1

            # 过滤重要事件
            is_important = any(
                imp_name.lower() in name.lower()
                for imp_name in self._high_impact_names
            )
            if not is_important:
                return None

            # 获取数值
            forecast_el = row.select_one('.forecast, .consensus')
            previous_el = row.select_one('.previous, .prev')
            actual_el = row.select_one('.actual')

            forecast = forecast_el.get_text(strip=True) if forecast_el else ''
            previous = previous_el.get_text(strip=True) if previous_el else ''
            actual = actual_el.get_text(strip=True) if actual_el else ''

            # 解析时间
            publish_time = self._parse_datetime(date_key.replace('-', ''), time_str)

            return CalendarEvent(
                id=f"{date_key}_{name}_{time_str}",
                name=name,
                name_en='',
                country='',
                importance=importance,
                publish_time=publish_time,
                forecast=forecast,
                previous=previous,
                actual=actual,
                unit='',
                symbols=self._get_event_symbols(name)
            )

        except Exception as e:
            return None

    async def close(self):
        """关闭会话和浏览器"""
        if self._session and not self._session.closed:
            await self._session.close()

        if self._browser:
            await self._browser.close()
            self._browser = None
            self._page = None
            self._context = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        self._logged_in = False
        print("[Jin10Crawler] 已关闭所有连接")

    # ==================== 财经日历 ====================

    async def fetch_calendar(self, days: int = 7) -> Dict[str, List[CalendarEvent]]:
        """
        获取财经日历

        Args:
            days: 获取未来多少天

        Returns:
            {date_str: [CalendarEvent]}
        """
        try:
            self.system_log.add_log("news_calendar_fetch", detail={"days": days}, message="开始获取财经日历...")

            # 尝试金十数据API
            session = await self._get_session()
            result = await self._fetch_jin10_calendar(session, days)
            if result:
                self.system_log.add_log("news_calendar_update", detail={
                    "source": "金十数据API",
                    "dates": len(result),
                    "events": sum(len(v) for v in result.values())
                }, message=f"金十数据API获取成功: {len(result)}天")
                return result

            # 尝试使用浏览器登录获取
            try:
                result = await self.fetch_calendar_with_browser(days)
                if result:
                    self.system_log.add_log("news_calendar_update", detail={
                        "source": "金十数据浏览器",
                        "dates": len(result),
                        "events": sum(len(v) for v in result.values())
                    }, message=f"浏览器获取成功: {len(result)}天")
                    return result
            except Exception as e:
                print(f"[Jin10Crawler] 浏览器获取失败: {e}")

            # 如果所有真实数据源都失败，返回模拟数据用于测试
            self.system_log.add_log("news_calendar_fetch_error", detail={
                "reason": "所有数据源失败，使用模拟数据"
            }, message="所有数据源失败，使用模拟数据")
            return self._get_mock_calendar(days)

        except Exception as e:
            self.system_log.add_log("news_calendar_fetch_error", detail={
                "error": str(e)
            }, message=f"获取财经日历失败: {e}")
            print(f"[Jin10Crawler] 获取财经日历失败: {e}")
            import traceback
            traceback.print_exc()
            return self._get_mock_calendar(days)

    async def _fetch_jin10_calendar(self, session: aiohttp.ClientSession, days: int) -> Optional[Dict]:
        """通过金十数据API获取财经日历"""
        today = datetime.now()
        date_list = []
        for i in range(days):
            date = today + timedelta(days=i)
            date_list.append(date.strftime("%Y%m%d"))

        result = {}

        for date_str in date_list:
            try:
                url = f"https://rili.jin10.com/datas/{date_str}.json"
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        events = self._parse_calendar_data(date_str, data)
                        if events:
                            date_key = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                            result[date_key] = events
            except Exception as e:
                continue

        if result:
            print(f"[Jin10Crawler] 金十数据获取成功: {len(result)} 天")
            return result

        return None

    def _get_mock_calendar(self, days: int) -> Dict[str, List[CalendarEvent]]:
        """生成模拟数据用于测试"""
        from .event_config import ECONOMIC_DATA

        result = {}
        today = datetime.now()

        # 模拟一些重要事件
        mock_events = [
            {"name": "非农就业人数", "importance": 3, "hour": 20, "minute": 30, "country": "US"},
            {"name": "失业率", "importance": 3, "hour": 20, "minute": 30, "country": "US"},
            {"name": "CPI年率", "importance": 3, "hour": 20, "minute": 30, "country": "US"},
            {"name": "美联储利率决议", "importance": 3, "hour": 14, "minute": 0, "country": "US"},
            {"name": "EIA原油库存", "importance": 2, "hour": 22, "minute": 30, "country": "US"},
            {"name": "初请失业金人数", "importance": 2, "hour": 20, "minute": 30, "country": "US"},
            {"name": "日本央行利率决议", "importance": 3, "hour": 11, "minute": 0, "country": "JP"},
            {"name": "ADP就业人数", "importance": 2, "hour": 20, "minute": 15, "country": "US"},
            {"name": "零售销售月率", "importance": 2, "hour": 20, "minute": 30, "country": "US"},
        ]

        # 分配到未来几天
        for i in range(min(days, 3)):
            date = today + timedelta(days=i)
            date_key = date.strftime("%Y-%m-%d")

            events = []
            # 每天分配2-3个事件
            day_events = mock_events[i*3:(i+1)*3] if i < 3 else mock_events[:2]

            for idx, mock in enumerate(day_events):
                event_date = date.replace(hour=mock["hour"], minute=mock["minute"])

                event = CalendarEvent(
                    id=f"mock_{date_key}_{idx}",
                    name=mock["name"],
                    name_en=mock["name"],
                    country=mock["country"],
                    importance=mock["importance"],
                    publish_time=event_date,
                    forecast="待定",
                    previous="--",
                    actual="",
                    unit="",
                    symbols=self._get_event_symbols(mock["name"])
                )
                events.append(event)

            if events:
                result[date_key] = events

        print(f"[Jin10Crawler] 生成模拟数据: {len(result)} 天")
        return result

    async def _fetch_calendar_api(self, session: aiohttp.ClientSession, days: int) -> Optional[Dict]:
        """通过API获取财经日历"""
        try:
            # 尝试金十数据
            today = datetime.now()
            date_list = []
            for i in range(days):
                date = today + timedelta(days=i)
                date_list.append(date.strftime("%Y%m%d"))

            result = {}

            for date_str in date_list:
                try:
                    # 金十数据日历API
                    url = f"https://rili.jin10.com/datas/{date_str}.json"
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            events = self._parse_calendar_data(date_str, data)
                            if events:
                                # 转换日期格式
                                date_key = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                                result[date_key] = events
                except Exception as e:
                    print(f"[Jin10Crawler] 获取 {date_str} 日历失败: {e}")
                    continue

            if result:
                print(f"[Jin10Crawler] API获取财经日历成功: {len(result)} 天")
                return result

        except Exception as e:
            print(f"[Jin10Crawler] API获取失败: {e}")

        return None

    def _parse_calendar_data(self, date_str: str, data: Dict) -> List[CalendarEvent]:
        """解析财经日历数据"""
        events = []

        try:
            # 金十数据格式: {date: {events: [...]}}
            if isinstance(data, dict):
                date_data = data.get(date_str, data)
                event_list = date_data.get('events', date_data.get('data', []))
            else:
                event_list = data if isinstance(data, list) else []

            for item in event_list:
                try:
                    event = self._parse_calendar_item(date_str, item)
                    if event and event.importance > 0:  # 只保留有影响的
                        events.append(event)
                except Exception as e:
                    continue

        except Exception as e:
            print(f"[Jin10Crawler] 解析日历数据失败: {e}")

        return events

    def _parse_calendar_item(self, date_str: str, item: Dict) -> Optional[CalendarEvent]:
        """解析单个日历事件"""
        # 获取事件名称
        name = item.get('name', item.get('event', ''))
        if not name:
            return None

        # 过滤不重要的事件
        is_important = False
        for important_name in self._important_names:
            if important_name.lower() in name.lower():
                is_important = True
                break

        if not is_important:
            return None

        # 获取重要性星级
        star = item.get('star', item.get('importance', 0))
        if isinstance(star, str):
            star = len(star)  # 星星数量
        importance = min(3, max(0, int(star)))

        # 解析时间
        time_str = item.get('time', item.get('datetime', ''))
        publish_time = self._parse_datetime(date_str, time_str)

        # 获取国家
        country = item.get('country', item.get('region', ''))

        # 获取数值
        forecast = item.get('forecast', item.get('consensus', ''))
        previous = item.get('previous', item.get('prev', ''))
        actual = item.get('actual', '')
        unit = item.get('unit', '')

        # 生成ID
        event_id = item.get('id', f"{date_str}_{name}_{time_str}")

        # 查找对应的事件配置
        symbols = self._get_event_symbols(name)

        return CalendarEvent(
            id=str(event_id),
            name=name,
            name_en=item.get('name_en', ''),
            country=country,
            importance=importance,
            publish_time=publish_time,
            forecast=str(forecast),
            previous=str(previous),
            actual=str(actual),
            unit=unit,
            symbols=symbols
        )

    def _parse_datetime(self, date_str: str, time_str: str) -> datetime:
        """解析日期时间"""
        try:
            # date_str: "20260315" 或 "2026-03-15"
            if len(date_str) == 8:
                year = int(date_str[:4])
                month = int(date_str[4:6])
                day = int(date_str[6:8])
            else:
                parts = date_str.split('-')
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])

            # time_str: "20:30" 或 "2030"
            hour, minute = 0, 0
            if time_str:
                time_str = time_str.strip()
                if ':' in time_str:
                    parts = time_str.split(':')
                    hour = int(parts[0])
                    minute = int(parts[1]) if len(parts) > 1 else 0
                elif len(time_str) >= 3:
                    hour = int(time_str[:-2])
                    minute = int(time_str[-2:])

            return datetime(year, month, day, hour, minute)

        except Exception as e:
            print(f"[Jin10Crawler] 解析时间失败: {date_str} {time_str}")
            return datetime.now()

    def _get_event_symbols(self, name: str) -> List[str]:
        """获取事件影响的品种"""
        for event in ECONOMIC_DATA:
            if event['name'] in name or event['name_en'].lower() in name.lower():
                return event['symbols']

        # 根据国家推断
        if '美国' in name or 'US' in name.upper():
            return ["GOLD", "SPX", "USDJPY"]
        elif '日本' in name or 'JP' in name.upper():
            return ["USDJPY"]

        return WATCH_SYMBOLS

    async def _fetch_calendar_page(self, session: aiohttp.ClientSession, days: int) -> Dict:
        """通过页面爬取财经日历"""
        # 备用方案：爬取HTML页面
        result = {}
        today = datetime.now()

        for i in range(days):
            date = today + timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")

            try:
                url = f"https://www.jin10.com/rili/calendar_{date.strftime('%Y%m%d')}.html"
                async with session.get(url) as response:
                    if response.status == 200:
                        html = await response.text()
                        events = self._parse_calendar_html(html, date_str)
                        if events:
                            result[date_str] = events
            except Exception as e:
                print(f"[Jin10Crawler] 爬取页面 {date_str} 失败: {e}")
                continue

        return result

    def _parse_calendar_html(self, html: str, date_str: str) -> List[CalendarEvent]:
        """解析日历HTML"""
        events = []

        try:
            soup = BeautifulSoup(html, 'html.parser')
            # 这里需要根据实际HTML结构解析
            # 金十数据的HTML结构可能会变化，需要定期维护

            event_rows = soup.select('.jin-calendar__tr')
            for row in event_rows:
                try:
                    time_td = row.select_one('.jin-calendar__time')
                    event_td = row.select_one('.jin-calendar__event')
                    if not time_td or not event_td:
                        continue

                    name = event_td.get_text(strip=True)
                    time_str = time_td.get_text(strip=True)

                    # 过滤重要事件
                    is_important = any(
                        imp_name.lower() in name.lower()
                        for imp_name in self._important_names
                    )
                    if not is_important:
                        continue

                    event = CalendarEvent(
                        id=f"{date_str}_{name}_{time_str}",
                        name=name,
                        publish_time=self._parse_datetime(date_str, time_str),
                        importance=2,  # 默认中等重要性
                        symbols=self._get_event_symbols(name)
                    )
                    events.append(event)

                except Exception:
                    continue

        except Exception as e:
            print(f"[Jin10Crawler] 解析HTML失败: {e}")

        return events

    # ==================== 快讯 ====================

    async def fetch_flash_news(self, max_id: int = 0, count: int = 30) -> List[FlashNews]:
        """
        获取快讯列表（无需登录，直接访问页面）

        Args:
            max_id: 获取此ID之前的快讯（用于分页）- 保留参数但不使用
            count: 获取数量

        Returns:
            快讯列表
        """
        try:
            # 直接访问快讯页面获取数据，无需登录
            return await self.fetch_flash_news_without_login(count)
        except Exception as e:
            self.system_log.add_log("news_flash_fetch_error", detail={
                "source": "Playwright浏览器",
                "error": str(e)
            }, message=f"[Playwright] 获取快讯失败: {e}")
            print(f"[Jin10Crawler] [Playwright] 获取快讯失败: {e}")
            return []

    async def fetch_flash_news_without_login(self, count: int = 30) -> List[FlashNews]:
        """
        获取快讯（无需登录，直接访问页面）

        Args:
            count: 获取数量

        Returns:
            快讯列表
        """
        try:
            # 检查Playwright是否可用
            if not PLAYWRIGHT_AVAILABLE:
                print("[Jin10Crawler] Playwright未安装，使用HTTP API获取快讯")
                return await self._fetch_flash_news_via_api(count)

            # 初始化浏览器（如果还没初始化）
            if self._browser is None:
                await self._init_browser()

            # 检查浏览器是否初始化成功
            if self._page is None:
                print("[Jin10Crawler] 浏览器初始化失败，使用HTTP API获取快讯")
                return await self._fetch_flash_news_via_api(count)

            # 直接访问快讯页面
            url = 'https://www.jin10.com/flash'
            print(f"[Jin10Crawler] 访问快讯页面: {url}")
            await self._page.goto(url, wait_until='networkidle', timeout=30000)
            await asyncio.sleep(3)

            # 获取页面内容
            content = await self._page.content()

            # 解析HTML获取快讯
            news_list = []
            soup = BeautifulSoup(content, 'html.parser')
            items = soup.select('.jin-flash-item.flash')

            print(f"[Jin10Crawler] 找到 {len(items)} 个快讯元素")

            for idx, item in enumerate(items[:count]):
                try:
                    # 获取时间: .item-time
                    time_el = item.select_one('.item-time')
                    time_str = time_el.get_text(strip=True) if time_el else ''

                    # 获取内容: .flash-text 或 .item-right
                    content_el = item.select_one('.flash-text')
                    if not content_el:
                        right_el = item.select_one('.item-right')
                        if right_el:
                            title_el = right_el.select_one('.item-title')
                            if title_el:
                                content_text = title_el.get_text(strip=True)
                            else:
                                content_text = right_el.get_text(strip=True)
                        else:
                            continue
                    else:
                        content_text = content_el.get_text(strip=True)

                    # 使用内容哈希生成唯一ID
                    content_hash = hashlib.md5(content_text.encode('utf-8')).hexdigest()[:12]
                    news_id = f"jin10_{content_hash}"

                    # 解析时间
                    news_time = self._parse_html_time(time_str)

                    news = FlashNews(
                        id=news_id,
                        content=content_text,
                        source='jin10',
                        time=news_time,
                        importance=0,
                        keywords=[],
                        related_symbols=[]
                    )
                    news_list.append(news)

                except Exception as e:
                    print(f"[Jin10Crawler] 解析快讯项失败: {e}")
                    continue

            if news_list:
                self.system_log.add_log("news_flash_fetch", detail={
                    "source": "金十网站",
                    "url": url,
                    "count": len(news_list)
                }, message=f"[金十网站] 获取快讯成功: {len(news_list)}条")
                print(f"[Jin10Crawler] [金十网站] 获取快讯成功: {len(news_list)}条")
            else:
                self.system_log.add_log("news_flash_fetch", detail={
                    "source": "金十网站",
                    "url": url,
                    "count": 0
                }, message="[金十网站] 未获取到快讯")

            return news_list

        except Exception as e:
            self.system_log.add_log("news_flash_fetch_error", detail={
                "source": "金十网站",
                "error": str(e)
            }, message=f"[金十网站] 获取快讯异常: {e}")
            print(f"[Jin10Crawler] [金十网站] 获取快讯异常: {e}")
            return []

    async def _fetch_flash_news_via_api(self, count: int = 30) -> List[FlashNews]:
        """
        通过HTTP API获取快讯（备用方法，无需Playwright）

        Args:
            count: 获取数量

        Returns:
            快讯列表
        """
        try:
            session = await self._get_session()

            # 金十快讯API
            params = {
                "channel": "-8200",  # 全部快讯
                "vip": 1,
                "max_time": int(datetime.now().timestamp())
            }

            async with session.get(self.FLASH_NEWS_API, params=params) as resp:
                if resp.status != 200:
                    print(f"[Jin10Crawler] HTTP API请求失败: {resp.status}")
                    return []

                data = await resp.json()

            # 解析快讯
            news_list = self._parse_flash_news(data, count)

            if news_list:
                self.system_log.add_log("news_flash_fetch", detail={
                    "source": "金十API",
                    "count": len(news_list)
                }, message=f"[金十API] 获取快讯成功: {len(news_list)}条")
                print(f"[Jin10Crawler] [金十API] 获取快讯成功: {len(news_list)}条")
            else:
                self.system_log.add_log("news_flash_fetch", detail={
                    "source": "金十API",
                    "count": 0
                }, message="[金十API] 未获取到快讯")

            return news_list

        except Exception as e:
            self.system_log.add_log("news_flash_fetch_error", detail={
                "source": "金十API",
                "error": str(e)
            }, message=f"[金十API] 获取快讯异常: {e}")
            print(f"[Jin10Crawler] [金十API] 获取快讯异常: {e}")
            return []

    def _parse_flash_news(self, data: Dict, count: int) -> List[FlashNews]:
        """解析快讯数据"""
        news_list = []

        try:
            items = data.get('data', [])

            for item in items[:count]:
                try:
                    news = FlashNews(
                        id=str(item.get('id', '')),
                        content=item.get('content', item.get('data', '')),
                        source='jin10',
                        time=self._parse_news_time(item.get('time', item.get('created_at', ''))),
                        importance=0,  # 后续分析填充
                        keywords=[],
                        related_symbols=[]
                    )
                    news_list.append(news)
                except Exception:
                    continue

        except Exception as e:
            print(f"[Jin10Crawler] 解析快讯失败: {e}")

        return news_list

    def _parse_news_time(self, time_data) -> datetime:
        """解析快讯时间"""
        if isinstance(time_data, (int, float)):
            # Unix时间戳
            return datetime.fromtimestamp(int(time_data))
        elif isinstance(time_data, str):
            try:
                # ISO格式
                return datetime.fromisoformat(time_data.replace('Z', '+00:00'))
            except:
                return datetime.now()
        return datetime.now()

    def _parse_html_time(self, time_str: str) -> datetime:
        """
        解析HTML中的时间字符串

        Args:
            time_str: 时间字符串，如 "23:18:35" 或 "03-15 23:18"

        Returns:
            datetime对象
        """
        if not time_str:
            return datetime.now()

        try:
            now = datetime.now()

            # 格式1: "HH:MM:SS"
            if re.match(r'^\d{2}:\d{2}:\d{2}$', time_str):
                hour, minute, second = map(int, time_str.split(':'))
                return now.replace(hour=hour, minute=minute, second=second, microsecond=0)

            # 格式2: "MM-DD HH:MM"
            if re.match(r'^\d{2}-\d{2} \d{2}:\d{2}$', time_str):
                parts = time_str.split(' ')
                month, day = map(int, parts[0].split('-'))
                hour, minute = map(int, parts[1].split(':'))
                return now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)

            # 格式3: "HH:MM"
            if re.match(r'^\d{2}:\d{2}$', time_str):
                hour, minute = map(int, time_str.split(':'))
                return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        except Exception as e:
            print(f"[Jin10Crawler] 解析HTML时间失败: {time_str}, {e}")

        return datetime.now()

    # ==================== 事件结果 ====================

    async def fetch_event_result(self, event_id: str) -> Optional[Dict]:
        """
        获取事件发布结果

        Args:
            event_id: 事件ID

        Returns:
            {actual, forecast, previous, result}
        """
        try:
            session = await self._get_session()

            # 尝试从日历数据中获取
            url = f"https://rili.jin10.com/datas/{event_id.split('_')[0]}.json"
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._find_event_result(event_id, data)

        except Exception as e:
            print(f"[Jin10Crawler] 获取事件结果失败: {e}")

        return None

    def _find_event_result(self, event_id: str, data: Dict) -> Optional[Dict]:
        """从数据中查找事件结果"""
        try:
            # 遍历查找匹配的事件
            for key, value in data.items():
                events = value.get('events', value.get('data', []))
                for item in events:
                    if str(item.get('id', '')) == event_id:
                        return {
                            'actual': item.get('actual', ''),
                            'forecast': item.get('forecast', ''),
                            'previous': item.get('previous', ''),
                        }
        except Exception:
            pass

        return None

    # ==================== 影响分析 ====================

    def analyze_data_impact(self, event: CalendarEvent) -> Dict:
        """
        分析数据对品种的影响

        Args:
            event: 已发布的事件（含实际值）

        Returns:
            {symbol: {direction, strength, reason}}
        """
        if not event.actual or not event.forecast:
            return {}

        result = self._compare_values(event.actual, event.forecast, event.unit)
        impact = {}

        for symbol in event.symbols:
            symbol_impact = self._get_symbol_impact(symbol, event.name, result)
            if symbol_impact:
                impact[symbol] = symbol_impact

        return impact

    def _compare_values(self, actual: str, forecast: str, unit: str) -> str:
        """比较实际值和预期值"""
        try:
            # 提取数值
            actual_num = self._extract_number(actual)
            forecast_num = self._extract_number(forecast)

            if actual_num is None or forecast_num is None:
                return 'unknown'

            diff_pct = (actual_num - forecast_num) / abs(forecast_num) if forecast_num != 0 else 0

            if abs(diff_pct) < 0.05:  # 5%以内认为符合预期
                return 'in_line'
            elif diff_pct > 0:
                return 'better'  # 实际值高于预期
            else:
                return 'worse'  # 实际值低于预期

        except Exception:
            return 'unknown'

    def _extract_number(self, value: str) -> Optional[float]:
        """从字符串中提取数值"""
        if not value:
            return None

        try:
            # 移除百分号、逗号等
            cleaned = re.sub(r'[,%$￥¥]', '', str(value))
            # 提取数字
            match = re.search(r'[-+]?\d*\.?\d+', cleaned)
            if match:
                return float(match.group())
        except Exception:
            pass

        return None

    def _get_symbol_impact(self, symbol: str, event_name: str, result: str) -> Optional[Dict]:
        """获取对特定品种的影响"""
        if result == 'unknown' or result == 'in_line':
            return None

        # 查找匹配的规则
        rules = DATA_IMPACT_RULES.get(symbol, {})

        for event_key, rule in rules.items():
            if event_key in event_name:
                direction = rule.get(result, '中性')
                reason_key = f'reason_{result}'
                reason = rule.get(reason_key, '')

                return {
                    'direction': direction,
                    'strength': '中',
                    'reason': reason
                }

        return None

    def analyze_news_impact(self, news: FlashNews) -> Dict:
        """
        分析快讯对品种的影响

        Args:
            news: 快讯内容

        Returns:
            {speaker, impact: {symbol: {direction, reason}}}
        """
        content = news.content

        # 1. 检查是否涉及关键人物
        speaker_info = self._check_key_speaker(content)

        # 2. 检查是否涉及关键事件
        event_info = self._check_key_event(content)

        impact = {}

        if speaker_info:
            # 根据讲话内容分析影响
            for symbol in speaker_info.get('impact_symbols', []):
                symbol_impact = self._analyze_speaker_content(
                    symbol, content, speaker_info
                )
                if symbol_impact:
                    impact[symbol] = symbol_impact

        if event_info:
            # 根据事件类型分析影响
            for symbol in event_info.get('symbols', []):
                symbol_impact = self._analyze_event_content(
                    symbol, content, event_info
                )
                if symbol_impact:
                    impact[symbol] = symbol_impact

        return {
            'speaker': speaker_info.get('name', '') if speaker_info else '',
            'speaker_title': speaker_info.get('title', '') if speaker_info else '',
            'event_type': event_info.get('name', '') if event_info else '',
            'impact': impact
        }

    def _check_key_speaker(self, content: str) -> Optional[Dict]:
        """检查是否涉及关键人物"""
        for speaker in KEY_SPEAKERS:
            for keyword in speaker['keywords']:
                if keyword in content:
                    # 检查是否涉及关键话题
                    for topic in speaker['watch_topics']:
                        if topic in content:
                            return speaker
        return None

    def _check_key_event(self, content: str) -> Optional[Dict]:
        """检查是否涉及关键事件"""
        for event in KEY_EVENTS:
            for keyword in event['watch_keywords']:
                if keyword in content:
                    return event
        return None

    def _analyze_speaker_content(self, symbol: str, content: str, speaker: Dict) -> Optional[Dict]:
        """分析讲话内容对品种的影响"""
        default_impact = speaker.get('default_impact', {}).get(symbol, {})

        for topic, direction in default_impact.items():
            if topic in content:
                return {
                    'direction': direction,
                    'strength': '高' if speaker['importance'] == 3 else '中',
                    'reason': f"{speaker['name']}关于{topic}的讲话"
                }

        return {
            'direction': '不确定',
            'strength': '中',
            'reason': f"{speaker['name']}讲话"
        }

    def _analyze_event_content(self, symbol: str, content: str, event: Dict) -> Optional[Dict]:
        """分析事件内容对品种的影响"""
        # 简单的情感分析
        negative_words = ['下跌', '暴跌', '利空', '担忧', '风险', '紧张', '冲突', '战争']
        positive_words = ['上涨', '暴涨', '利好', '乐观', '增长', '协议', '达成']

        negative_count = sum(1 for w in negative_words if w in content)
        positive_count = sum(1 for w in positive_words if w in content)

        if negative_count > positive_count:
            direction = '利空'
        elif positive_count > negative_count:
            direction = '利好'
        else:
            direction = '不确定'

        return {
            'direction': direction,
            'strength': '高' if event['importance'] == 3 else '中',
            'reason': f"{event['name']}相关新闻"
        }


# 全局单例
_jin10_crawler = None


def get_jin10_crawler() -> Jin10Crawler:
    """获取金十爬虫单例"""
    global _jin10_crawler
    if _jin10_crawler is None:
        _jin10_crawler = Jin10Crawler()
    return _jin10_crawler