import ccxt
import numpy as np
import requests
import hashlib
import uuid
import time
import json
import random
import discord
from discord.ext import tasks
import matplotlib.pyplot as plt
import os

# 設定 matplotlib 支持中文和負號
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

import mplfinance as mpf
import pandas as pd
from discord.ext import commands


# === 全域變數與統計檔案設定 ===
STATS_FILE = "stats.json"
win_count = 0
loss_count = 0

# === 移動止損相關全域變數 ===
current_pos_entry_type = None # 記錄持倉的進場信號類型 ('rsi' 或 'breakout')
current_stop_loss_price = None # 記錄當前持倉的止損價格
current_position_id_global = None # 記錄當前持倉的 positionId

def load_stats():
    global win_count, loss_count
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r') as f:
                stats = json.load(f)
                win_count = stats.get('win_count', 0)
                loss_count = stats.get('loss_count', 0)
            print(f"已載入統計數據: 勝場 {win_count}, 敗場 {loss_count}")
        except (IOError, json.JSONDecodeError) as e:
            print(f"讀取統計數據失敗: {e}, 初始化為 0")
            win_count = 0
            loss_count = 0
    else:
        print("未找到統計數據檔案，初始化為 0")
        win_count = 0
        loss_count = 0

def save_stats():
    global win_count, loss_count
    try:
        with open(STATS_FILE, 'w') as f:
            json.dump({'win_count': win_count, 'loss_count': loss_count}, f)
        print(f"已儲存統計數據: 勝場 {win_count}, 敗場 {loss_count}")
    except IOError as e:
        print(f"錯誤：無法儲存勝率統計數據: {e}")





# === Bitunix API 函數 === #
def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


# 完全按照ccc.py中的get_signed_params函數實現

from config import BITUNIX_API_KEY, BITUNIX_SECRET_KEY, DISCORD_WEBHOOK_URL, STOP_MULT, LIMIT_MULT, RSI_BUY, RSI_LEN, EXIT_RSI, BREAKOUT_LOOKBACK, ATR_LEN, ATR_MULT, TIMEFRAME, LEVERAGE, TRADING_PAIR, SYMBOL, MARGIN_COIN, LOOP_INTERVAL_SECONDS, QUANTITY_PRECISION
print(f"[Config Check] SYMBOL from config: {SYMBOL}")
print(f"[Config Check] TRADING_PAIR from config: {TRADING_PAIR}")

def get_signed_params(api_key, secret_key, query_params: dict = None, body: dict = None, path: str = None, method: str = None):
    """
    按照 Bitunix 官方雙重 SHA256 簽名方式對請求參數進行簽名。
    
    參數:
        api_key (str): 用戶 API Key
        secret_key (str): 用戶 Secret Key
        query_params (dict): 查詢參數 (GET 方法)
        body (dict or None): 請求 JSON 主體 (POST 方法)
    
    返回:
        headers (dict): 包含簽名所需的請求頭（api-key, sign, nonce, timestamp 等）
    """
    nonce = uuid.uuid4().hex
    timestamp = str(int(time.time() * 1000))

    # 構造 query string: 將參數按鍵名 ASCII 升序排序後，鍵名與鍵值依次拼接
    if query_params:
        params_str = {k: str(v) for k, v in query_params.items()}
        sorted_items = sorted(params_str.items(), key=lambda x: x[0])
        query_str = "".join([f"{k}{v}" for k, v in sorted_items])
    else:
        query_str = ""

    # 構造 body string: 將 JSON 體壓縮成字符串 (無空格)
    if body is not None:
        if isinstance(body, (dict, list)):
            body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
        else:
            body_str = str(body)
    else:
        body_str = ""

    # 根據 method 決定簽名內容
    if method == "GET":
        digest_input = nonce + timestamp + api_key + query_str
    else:
        digest_input = nonce + timestamp + api_key + body_str
    # 第一次 SHA256
    digest = hashlib.sha256(digest_input.encode('utf-8')).hexdigest()
    # 第二次 SHA256
    sign = hashlib.sha256((digest + secret_key).encode('utf-8')).hexdigest()

  

    # 構造標頭
    headers = {
        "api-key": api_key,
        "sign": sign,
        "nonce": nonce,
        "timestamp": timestamp,
        "language": "en-US",
        "Content-Type": "application/json"
    }
    return nonce, timestamp, sign, headers

def send_order(api_key, secret_key, symbol, margin_coin, side, size, leverage=LEVERAGE, position_id=None):
    # 直接下單，不再自動設置槓桿/槓桿
    # 正確的API端點路徑
    path = "/api/v1/futures/trade/place_order"
    url = f"https://fapi.bitunix.com{path}"
    
    # 根據cc.py中的格式調整請求參數
    # 將side轉換為適當的side和tradeSide參數
    if side == "open_long":
        api_side = "BUY"
        trade_side = "OPEN"
    elif side == "close_long":
        api_side = "SELL"
        trade_side = "CLOSE"
    elif side == "open_short":
        api_side = "SELL"
        trade_side = "OPEN"
    elif side == "close_short":
        api_side = "BUY"
        trade_side = "CLOSE"
    else:
        print(f"錯誤：不支持的交易方向 {side}")
        return {"error": f"不支持的交易方向: {side}"}
    
    body = {
        "symbol": symbol,
        "marginCoin": margin_coin,  # 新增保證金幣種參數
        "qty": str(size),  # API要求數量為字符串
        "side": api_side,
        "tradeSide": trade_side,
        "orderType": "MARKET",  # 市價單
        "effect": "GTC"  # 訂單有效期
    }

    if position_id and (side == "close_long" or side == "close_short"):
        body["positionId"] = position_id

    print(f"準備發送訂單: {body}")
    
    try:
        # 使用更新後的get_signed_params獲取完整的headers
        _, _, _, headers = get_signed_params(BITUNIX_API_KEY, BITUNIX_SECRET_KEY, {}, body)
        
        response = requests.post(url, headers=headers, data=json.dumps(body, separators=(',', ':'), ensure_ascii=False))
        response.raise_for_status()  # 檢查HTTP錯誤
        result = response.json()
        print(f"API響應: {result}")
        return result
    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP錯誤: {e}, 響應: {response.text if 'response' in locals() else '無響應'}"
        print(error_msg)
        send_discord_message(f"🔴 **下單錯誤**: {error_msg} 🔴", api_key, secret_key)
        return {"error": error_msg}
    except requests.exceptions.RequestException as e:
        error_msg = f"請求錯誤: {e}"
        print(error_msg)
        send_discord_message(f"🔴 **下單錯誤**: {error_msg} 🔴", api_key, secret_key)
        return {"error": error_msg}
    except Exception as e:
        error_msg = f"未知錯誤: {e}"
        print(error_msg)
        send_discord_message(f"🔴 **下單錯誤**: {error_msg} 🔴", api_key, secret_key)
        return {"error": error_msg}

def place_conditional_orders(api_key, secret_key, symbol, margin_coin, position_id, stop_price=None, limit_price=None):
    """
    Place Stop Loss and Take Profit orders for a given position using Bitunix API.
    Note: Bitunix API documentation indicates support for Position TP/SL orders.
    Trailing Stop orders are not explicitly supported by the provided API documentation for this endpoint.
    """
    path = "/api/v1/futures/tpsl/position/place_order"
    url = f"https://fapi.bitunix.com{path}"

    body = {
        "symbol": symbol,
        "positionId": position_id,
    }

    if stop_price is not None:
        body["slPrice"] = str(stop_price) # API requires price as string
        body["slStopType"] = "LAST_PRICE" # Use LAST_PRICE as trigger type

    if limit_price is not None:
        body["tpPrice"] = str(limit_price) # API requires price as string
        body["tpStopType"] = "LAST_PRICE" # Use LAST_PRICE as trigger type

    # Ensure at least one of TP or SL is provided
    if stop_price is None and limit_price is None:
        print(f"[Conditional Orders] 警告: 未提供止損或止盈價格，不設置條件訂單 for position {position_id} on {symbol}")
        return {"error": "未提供止損或止盈價格"}

    print(f"[Conditional Orders] 準備為持倉 {position_id} 在 {symbol} 上設置條件訂單: {body}")

    try:
        # 使用 get_signed_params 獲取完整的 headers
        _, _, _, headers = get_signed_params(api_key, secret_key, {}, body, path, method="POST")

        response = requests.post(url, headers=headers, data=json.dumps(body, separators=(',', ':'), ensure_ascii=False))
        response.raise_for_status()  # 檢查HTTP錯誤
        result = response.json()
        print(f"[Conditional Orders] API 響應: {result}")

        if result.get("code") == 0:
            print(f"[Conditional Orders] 成功為持倉 {position_id} 設置條件訂單")
            # 可以選擇發送 Discord 通知
            # send_discord_message(f"✅ **條件訂單設置成功** ✅", operation_details={
            #     "type": "status_update",
            #     "details": f"持倉 {position_id} 的止損: {stop_price}, 止盈: {limit_price}"
            # })
            return result
        else:
            error_msg = f"[Conditional Orders] API 返回錯誤: {result.get('msg', '未知錯誤')}"
            print(error_msg)
            send_discord_message(f"🔴 **條件訂單設置失敗** 🔴", api_key, secret_key, operation_details={
                "type": "error",
                "details": error_msg,
                "force_send": True
            })
            return {"error": error_msg}

    except requests.exceptions.HTTPError as e:
        error_msg = f"[Conditional Orders] HTTP 錯誤: {e}, 響應: {response.text if 'response' in locals() else '無響應'}"
        print(error_msg)
        send_discord_message(f"🔴 **條件訂單設置失敗** 🔴", api_key, secret_key, operation_details={
            "type": "error",
            "details": error_msg,
            "force_send": True
        })
        return {"error": error_msg}
    except requests.exceptions.RequestException as e:
        error_msg = f"[Conditional Orders] 請求錯誤: {e}"
        print(error_msg)
        send_discord_message(f"🔴 **條件訂單設置失敗** 🔴", api_key, secret_key, operation_details={
            "type": "error",
            "details": error_msg,
            "force_send": True
        })
        return {"error": error_msg}
    except Exception as e:
        error_msg = f"[Conditional Orders] 未知錯誤: {e}"
        print(error_msg)
        send_discord_message(f"🔴 **條件訂單設置失敗** 🔴", api_key, secret_key, operation_details={
            "type": "error",
            "details": error_msg,
            "force_send": True
        })
        return {"error": error_msg}

# Note: As of current information, automatic trailing stop placement for breakout entries is not implemented due to lack of specific API details.

def modify_position_tpsl(api_key, secret_key, symbol, position_id, stop_price=None, limit_price=None):
    """
    Modify Stop Loss and/or Take Profit orders for a given position using Bitunix API.
    Endpoint: /api/v1/futures/tpsl/modify_position_tp_sl_order
    """
    path = "/api/v1/futures/tpsl/modify_position_tp_sl_order"
    url = f"https://fapi.bitunix.com{path}"

    body = {
        "symbol": symbol,
        "positionId": position_id,
    }

    if stop_price is not None:
        body["slPrice"] = str(stop_price) # API requires price as string
        body["slStopType"] = "LAST_PRICE" # Use LAST_PRICE as trigger type

    if limit_price is not None:
        body["tpPrice"] = str(limit_price) # API requires price as string
        body["tpStopType"] = "LAST_PRICE" # Use LAST_PRICE as trigger type

    # Ensure at least one of TP or SL is provided
    if stop_price is None and limit_price is None:
        print(f"[Modify Conditional Orders] 警告: 未提供止損或止盈價格，不修改條件訂單 for position {position_id} on {symbol}")
        return {"error": "未提供止損或止盈價格"}

    print(f"[Modify Conditional Orders] 準備為持倉 {position_id} 在 {symbol} 上修改條件訂單: {body}")

    try:
        # 使用 get_signed_params 獲取完整的 headers
        _, _, _, headers = get_signed_params(api_key, secret_key, {}, body, path, method="POST")

        response = requests.post(url, headers=headers, data=json.dumps(body, separators=(',', ':'), ensure_ascii=False))
        response.raise_for_status()  # 檢查HTTP錯誤
        result = response.json()
        print(f"[Modify Conditional Orders] API 響應: {result}")

        if result.get("code") == 0:
            print(f"[Modify Conditional Orders] 成功為持倉 {position_id} 修改條件訂單")
            return result
        else:
            error_msg = f"[Modify Conditional Orders] API 返回錯誤: {result.get('msg', '未知錯誤')}"
            print(error_msg)
            send_discord_message(f"🔴 **修改條件訂單失敗** 🔴", api_key, secret_key, operation_details={
                "type": "error",
                "details": error_msg,
                "force_send": True
            })
            return {"error": error_msg}

    except requests.exceptions.HTTPError as e:
        error_msg = f"[Modify Conditional Orders] HTTP 錯誤: {e}, 響應: {response.text if 'response' in locals() else '無響應'}"
        print(error_msg)
        send_discord_message(f"🔴 **修改條件訂單失敗** 🔴", api_key, secret_key, operation_details={
            "type": "error",
            "details": error_msg,
            "force_send": True
        })
        return {"error": error_msg}
    except requests.exceptions.RequestException as e:
        error_msg = f"[Modify Conditional Orders] 請求錯誤: {e}"
        print(error_msg)
        send_discord_message(f"🔴 **修改條件訂單失敗** 🔴", api_key, secret_key, operation_details={
            "type": "error",
            "details": error_msg,
            "force_send": True
        })
        return {"error": error_msg}
    except Exception as e:
        error_msg = f"[Modify Conditional Orders] 未知錯誤: {e}"
        print(error_msg)
        send_discord_message(f"🔴 **修改條件訂單失敗** 🔴", api_key, secret_key, operation_details={
            "type": "error",
            "details": error_msg,
            "force_send": True
        })
        return {"error": error_msg}


# === Discord 提醒設定 === #
# DISCORD_WEBHOOK_URL = 'https://discordapp.com/api/webhooks/1366780723864010813/h_CPbJX3THcOElVVHYOeJPR4gTgZGHJ1ehSeXuOAceGTNz3abY0XlljPzzxkaimAcE77'

# 消息緩衝區和計時器設置
message_buffer = []
last_send_time = 0
BUFFER_TIME_LIMIT = 180  # 3分鐘 = 180秒

# 記錄上一次的餘額，用於比較變化
last_balance = None

# 修改函數簽名以包含 operation_details
def send_discord_message(core_message, api_key=None, secret_key=None, operation_details=None):

    global message_buffer, last_send_time, win_count, loss_count # 確保能訪問全域勝敗計數
    current_time = time.time()

    print(f"進入 send_discord_message 函數，核心訊息: {core_message[:50]}...") # 添加日誌

    # 獲取最新的實際持倉狀態和PNL (用於顯示"目前持倉"的盈虧)
    actual_pos_side, actual_pos_qty_str, _, actual_unrealized_pnl = None, None, None, 0.0
    current_pos_pnl_msg = ""
    
    if api_key and secret_key:
        # 注意：這裡的 get_current_position_details 返回四個值
        actual_pos_side, actual_pos_qty_str, _, actual_unrealized_pnl = get_current_position_details(api_key, secret_key, SYMBOL, MARGIN_COIN)
        if actual_pos_side in ["long", "short"] and actual_unrealized_pnl is not None:
            # 這裡可以加入收益率計算，如果 get_current_position_details 也返回保證金的話
            current_pos_pnl_msg = f"\n💰 目前未實現盈虧: {actual_unrealized_pnl:.4f} USDT"

    # 構造勝率字符串
    total_trades = win_count + loss_count
    win_rate_str = f"{win_count / total_trades * 100:.2f}% ({win_count}勝/{loss_count}負)" if total_trades > 0 else "N/A (尚無已完成交易)"
    
    action_specific_msg = core_message
    current_pos_status_for_discord = ""

    if operation_details:
        op_type = operation_details.get("type")
        if op_type == "close_success":
            side_closed_display = "多單" if operation_details.get("side_closed") == "long" else "空單"
            closed_qty = operation_details.get("qty", "N/A")
            pnl = operation_details.get("pnl", 0.0)
            pnl_display = f"{pnl:.4f}" if pnl is not None else "N/A"
            action_specific_msg = f"{core_message} (數量: {closed_qty})\n🎯 **平倉類型**: {side_closed_display}\n💰 **本次已實現盈虧**: {pnl_display} USDT"
            # 添加信號信息（如果存在）
            signal_info = operation_details.get("signal")
            if signal_info:
                action_specific_msg += f"\n📊 **平倉信號**: {signal_info}"
            current_pos_status_for_discord = "🔄 **目前持倉**：無持倉" # 平倉成功後，假設無持倉
            current_pos_pnl_msg = "" # 平倉後，不顯示“目前未實現盈虧”
        elif op_type == "open_success":
            side_opened_display = "多單" if operation_details.get("side_opened") == "long" else "空單"
            opened_qty = operation_details.get("qty", "N/A")
            entry_price_display = f"{operation_details.get('entry_price', 'N/A'):.2f}"
            action_specific_msg = f"{core_message} (數量: {opened_qty}, 估計價格: {entry_price_display} USDT)\nℹ️ **開倉類型**: {side_opened_display}"
            # 添加信號信息（如果存在）
            signal_info = operation_details.get("signal")
            if signal_info:
                action_specific_msg += f"\n📊 **開倉信號**: {signal_info}"
            # 開倉後，持倉狀態應由下方的 actual_pos_side 決定
        elif op_type == "error":
            action_specific_msg = f"🔴 **錯誤**: {core_message}\n{operation_details.get('details', '')}"
            # 添加信號信息（如果存在）
            signal_info = operation_details.get("signal")
            if signal_info:
                action_specific_msg += f"\n📊 **相關信號**: {signal_info}"
        # elif op_type == "balance_update": # 用於餘額更新
        #     available = operation_details.get("available", 0)
        #     margin = operation_details.get("margin", 0)
        #     unrealized_pnl = operation_details.get("unrealized_pnl", 0)
        #     total_asset = operation_details.get("total_asset", available + margin + unrealized_pnl) # 計算總資產
        #     action_specific_msg = f"💰 **當前總資產**: {total_asset:.4f} USDT\n可用餘額: {available:.4f} USDT\n已用保證金: {margin:.4f} USDT\n未實現盈虧: {unrealized_pnl:.4f} USDT" # 構造詳細的餘額信息
        # elif op_type == "status_update": # 用於通道指標等狀態更新
        #     action_specific_msg = core_message
        # 可以添加更多 op_type 的處理

    # 決定最終的持倉狀態顯示 (如果不是平倉成功，則根據實際查詢結果)
    if not (operation_details and operation_details.get("type") == "close_success"):
        if actual_pos_side == "long":
            current_pos_status_for_discord = f"📈 **目前持倉**：多單 (數量: {actual_pos_qty_str})"
        elif actual_pos_side == "short":
            current_pos_status_for_discord = f"📉 **目前持倉**：空單 (數量: {actual_pos_qty_str})"
        else:
            current_pos_status_for_discord = "🔄 **目前持倉**：無持倉"

    # 構造 Discord Embed
    print(f"[Discord Embed] 標題使用的交易對符號: {SYMBOL}") # 添加日誌
    embed = discord.Embed(
        title=f"{SYMBOL} 交易通知", # 使用 SYMBOL
        description=action_specific_msg,
        color=discord.Color.blue() # 可以根據訊息類型調整顏色
    )

    # 添加統計數據欄位
    embed.add_field(name="交易統計", value=win_rate_str, inline=True)

    # 添加持倉狀態欄位
    embed.add_field(name="目前持倉", value=current_pos_status_for_discord, inline=True)

    # 添加未實現盈虧欄位 (如果存在)
    if current_pos_pnl_msg:
        # 移除開頭的換行符和貨幣符號，只保留數字和單位
        pnl_value = current_pos_pnl_msg.replace('\n💰 目前未實現盈虧: ', '').strip()
        embed.add_field(name="目前未實現盈虧", value=pnl_value, inline=False)

    # 添加時間戳欄位
    embed.add_field(name="時間", value=time.strftime('%Y-%m-%d %H:%M:%S'), inline=False)

    # 檢查是否有圖片需要發送 (來自 operation_details)
    files_to_send = None
    image_data = operation_details.get("image_data") if operation_details else None # 檢查 image_data
    image_path = operation_details.get("image_path") if operation_details else None # 保留 image_path 作為備用或舊邏輯兼容

 
    print(f"[Discord Send] 提取到的 image_data (存在): {image_data is not None}") # 添加日誌
    print(f"[Discord Send] 提取到的 image_path: {image_path}") # 添加日誌

    if image_data:
        print("[Discord Send] 檢測到 image_data，準備從記憶體發送圖片。")
        # 將圖片作為附件添加到 Embed 中
        # 需要一個文件名，即使是從記憶體發送
        image_filename = operation_details.get("image_filename", "chart.png") # 可以從 operation_details 獲取文件名，或使用預設值
        files_to_send = {'file': (image_filename, image_data, 'image/png')}
        embed.set_image(url=f"attachment://{image_filename}") # 設置 Embed 圖片為附件
        print(f"[Discord Send] 準備將圖片數據 ({len(image_data)} bytes) 作為附件發送")

    elif image_path and os.path.exists(image_path):
        print(f"[Discord Send] 檢測到 image_path 且文件存在: {image_path}，從文件發送圖片。")
        # 將圖片作為附件添加到 Embed 中
        image_filename = os.path.basename(image_path)
        # Read file content and close the file before sending
        try:
            with open(image_path, 'rb') as f:
                image_data = f.read()
            files_to_send = {'file': (image_filename, image_data, 'image/png')}
            embed.set_image(url=f"attachment://{image_filename}") # 設置 Embed 圖片為附件
            print(f"[Discord Send] 已讀取圖片文件 {image_path}，準備作為附件發送")
        except IOError as e_read:
            print(f"[Discord Send] 讀取圖片文件 {image_path} 失敗: {e_read}")
            image_data = None # Ensure image_data is None if reading failed
            files_to_send = None # 無法讀取文件，不發送圖片

    # 將 Embed 轉換為 webhook payload 格式
    embed_payload = embed.to_dict()
    data_payload = {"embeds": [embed_payload]}

    # 將訊息添加到緩衝區
    # 注意：這裡緩衝區存儲的是 Embed 對象或其字典表示，而不是字符串
    # 為了簡化，我們暫時不對 Embed 進行緩衝，直接發送
    # 如果需要緩衝，需要更複雜的邏輯來合併 Embeds 或處理文件附件
    # 這裡直接發送，忽略 BUFFER_TIME_LIMIT 邏輯
    print("[Discord Send] 準備發送 Discord Embed 訊息")
    try:
        if files_to_send:
            # 發送帶圖片的 Embed
            print(f"[Discord Send] 發送帶圖片的 Embed 到 {DISCORD_WEBHOOK_URL}")
            # 將文件和 JSON payload 都作為 files 參數發送，使用列表格式並為 JSON payload 指定 content_type
            files_list = [
                ('file', (image_filename, image_data, 'image/png')), # Use image_data here
                ('payload_json', (None, json.dumps(data_payload), 'application/json'))
            ]
            # 不傳遞任何自定義 headers，讓 requests 自動處理 multipart/form-data 的 headers
            response = requests.post(DISCORD_WEBHOOK_URL, files=files_list)
            print(f"[Discord Send] requests.post 狀態碼: {response.status_code}, 響應內容: {response.text[:200]}...")
            response.raise_for_status() # 檢查 HTTP 錯誤
            print(f"[Discord Send] Discord Embed (帶圖片) 發送請求成功，狀態碼: {response.status_code}")
            # 如果是從文件發送的，嘗試刪除文件
            if image_path and os.path.exists(image_path):
                 # 嘗試刪除圖片文件，加入重試機制
                 max_retries = 3
                 initial_delay = 1 # 初始延遲1秒
                 retry_delay = 2 # 每次重試延遲2秒
                 for i in range(max_retries):
                     try:
                         # 在刪除前加入延遲，確保文件句柄已釋放
                         if i == 0:
                             time.sleep(initial_delay)
                         else:
                             time.sleep(retry_delay)
                         print(f"[Discord Send] 嘗試刪除文件 (第 {i+1}/{max_retries} 次): {image_path}")
                         os.remove(image_path)
                         print(f"[Discord Send] 已發送圖片並成功刪除文件: {image_path}")
                         break # 刪除成功，跳出循環
                     except Exception as e_remove:
                         if i < max_retries - 1:
                             print(f"[Discord Send] 刪除失敗: {str(e_remove)[:100]}...，將在 {retry_delay} 秒後重試 ({i+1}/{max_retries})")
                             time.sleep(retry_delay)
                         else:
                             print(f"[Discord Send] 最終刪除失敗: {str(e_remove)[:100]}...，保留文件: {image_path}")
                             # 嘗試重命名文件，以便下次不會嘗試刪除同名文件
                             try:
                                 os.rename(image_path, image_path+".temp")
                             except Exception as e_rename:
                                 print(f"臨時文件重命名失敗: {str(e_rename)[:100]}")
                             # 可以選擇記錄佔用文件的進程信息，但這需要 psutil 庫，且可能需要管理員權限
                             # import psutil
                             # for proc in psutil.process_iter(['pid', 'name', 'open_files']):
                             #     try:
                             #         files = proc.info['open_files']
                             #         if any(f.path == image_path for f in files):
                             #             print(f'發現進程佔用: PID={proc.pid} 名稱={proc.name()}')
                             #     except (psutil.NoSuchProcess, psutil.AccessDenied):
                             #         continue
        else:
            # 發送不帶圖片的 Embed
            print(f"[Discord Send] 發送不帶圖片的 Embed 到 {DISCORD_WEBHOOK_URL}")
            response = requests.post(DISCORD_WEBHOOK_URL, json=data_payload)
            print(f"[Discord Send] requests.post 狀態碼: {response.status_code}, 響應內容: {response.text[:200]}...")
            response.raise_for_status() # 檢查 HTTP 錯誤
            print(f"[Discord Send] Discord Embed (無圖片) 發送請求成功，狀態碼: {response.status_code}")

    except requests.exceptions.HTTPError as http_err:
        print(f"[Discord Send] HTTP錯誤 - 發送 Discord Embed 失敗: {http_err}, 響應: {http_err.response.text if http_err.response else '無響應內容'}")
    except Exception as e:
        print(f"[Discord Send] 其他錯誤 - 發送 Discord Embed 失敗: {e}")
        print(f"[Discord Send] 錯誤類型: {type(e)}")

    # 清空緩衝區 (如果實現了緩衝)
    # message_buffer = []
    # last_send_time = current_time
    print(f"已嘗試發送 Discord Embed 消息 - 時間: {time.strftime('%Y-%m-%d %H:%M:%S')}")

# 強制發送緩衝區中的所有消息，不管時間限制
def flush_discord_messages():
    # 由於 send_discord_message 已改為直接發送 Embed，此函數暫時不需要實現複雜的緩衝區處理
    # 如果未來需要緩衝多個 Embeds，需要重新設計此函數
    print("flush_discord_messages 函數被呼叫，但目前不執行任何操作 (Embeds 直接發送)")
    pass





# === 策略邏輯 === #
def fetch_ohlcv(api_key=None, secret_key=None): # 移除了未使用的 symbol 參數
    """獲取指定交易對的K線數據，並添加錯誤處理"""
    try:
        # 使用ccxt庫連接到Binance交易所
        exchange = ccxt.binance()
        # 獲取指定交易對的4小時K線數據，限制為最近100根
        # 這將確保我們總是獲取最新的市場數據
        ohlcv = exchange.fetch_ohlcv(TRADING_PAIR, timeframe=TIMEFRAME, limit=100) # 使用 TRADING_PAIR
        return np.array(ohlcv)
    except Exception as e:
        error_msg = f"獲取 {TRADING_PAIR} K線數據失敗: {e}"
        print(f"錯誤：{error_msg}")
        return None




def compute_indicators(df, rsi_len, atr_len, breakout_len, api_key=None, secret_key=None, symbol=None):
    """計算技術指標，並添加錯誤處理"""
    try:
        # 確保 talib 庫已安裝並導入
        try:
            import talib
        except ImportError:
            error_msg = "錯誤：TA-Lib 未正確安裝。請按照以下步驟操作：\n1. 確保虛擬環境已激活\n2. 檢查是否已安裝 TA-Lib C 函式庫\n3. 執行 'pip install TA_Lib‑*.whl' 安裝 Python 套件\n詳細安裝指引請參考 README.md"
            print(error_msg)
            return None # 返回 None 表示計算失敗

        df["rsi"] = talib.RSI(df["close"], timeperiod=rsi_len)
        df["atr"] = talib.ATR(df["high"], df["low"], df["close"], timeperiod=atr_len)
        # 使用 shift(1) 確保不包含當前 K 線的最高價
        df["highest_break"] = df["high"].shift(1).rolling(window=breakout_len).max()
        return df
    except Exception as e:
        error_msg = f"計算指標失敗: {e}"
        print(f"錯誤：{error_msg}")
        return None # 返回 None 表示計算失敗

def calculate_trade_size(api_key, secret_key, symbol, wallet_percentage, leverage, current_price):
    """根據錢包餘額、槓桿和當前價格計算下單數量"""
    available_balance = check_wallet_balance(api_key, secret_key) # 確保這裡獲取的是最新的可用餘額
    if available_balance is None or available_balance <= 0:
        print("錯誤：無法獲取錢包餘額或餘額不足")
        return 0

    # 計算用於交易的資金量
    trade_capital = available_balance * wallet_percentage

    # 計算理論上可以開倉的合約價值 (使用槓桿)
    # 合約價值 = 資金量 * 槓桿
    contract_value = trade_capital * leverage

    # 計算下單數量 (合約數量)
    # 數量 = 合約價值 / 當前價格
    # 這裡需要考慮 Bitunix 對於不同幣種的最小下單單位和數量精度
    if current_price > 0:
        quantity = contract_value / current_price
        # 這裡需要根據實際交易對的精度進行調整
        # 例如，如果 ETHUSDT 數量精度是 0.001，則需要 round(quantity, 3)
        # 為了通用性，這裡暫時不進行精度處理，實際應用中需要根據交易所API獲取精度
        # 或者從配置中讀取
        # 假設精度為 N 位小數
        # quantity = round(quantity, N)
        # 這裡使用從 config 讀取的精度 N
        quantity = round(quantity, QUANTITY_PRECISION)
        print(f"計算下單數量: 可用餘額={available_balance:.4f}, 交易資金={trade_capital:.4f}, 合約價值={contract_value:.4f}, 當前價格={current_price:.2f}, 計算數量={quantity:.3f}")
        return quantity
    else:
        print("錯誤：當前價格無效")
        return 0

# === 交易策略核心邏輯 === #
def execute_trading_strategy(api_key, secret_key, symbol, margin_coin, wallet_percentage, leverage, rsi_buy_signal, breakout_lookback, atr_multiplier):
    global win_count, loss_count, current_pos_entry_type, current_stop_loss_price, current_position_id_global
    buy_signal = False # 初始化买入信号
    close_long_signal = False # 初始化平多信号
    print(f"執行交易策略: {symbol}")

    try:
        # 1. 獲取最新的K線數據
        # fetch_ohlcv 會直接使用從 config 導入的 SYMBOL
        ohlcv_data = fetch_ohlcv(api_key, secret_key)


        # 將數據轉換為 Pandas DataFrame
        df = pd.DataFrame(ohlcv_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # 2. 計算技術指標
        # 將 api_key, secret_key, symbol 傳遞給 compute_indicators
        df = compute_indicators(df, RSI_LEN, ATR_LEN, BREAKOUT_LOOKBACK, api_key, secret_key, symbol)


        # 獲取最新的指標值
        latest_close = df['close'].iloc[-1]
        latest_rsi = df['rsi'].iloc[-1]
        latest_highest_break = df['highest_break'].iloc[-1]
        latest_atr = df['atr'].iloc[-1]

        print(f"最新數據: 收盤價={latest_close:.2f}, RSI={latest_rsi:.2f}, 突破高點={latest_highest_break:.2f}, ATR={latest_atr:.4f}")

        # 3. 檢查當前持倉狀態
        # 確保 get_current_position_details 也能處理錯誤並通知 Discord
        current_pos_side, current_pos_qty_str, current_position_id, current_unrealized_pnl = get_current_position_details(api_key, secret_key, symbol, margin_coin)
        current_pos_qty = float(current_pos_qty_str) if current_pos_qty_str else 0.0

        # Define time filter dates (using datetime objects for easier comparison)
        # Pine Script uses timestamp(YYYY, M, D, H, M), Python uses datetime
        # Note: Python datetime objects are timezone-aware or naive. Using naive for simplicity, assuming UTC or exchange time.
        import datetime
        START_DATE = datetime.datetime(2025, 1, 1, 0, 0)
        END_DATE = datetime.datetime(2025, 12, 31, 23, 59)

        # Get the timestamp of the latest K-line
        latest_timestamp_ms = df['timestamp'].iloc[-1].timestamp() * 1000 # Convert datetime to timestamp in ms
        latest_datetime = datetime.datetime.fromtimestamp(latest_timestamp_ms / 1000) # Convert ms timestamp back to datetime

        # Check if current time is within the live trading period
        is_live = START_DATE <= latest_datetime <= END_DATE
        print(f"[Time Filter] Current time: {latest_datetime}, Is Live: {is_live}")

        # 4. 判斷交易信號並執行操作
        # 計算止損和止盈價格 (基於 ATR)，與 Pine Script 一致
        # Pine Script: stop=close - atr * stopMult, limit=close + atr * limitMult
        stop_loss_long = latest_close - latest_atr * STOP_MULT # 使用 STOP_MULT 參數
        take_profit_long = latest_close + latest_atr * LIMIT_MULT # 使用 LIMIT_MULT 參數
        # 暫時不實現空單策略的止損止盈
        # stop_loss_short = latest_close + latest_atr * STOP_MULT
        # take_profit_short = latest_close - latest_atr * LIMIT_MULT

        # 檢查是否達到爆倉閾值 (假設爆倉閾值為 -100% PNL)
        if current_pos_side in ["long", "short"] and current_unrealized_pnl is not None:
            # 需要獲取開倉保證金來計算收益率，這裡暫時無法獲取，先跳過爆倉判斷
            # 實際應用中需要修改 get_current_position_details 或其他方式獲取開倉保證金
            pass # 暫時跳過爆倉判斷

        # 開多單條件
        # 條件1: RSI 反轉 (RSI > RSI_BUY) (Pine: rsi > rsiBuy)
        # rsi_buy_signal is the value of RSI_BUY from config.py, passed as an argument
        rsi_long_entry_condition = latest_rsi > rsi_buy_signal

        # 條件2: 突破進場 (close > highestBreak) (Pine: close > highestBreak)
        # latest_highest_break is ta.highest(close[1], breakoutLen)
        # breakout_lookback is BREAKOUT_LOOKBACK from config.py, passed as an argument
        breakout_long_entry_condition = latest_close > latest_highest_break
        
        # 綜合開多單信號 (任一條件滿足) (Pine: if rsiLong or longBreak)
        # 添加時間過濾條件
        buy_signal = is_live and (rsi_long_entry_condition or breakout_long_entry_condition)
        
        open_signal_reason = "" # Initialize reason string

        # 開空單條件：暫時不實現
        sell_signal = False # 暫時不實現空單策略

        # 平多單條件：RSI 下穿 EXIT_RSI (Pine: rsiLongExit = rsi < exitRSI)
        # EXIT_RSI is imported from config.py and should be accessible globally
        close_long_signal = (current_pos_side == "long") and (latest_rsi < EXIT_RSI)

        # 平空單條件：暫時不實現
        close_short_signal = False # (current_pos_side == "short") and (latest_rsi > (100 - EXIT_RSI)) # 暫時不實現空單策略

        # 執行交易
        if buy_signal and current_pos_side is None:
            # Determine the reason for the signal for Discord message
            signal_details = []
            if rsi_long_entry_condition: # Check original conditions for reason
                signal_details.append("RSI Mean Reversion")
            if breakout_long_entry_condition: # Check original conditions for reason
                signal_details.append("Breakout Entry")
            open_signal_reason = " & ".join(signal_details) if signal_details else "Signal Triggered"
            
            print(f"觸發開多信號 ({open_signal_reason})")
            # 確保 calculate_trade_size 也能處理錯誤並通知 Discord
            trade_size = calculate_trade_size(api_key, secret_key, symbol, wallet_percentage, leverage, latest_close)
            if trade_size > 0:
                print(f"準備開多單，數量: {trade_size}")
                order_result = send_order(api_key, secret_key, symbol, margin_coin, "open_long", trade_size, leverage)
                if order_result and "error" not in order_result:
                    send_discord_message("🟢 **開多成功** 🟢", api_key, secret_key, operation_details={
                        "type": "open_success",
                        "side_opened": "long",
                        "qty": trade_size,
                        "entry_price": latest_close, # 這裡使用當前收盤價作為估計開倉價
                        "signal": open_signal_reason, # Updated signal reason
                        "force_send": True # 強制發送
                    })

                    # 獲取新開倉的 positionId (假設API響應中包含此信息)
                    new_position_id = order_result.get("data", {}).get("positionId") # 需要根據實際API響應結構調整

                    if new_position_id:
                        print(f"成功開多單，positionId: {new_position_id}")
                        # 更新全域持倉變數
                        current_position_id_global = new_position_id
                        current_pos_entry_type = "rsi" if rsi_long_entry_condition else "breakout" # 記錄進場類型

                        # 根據觸發信號設置 ATR 相關的出場訂單
                        if rsi_long_entry_condition: # 如果是 RSI 觸發的進場
                            print(f"RSI 進場觸發，設置止損止盈訂單: SL={stop_loss_long:.4f}, TP={take_profit_long:.4f}")
                            # 呼叫函數設置止損止盈
                            place_conditional_orders(api_key, secret_key, symbol, margin_coin, new_position_id, stop_price=stop_loss_long, limit_price=take_profit_long)
                            current_stop_loss_price = stop_loss_long # 記錄初始止損價格

                        # 注意：Bitunix API 的 Position TP/SL 端點 (/api/v1/futures/tpsl/place_order) 不支持設置移動止損 (Trailing Stop)。<mcreference link="https://openapidoc.bitunix.com/doc/tp_sl/place_position_tp_sl_order.html" index="1">1</mcreference>
                        # 因此，對於突破進場，我們需要手動實現移動止損邏輯。
                        if breakout_long_entry_condition: # 如果是突破觸發的進場
                            print("突破進場觸發，將手動實現移動止損邏輯。")
                            # 突破進場時，設置初始止損為 ATR 止損價
                            place_conditional_orders(api_key, secret_key, symbol, margin_coin, new_position_id, stop_price=stop_loss_long)
                            current_stop_loss_price = stop_loss_long # 記錄初始止損價格

                    else:
                        print("警告：無法從訂單結果中獲取 positionId，無法設置條件訂單")

                    # 勝負統計邏輯應在平倉時判斷，這裡暫時不修改
                    # win_count += 1
                    # save_stats()
                else:
                     send_discord_message("🔴 **開多失敗** 🔴", api_key, secret_key, operation_details={
                        "type": "error",
                        "details": order_result.get("error", "未知錯誤"),
                        "signal": open_signal_reason, # Updated signal reason
                        "force_send": True # 強制發送
                    })
                     # 勝負統計邏輯應在平倉時判斷，這裡暫時不修改
                     # loss_count += 1
                     # save_stats()
            else:
                print("計算下單數量為 0，不執行開多操作")

    except Exception as e:
        error_msg = f"執行交易策略時發生未知錯誤: {e}"
        print(f"錯誤：{error_msg}")

    # === 移動止損邏輯 (僅適用於突破進場的多單) ===
    if current_pos_side == "long" and current_pos_entry_type == "breakout" and current_position_id_global:
        print("檢查移動止損條件...")
        # 計算潛在的新止損價格 (當前收盤價 - ATR * STOP_MULT)
        potential_new_stop_loss = latest_close - latest_atr * STOP_MULT

        # 如果潛在的新止損價格高於當前記錄的止損價格，則更新止損
        if current_stop_loss_price is not None and potential_new_stop_loss > current_stop_loss_price:
            print(f"觸發移動止損條件: 當前止損={current_stop_loss_price:.4f}, 潛在新止損={potential_new_stop_loss:.4f}")
            # 呼叫修改訂單函數更新止損價格
            modify_result = modify_position_tpsl(api_key, secret_key, symbol, current_position_id_global, stop_price=potential_new_stop_loss)

            if modify_result and "error" not in modify_result:
                print(f"成功更新移動止損至 {potential_new_stop_loss:.4f}")
                current_stop_loss_price = potential_new_stop_loss # 更新記錄的止損價格
                send_discord_message(f"⬆️ **移動止損更新** ⬆️", api_key, secret_key, operation_details={
                    "type": "status_update",
                    "details": f"持倉 {current_position_id_global} 的新止損價格: {potential_new_stop_loss:.4f}",
                    "force_send": True # 強制發送
                })
            else:
                print(f"更新移動止損失敗: {modify_result.get('error', '未知錯誤')}")
                send_discord_message(f"🔴 **移動止損更新失敗** 🔴", api_key, secret_key, operation_details={
                    "type": "error",
                    "details": f"更新持倉 {current_position_id_global} 的移動止損失敗: {modify_result.get('error', '未知錯誤')}",
                    "force_send": True # 強制發送
                })
        else:
             print("移動止損條件未滿足或價格未朝有利方向移動")

    # 檢查平多信號
    if close_long_signal and current_pos_side == "long":
        print("觸發平多信號")
        if current_pos_qty > 0 and current_position_id:
            print(f"準備平多單，數量: {current_pos_qty}")
            # 在平倉前獲取一次餘額，用於計算本次交易的已實現盈虧
            balance_before_close = check_wallet_balance(api_key, secret_key)
            order_result = send_order(api_key, secret_key, symbol, margin_coin, "close_long", current_pos_qty, position_id=current_position_id)
            if order_result and "error" not in order_result:
                # 平倉成功後再次獲取餘額，計算已實現盈虧
                balance_after_close = check_wallet_balance(api_key, secret_key)
                realized_pnl = balance_after_close - balance_before_close if balance_before_close is not None else None

                send_discord_message("🟠 **平多成功** 🟠", api_key, secret_key, operation_details={
                    "type": "close_success",
                    "side_closed": "long",
                    "qty": current_pos_qty,
                    "pnl": realized_pnl,
                    "force_send": True # 強制發送
                })
                # 勝負判斷邏輯：如果已實現盈虧 > 0 則為勝，否則為敗
                if realized_pnl is not None:
                    if realized_pnl > 0:
                        win_count += 1
                    else:
                        loss_count += 1
                    save_stats()

                # 平倉成功後重置全域持倉變數
                current_pos_entry_type = None
                current_stop_loss_price = None
                current_position_id_global = None

            else:
                 send_discord_message("🔴 **平多失敗** 🔴", api_key, secret_key, operation_details={
                    "type": "error",
                    "details": order_result.get("error", "未知錯誤"),
                    "force_send": True # 強制發送
                })
                 # 平倉失敗不計入勝敗統計
        else:
            print("無多單持倉或 positionId 無效，不執行平多操作")


    # 暫時不實現空單策略的開倉和平倉
    # elif sell_signal and current_pos_side is None:
    #     print("觸發開空信號")
    #     ...
    # elif close_short_signal and current_pos_side == "short":
    #     print("觸發平空信號")
    #     ...

    else:
        print("無交易信號或已有持倉")

    # 5. 繪製圖表並發送 Discord 通知 (如果需要)
    # 這裡可以添加繪製K線圖、指標和交易信號的邏輯
    # 並在有交易發生時或定時發送圖表到 Discord
    # 為了簡化，暫時不實現圖表功能
    # pass

# === Discord Bot 設定與啟動 === #
# 這裡保留 Discord Bot 的基本結構，但移除與舊通道策略圖表相關的邏輯

# 載入統計數據
load_stats()

# 創建 Discord Bot 實例
intents = discord.Intents.default()
intents.message_content = True # 需要這個權限來讀取訊息內容
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print('------')
    # 啟動定時任務
    trade_task.start()
    balance_check_task.start() # 啟動餘額檢查任務
    # 在啟動時發送一條通知 (此訊息已移至 main 函數，並包含啟動圖表)
    # send_discord_message("🚀 交易機器人已啟動！🚀", BITUNIX_API_KEY, BITUNIX_SECRET_KEY, SYMBOL, operation_details={"force_send": True})

@tasks.loop(minutes=1) # 每分鐘執行一次交易策略
async def trade_task():
    print("執行定時交易任務...")
    try:
        # 使用從 config 導入的正確參數名
        execute_trading_strategy(BITUNIX_API_KEY, BITUNIX_SECRET_KEY, SYMBOL, MARGIN_COIN, WALLET_PERCENTAGE, LEVERAGE, RSI_BUY, BREAKOUT_LOOKBACK, ATR_MULT)
        flush_discord_messages() # 每次任務結束後強制發送緩衝區消息
    except Exception as e:
        print(f"交易任務執行錯誤: {e}")
        send_discord_message(f"🔴 **交易任務錯誤**: {e} 🔴", BITUNIX_API_KEY, BITUNIX_SECRET_KEY, operation_details={"type": "error", "details": str(e), "force_send": True})
        flush_discord_messages()

@tasks.loop(minutes=5) # 每5分鐘檢查一次餘額
async def balance_check_task():
    print("執行定時餘額檢查任務...")
    try:
        check_wallet_balance(BITUNIX_API_KEY, BITUNIX_SECRET_KEY)
        flush_discord_messages() # 每次任務結束後強制發送緩衝區消息
    except Exception as e:
        print(f"餘額檢查任務執行錯誤: {e}")

# 移除舊的繪圖命令
# @bot.command(name='plot')
# async def plot_command(ctx):
#     await ctx.send("繪圖功能已更新，請等待自動通知。")

# 移除舊的通道繪圖函數
# def plot_channel_and_send_to_discord(...):
#     pass # 函數內容已移除

def plot_strategy_and_send_to_discord(df, latest_close, latest_rsi, latest_highest_break, latest_atr, buy_signal, close_long_signal, api_key, secret_key, custom_message=None, force_send_message=False):

    """繪製K線圖、指標和交易信號，並發送到Discord"""
    try:
        # 設定中文字體，解決標題亂碼問題
        # 嘗試使用多種常見中文字體
        chinese_fonts = ['SimHei', 'Microsoft YaHei', 'STSong', 'FangSong']
        font_set = False
        for font in chinese_fonts:
            try:
                plt.rcParams['font.sans-serif'] = [font]
                plt.rcParams['axes.unicode_minus'] = False  # 解決座標軸負號顯示問題
                print(f"成功設定字體為: {font}")
                font_set = True
                break # 成功設定後跳出迴圈
            except Exception as e_font:
                print(f"設定字體 {font} 失敗: {e_font}")
                continue # 嘗試下一個字體
        
        if not font_set:
            print("警告: 未找到支援中文的字體，圖表標題和標籤可能顯示亂碼。")

        print(f"開始繪製圖表函數: {SYMBOL}") # 添加日誌
        # 確保有足夠的數據繪圖
        print(f"[Plotting] 繪製圖表使用的交易對符號: {SYMBOL}") # 添加日誌
        if len(df) < max(BREAKOUT_LOOKBACK, ATR_LEN, RSI_LEN) + 2:
            print("數據不足，無法繪製圖表")
            return

        # 準備 mplfinance 需要的數據
        # mplfinance 需要 datetime index
        df_plot = df.copy()
        df_plot = df_plot.set_index('timestamp')

        # 創建 addplots 列表
        apds = [
            # RSI 主線 (藍色)
            mpf.make_addplot(df_plot['rsi'], panel=1, color='blue', width=1.2, ylabel='RSI'),
            # RSI Buy 水平線 (綠色虛線)
            mpf.make_addplot(pd.Series(RSI_BUY, index=df_plot.index), panel=1, color='green', linestyle='dashed', width=1.2),
            # RSI Sell 水平線 (紅色虛線) - 使用 EXIT_RSI
            mpf.make_addplot(pd.Series(EXIT_RSI, index=df_plot.index), panel=1, color='red', linestyle='dashed', width=1.2),
            # ATR 指標 (紫色)
            mpf.make_addplot(df_plot['atr'], panel=2, color='purple', width=1.2, ylabel='ATR'),
            # 突破高點線 (紫色虛線) - 範例圖未顯示，故註釋掉
            # mpf.make_addplot(df_plot['highest_break'], color='purple', linestyle='--', panel=0, width=1.2)
        ]

        # 標記交易信號 - 為了與範例圖一致，範例圖中沒有在主圖上用大箭頭標註買賣點，故註釋掉
        # buy_points = []
        # sell_points = [] 
        # close_long_points = []
        # if buy_signal:
        #      buy_points.append(df_plot.index[-1])
        # if close_long_signal:
        #      close_long_points.append(df_plot.index[-1])
        # if buy_points:
        #     apds.append(mpf.make_addplot(df_plot['close'], type='scatter', marker='^', markersize=100, color='green', panel=0))
        # if close_long_points:
        #      apds.append(mpf.make_addplot(df_plot['close'], type='scatter', marker='v', markersize=100, color='red', panel=0))

        # 定義一個類似幣安深色模式的自訂樣式
        # 參考: https://github.com/matplotlib/mplfinance/issues/614
        binance_dark_style = {
            "base_mpl_style": "dark_background",
            "marketcolors": {
                "candle": {"up": "#3dc985", "down": "#ef4f60"},  
                "edge": {"up": "#3dc985", "down": "#ef4f60"},  
                "wick": {"up": "#3dc985", "down": "#ef4f60"},  
                "ohlc": {"up": "green", "down": "red"},
                "volume": {"up": "#247252", "down": "#82333f"},  
                "vcedge": {"up": "green", "down": "red"},  
                "vcdopcod": False,
                "alpha": 1,
            },
            "mavcolors": ("#ad7739", "#a63ab2", "#62b8ba"),
            "facecolor": "#1b1f24",
            "gridcolor": "#2c2e31",
            "gridstyle": "--",
            "y_on_right": True,
            "rc": {
                "axes.grid": True,
                "axes.grid.axis": "y",
                "axes.edgecolor": "#474d56",
                "axes.titlecolor": "red",
                "figure.facecolor": "#161a1e",
                "figure.titlesize": "x-large",
                "figure.titleweight": "semibold",
            },
            "base_mpf_style": "binance-dark",
        }

        # 繪製圖表
        fig, axes = mpf.plot(df_plot, 
                             type='candle', 
                             style=binance_dark_style,  # 使用自訂的深色樣式
                             title=f'{SYMBOL} {TIMEFRAME} K線圖與指標',  # 更新圖表標題以匹配 config.py 中的 TIMEFRAME
                             ylabel='Price',  # 主圖 Y 軸標籤
                             volume=False,  # 範例圖中沒有成交量
                             addplot=apds, 
                             panel_ratios=(6, 2, 2),  # 調整面板比例 (主圖:RSI:ATR)
                             figscale=1.5, 
                             returnfig=True,
                             tight_layout=True) # 使用 tight_layout 使圖表更緊湊

        # 添加圖例到指標面板
        from matplotlib.lines import Line2D # 確保 Line2D 已導入

        # RSI Panel Legend
        if len(axes) > 1 and axes[1] is not None: # axes[1] 對應 panel=1 (RSI)
            legend_elements_rsi = [
                Line2D([0], [0], color='blue', lw=1.2, label=f'RSI ({RSI_LEN})'),
                Line2D([0], [0], color='green', linestyle='dashed', lw=1.2, label=f'RSI Buy ({RSI_BUY})'),
                Line2D([0], [0], color='red', linestyle='dashed', lw=1.2, label=f'RSI Sell ({EXIT_RSI})')
            ]
            axes[1].legend(handles=legend_elements_rsi, loc='best', fontsize='small')
            # axes[1].set_ylabel('RSI') # ylabel 已經在 make_addplot 中設定

        # ATR Panel Legend
        if len(axes) > 2 and axes[2] is not None: # axes[2] 對應 panel=2 (ATR)
            legend_elements_atr = [
                Line2D([0], [0], color='purple', lw=1.2, label=f'ATR ({ATR_LEN})')
            ]
            axes[2].legend(handles=legend_elements_atr, loc='best', fontsize='small')
            # axes[2].set_ylabel('ATR') # ylabel 已經在 make_addplot 中設定
        
        # 主圖表的 K 線圖例 (Close, High, Low) 通常由 style='charles' 提供。
        # 如果 style 未能正確顯示，可能需要額外處理，但通常 'charles' 風格會包含。

        # 保存圖表到臨時文件
        # 使用絕對路徑確保文件位置的明確性
        image_filename = f'{SYMBOL}_strategy_plot_{int(time.time())}.png' # 添加時間戳以避免文件名衝突
        image_path = os.path.abspath(image_filename) # 獲取絕對路徑
        print(f"[Plotting] 準備保存圖表到絕對路徑: {image_path}") # 修改日誌
        # 儲存圖表到記憶體中的 BytesIO 物件
        import io
        buffer = io.BytesIO()
        print("[Plotting] 準備儲存圖表到記憶體緩衝區...")
        try:
            fig.savefig(buffer, format='png', dpi=100, bbox_inches='tight') # bbox_inches='tight' 嘗試裁剪空白邊緣
            plt.close(fig) # 關閉圖形以釋放記憶體
            buffer.seek(0) # 將緩衝區位置重置到開頭
            image_data = buffer.getvalue()
            buffer.close() # 關閉緩衝區
            print("[Plotting] 圖表已儲存到記憶體緩衝區。")
        except Exception as e_save:
            print(f"[Plotting] 儲存圖表到記憶體失敗: {e_save}")
            plt.close(fig) # 確保關閉圖形
            return # 儲存失敗則不繼續發送

        # 準備 Discord 消息內容
        # 根據需求，繪圖函數不再主動發送通知，只在被需要時返回圖片數據

        # Determine message content and force send status based on context
        message_core = None
        operation_type = None
        current_should_send_forced = force_send_message # Start with the function's force_send_message flag

        if custom_message:
            # Handle custom messages (like startup)
            message_core = custom_message
            operation_type = "custom_message" # Or a more specific type if needed
            current_should_send_forced = True # Custom messages are likely important and should be forced

        elif buy_signal:
            # Handle buy signal chart
            message_core = f"📈 **{SYMBOL} 買入信號出現!** 📈\n價格: {latest_close:.2f}, RSI: {latest_rsi:.2f}"
            operation_type = "buy_signal_chart"
            current_should_send_forced = True # Trade signal charts are always force-sent

        elif close_long_signal:
            # Handle close long signal chart
            message_core = f"📉 **{SYMBOL} 平倉信號出現!** 📉\n價格: {latest_close:.2f}, RSI: {latest_rsi:.2f}"
            operation_type = "close_long_signal_chart"
            current_should_send_forced = True # Trade signal charts are always force-sent

        else:
            # Handle general status updates or no-send cases
            if current_should_send_forced:
                # If force_send_message was True but no specific signal/custom message
                message_core = f"📊 **{SYMBOL} 市場狀態更新 (依請求)** 📊\n價格: {latest_close:.2f}, RSI: {latest_rsi:.2f}"
                operation_type = "status_update_chart_forced"
            else:
                # If force_send_message was False and no specific signal/custom message
                print(f"[Plotting] 非啟動/交易信號，且未強制發送 (force_send_message={force_send_message})，將不發送此圖表更新。")
                return # Do not send

        # Only send if message_core is not None (meaning a message was constructed)
        if message_core:
            print(f"[Plotting] 準備發送 Discord 訊息。核心內容: '{message_core[:100]}...'，圖片數據長度: {len(image_data) if image_data else 0}, 強制發送標記: {current_should_send_forced}")
        send_discord_message(message_core, api_key, secret_key, operation_details={
            "type": operation_type,
            "image_data": image_data, # 傳遞圖片數據
            "image_filename": f'{SYMBOL}_strategy_plot_{int(time.time())}.png', # 提供一個文件名
            "force_send": current_should_send_forced
        })
        print(f"[Plotting] Discord 訊息 (含圖表) 已請求發送。類型: {operation_type}, 強制: {current_should_send_forced}")

    except Exception as e:
        print(f"錯誤：繪製或發送圖表時發生錯誤: {e}")
        # 如果在儲存到記憶體後但在發送前發生錯誤，不需要刪除文件
        pass # 不再需要文件清理邏輯

if __name__ == "__main__":
    load_stats()

    pass # 如果依賴 Discord Bot 的 tasks.loop，則在此處等待 Bot 啟動

current_wallet_balance = 0.0

def check_wallet_balance(api_key, secret_key):
    global last_balance, current_wallet_balance
    margin_coin = MARGIN_COIN # 從 config 導入
    query_params = {"marginCoin": margin_coin}
    path = "/api/v1/futures/account"
    url = f"https://fapi.bitunix.com{path}?marginCoin={margin_coin}"
    
    # 使用更新後的get_signed_params獲取完整的headers，指定method為GET
    _, _, _, headers = get_signed_params(api_key, secret_key, query_params, method="GET")

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Check if request was successful

        # Log the full response for debugging
        print(f"Response from API: {response.text}")

        balance_info = response.json()
        current_balance = None

        # Check if 'data' is in the response
        if "data" in balance_info and balance_info["data"] is not None:
            print(f"完整的數據結構: {balance_info['data']}")
            if isinstance(balance_info["data"], dict):
                account_data = balance_info["data"]
                available_balance = float(account_data.get("available", 0))
                margin_balance = float(account_data.get("margin", 0))
                cross_unrealized_pnl = float(account_data.get("crossUnrealizedPNL", 0))
                isolation_unrealized_pnl = float(account_data.get("isolationUnrealizedPNL", 0))
                total_unrealized_pnl = cross_unrealized_pnl + isolation_unrealized_pnl
                total_asset = available_balance + margin_balance + total_unrealized_pnl

                # 檢查總資產是否發生變化，或者根據需要調整觸發邏輯
                # 暫時修改為只要獲取到有效數據就發送更新
                print(f"已獲取並發送餘額信息: 可用 {available_balance}, 保證金 {margin_balance}, 未實現盈虧 {total_unrealized_pnl}, 總資產 {total_asset}")

                # 更新 last_balance 和 current_wallet_balance (這裡可能需要重新考慮這些變數的用途)
                # 如果 last_balance 僅用於觸發餘額更新消息，現在邏輯已改變，可以移除或修改其用途
                # 暫時保留 current_wallet_balance，但其含義可能需要根據實際使用情況調整
                current_wallet_balance = available_balance # 暫時將錢包餘額設為可用餘額
                return available_balance # 返回可用餘額
            else:
                error_message = "餘額數據格式不正確"
                print(f"餘額查詢錯誤: {error_message}, 原始數據: {balance_info['data']}")
                return current_wallet_balance # 返回上一次的餘額或初始值
        else:
            error_message = balance_info.get("message", "無法獲取餘額信息")
            return current_wallet_balance # 返回上一次的餘額或初始值
    except requests.exceptions.HTTPError as err:
        print(f"HTTP Error: {err}")
        return current_wallet_balance
    except requests.exceptions.RequestException as err:
        print(f"Request Exception: {err}")
        return current_wallet_balance

# === 查詢持倉狀態 === #
def get_current_position_details(api_key, secret_key, symbol, margin_coin=MARGIN_COIN): # 使用 MARGIN_COIN from config as default
    """查詢目前持倉的詳細信息，包括方向、數量、positionId 和未實現盈虧。"""
    import hashlib, uuid, time, requests

    url = "https://fapi.bitunix.com/api/v1/futures/position/get_pending_positions"
    params = {"symbol": symbol}
    nonce = uuid.uuid4().hex
    timestamp = str(int(time.time() * 1000))
    
    sorted_items = sorted((k, str(v)) for k, v in params.items())
    query_string = "".join(f"{k}{v}" for k, v in sorted_items)

    digest_input = nonce + timestamp + api_key + query_string
    digest = hashlib.sha256(digest_input.encode('utf-8')).hexdigest()
    sign = hashlib.sha256((digest + secret_key).encode('utf-8')).hexdigest()

    headers = {
        "api-key": api_key,
        "sign": sign,
        "nonce": nonce,
        "timestamp": timestamp,
        "Content-Type": "application/json"
    }
    try:
        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        if data.get("code") == 0 and data.get("data"):
            for pos_detail in data["data"]:
                pos_qty_str = pos_detail.get("qty", "0")
                position_id = pos_detail.get("positionId")
                unrealized_pnl = float(pos_detail.get("unrealizedPNL", 0.0)) # 獲取未實現盈虧
                
                if float(pos_qty_str) > 0: # 只處理有實際數量的倉位
                    if pos_detail.get("side") == "BUY":
                        print(f"API偵測到多單持倉: qty={pos_qty_str}, positionId={position_id}, PNL={unrealized_pnl}")
                        return "long", pos_qty_str, position_id, unrealized_pnl
                    if pos_detail.get("side") == "SELL":
                        print(f"API偵測到空單持倉: qty={pos_qty_str}, positionId={position_id}, PNL={unrealized_pnl}")
                        return "short", pos_qty_str, position_id, unrealized_pnl
        # print("API未偵測到有效持倉或回傳數據格式問題。") # 可以根據需要取消註釋
        return None, None, None, 0.0  # 無持倉或錯誤，PNL返回0.0
    except Exception as e:
        print(f"查詢持倉詳細失敗: {e}")
        return None, None, None, 0.0

order_points = []  # 全域下單點記錄

def plot_channel_and_send_to_discord(ohlcv, upperBand, lowerBand, middleBand, last, message, order_points=None):
    import mplfinance as mpf
    import pandas as pd
    import numpy as np
    import os

    print("DEBUG: order_points 傳入內容：", order_points)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('datetime', inplace=True)
    df = df[['open', 'high', 'low', 'close', 'volume']]

    apds = [
        mpf.make_addplot(upperBand, color='#00FFFF', width=1.2),
        mpf.make_addplot(lowerBand, color='#FFFF00', width=1.2),
        mpf.make_addplot(middleBand, color='#FF00FF', width=1.0, linestyle='dashed')
    ]

    def mark_orders(ax):
        if order_points:
            for pt in order_points:
                print("DEBUG: 標註點", pt)
                if 0 <= pt['idx'] < len(df):
                    dt = df.index[pt['idx']]
                    price = pt['price']
                    color = '#39FF14' if pt['side'] == 'long' else '#FF1744'  # 螢光綠/亮紅
                    marker = '^' if pt['side'] == 'long' else 'v'
                    offset = -40 if pt['side'] == 'long' else 40
                    ax.scatter(dt, price, color=color, marker=marker, s=400, zorder=10, edgecolors='black', linewidths=2)
                    ax.annotate(
                        f"{pt['side'].upper()}",
                        (dt, price),
                        textcoords="offset points",
                        xytext=(0, offset),
                        ha='center',
                        color=color,
                        fontsize=16,
                        fontweight='bold',
                        bbox=dict(boxstyle='round,pad=0.4', fc='black', ec=color, lw=3, alpha=0.95),
                        arrowprops=dict(arrowstyle='->', color=color, lw=3, alpha=0.8)
                    )

    img_path = 'channel_candle.png'
    fig, axlist = mpf.plot(
        df,
        type='candle',
        style='charles',
        addplot=apds,
        figsize=(16, 8),
        title='通道指標蠟燭圖',
        ylabel='價格',
        returnfig=True,
        tight_layout=True,
        update_width_config=dict(candle_linewidth=1.2, candle_width=0.6)
    )
    mark_orders(axlist[0])
    fig.savefig(img_path, facecolor='black')
    
    # 呼叫更新後的 send_discord_message 函數，並傳遞圖片路徑
    send_discord_message(message, BITUNIX_API_KEY, BITUNIX_SECRET_KEY, operation_details={"image_path": img_path, "force_send": True}) # force_send 確保圖片立即發送
    # 注意：send_discord_message 內部會負責刪除圖片文件


def main():
    global win_count, loss_count # 宣告使用全域變數
    load_stats() # 啟動時載入統計數據

    # 用戶參數
    from config import TRADING_PAIR, SYMBOL, MARGIN_COIN, LEVERAGE, WALLET_PERCENTAGE, RSI_LEN, ATR_LEN, BREAKOUT_LOOKBACK, STOP_MULT, LIMIT_MULT, RSI_BUY, EXIT_RSI, ATR_MULT, TIMEFRAME
    api_key = BITUNIX_API_KEY # 從 config 導入
    secret_key = BITUNIX_SECRET_KEY # 從 config 導入
    # trading_pair 變數不再需要在 main 中單獨定義，直接使用導入的 TRADING_PAIR 或 SYMBOL
    symbol = SYMBOL # SYMBOL 已經從 config 導入
    margin_coin = MARGIN_COIN # 從 config 導入
    leverage = LEVERAGE
    wallet_percentage = WALLET_PERCENTAGE

    current_pos_side = None
    current_pos_qty = None
    # win_count 和 loss_count 由 load_stats() 初始化，此處無需重置為0
    # win_count = 0
    # loss_count = 0
    last_upper_band = None
    last_lower_band = None
    last_middle_band = None
    
    print("交易機器人啟動，開始載入初始K線數據並準備生成啟動圖表...")
    # 原啟動訊息已移除，將由包含圖表的訊息替代

    # 獲取初始K線數據用於繪圖
    # fetch_ohlcv 會直接使用從 config 導入的 SYMBOL
    ohlcv_data = fetch_ohlcv(api_key, secret_key)

    min_data_len = max(RSI_LEN, ATR_LEN, BREAKOUT_LOOKBACK + 1) + 5 # +1 for shift, +5 for buffer
    if ohlcv_data is None or len(ohlcv_data) < min_data_len:
        error_detail_msg = f"需要至少 {min_data_len} 條數據，實際獲取 {len(ohlcv_data) if ohlcv_data is not None else 0} 條。"
        send_discord_message(f"🔴 啟動失敗：無法獲取足夠的初始K線數據繪製圖表。{error_detail_msg}", api_key, secret_key, operation_details={"type": "error", "details": f"Insufficient initial K-line data for chart. {error_detail_msg}", "force_send": True})
        return

    df = pd.DataFrame(ohlcv_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    # 可選擇截取最近一部分數據進行繪圖，避免過長的歷史數據影響圖表可讀性
    # df = df.iloc[-min_data_len*2:] 

    df_for_plot = compute_indicators(df.copy(), RSI_LEN, ATR_LEN, BREAKOUT_LOOKBACK, api_key, secret_key, symbol)
    if df_for_plot is None or df_for_plot.empty:
        send_discord_message("🔴 啟動失敗：計算初始指標失敗，無法繪製圖表。", api_key, secret_key, operation_details={"type": "error", "details": "Failed to compute initial indicators for chart", "force_send": True})
        return

    if df_for_plot['rsi'].isnull().all() or df_for_plot['atr'].isnull().all():
        send_discord_message("🔴 啟動失敗：計算出的初始指標包含過多無效值 (NaN)，無法繪製圖表。", api_key, secret_key, operation_details={"type": "error", "details": "Computed initial indicators are mostly NaN, cannot plot chart.", "force_send": True})
        return

    latest_close = df_for_plot['close'].iloc[-1]
    latest_rsi = df_for_plot['rsi'].iloc[-1]
    latest_highest_break = df_for_plot['highest_break'].iloc[-1] if 'highest_break' in df_for_plot.columns and pd.notna(df_for_plot['highest_break'].iloc[-1]) else None
    latest_atr = df_for_plot['atr'].iloc[-1]
    
    if pd.isna(latest_close) or pd.isna(latest_rsi) or pd.isna(latest_atr):
        send_discord_message("🔴 啟動失敗：獲取的最新指標數據包含無效值 (NaN)，無法繪製圖表。", api_key, secret_key, operation_details={"type": "error", "details": "Latest indicator data contains NaN, cannot plot chart.", "force_send": True})
        return

    print(f"[Main Startup] 準備繪製啟動圖表... 最新收盤價: {latest_close:.2f}, RSI: {latest_rsi:.2f}, ATR: {latest_atr:.4f}")
    # 使用 df_for_plot 進行繪圖
    plot_strategy_and_send_to_discord(
        df_for_plot, latest_close, latest_rsi,
        latest_highest_break, 
        latest_atr,
        buy_signal=False, close_long_signal=False, 
        api_key=api_key, secret_key=secret_key,
        custom_message=f"""🚀 交易機器人啟動 🚀
策略參數:
STOP_MULT: {STOP_MULT}
LIMIT_MULT: {LIMIT_MULT}
RSI_BUY: {RSI_BUY}
RSI_LEN: {RSI_LEN}
EXIT_RSI: {EXIT_RSI}
BREAKOUT_LOOKBACK: {BREAKOUT_LOOKBACK}
ATR_LEN: {ATR_LEN}
ATR_MULT: {ATR_MULT}
TIMEFRAME: {TIMEFRAME}
""",

        force_send_message=True
    )
    print(f"[Main Startup] 啟動圖表及訊息已請求發送。")

    last_kline_len = len(ohlcv_data)

    # 在主循環開始前，獲取一次當前持倉狀態 (返回四個值)
    current_pos_side, current_pos_qty_str, current_pos_id, current_unrealized_pnl = get_current_position_details(api_key, secret_key, SYMBOL, MARGIN_COIN)
    print(f"啟動時持倉狀態: side={current_pos_side}, qty={current_pos_qty_str}, positionId={current_pos_id}, PNL={current_unrealized_pnl}")
    
    # 啟動時自動補上現有持倉點 (這部分邏輯如果存在，需要確保 order_points 的更新)
    import numpy as np
    from typing import Any
    def get_entry_price_and_side(api_key: str, secret_key: str, symbol: str) -> Any:
        url = "https://fapi.bitunix.com/api/v1/futures/position/get_pending_positions"
        params = {"symbol": symbol}
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))
        api_key_ = api_key
        secret_key_ = secret_key
        sorted_items = sorted((k, str(v)) for k, v in params.items())
        query_string = "".join(f"{k}{v}" for k, v in sorted_items)
        digest_input = nonce + timestamp + api_key_ + query_string
        digest = hashlib.sha256(digest_input.encode('utf-8')).hexdigest()
        sign = hashlib.sha256((digest + secret_key_).encode('utf-8')).hexdigest()
        headers = {
            "api-key": api_key_,
            "sign": sign,
            "nonce": nonce,
            "timestamp": timestamp,
            "Content-Type": "application/json"
        }
        try:
            res = requests.get(url, headers=headers, params=params)
            data = res.json()
            if data.get("code") == 0 and data.get("data"):
                for pos in data["data"]:
                    side = None
                    if pos.get("side") == "BUY" and float(pos.get("qty", 0)) > 0:
                        side = "long"
                    elif pos.get("side") == "SELL" and float(pos.get("qty", 0)) > 0:
                        side = "short"
                    if side:
                        entry_price = float(pos.get("avgOpenPrice", pos.get("entryValue", 0)))
                        return entry_price, side
            return None
        except Exception as e:
            print(f"查詢持倉失敗: {e}")
            return None

    entry = get_entry_price_and_side(api_key, secret_key, symbol)
    if entry:
        entry_price, side = entry
        # 使用 df_for_plot 中的 'close' 數據
        close_prices = df_for_plot['close'].values
        idx = int(np.argmin(np.abs(close_prices - entry_price)))
        order_points.append({'idx': idx, 'price': close_prices[idx], 'side': side})
        print(f"DEBUG: 啟動自動補標註現有持倉點: {order_points[-1]}")

    while True:
        # 檢查錢包餘額並獲取當前餘額
        balance = check_wallet_balance(api_key, secret_key)
        # 計算下單數量 (錢包餘額的30%*槓桿/當前BTC價格)
        btc_price = None
        # 執行交易策略
        execute_trading_strategy(api_key, secret_key, symbol, margin_coin, wallet_percentage, leverage, RSI_BUY, BREAKOUT_LOOKBACK, ATR_MULT)

        # 檢查錢包餘額並獲取當前餘額 (用於下一次循環的數量計算)
        balance = check_wallet_balance(api_key, secret_key)
        if balance is None or balance <= 0:
            print("餘額為0或無法獲取餘額，退出程序")
            send_discord_message("🛑 **程序終止**: 餘額為0或無法獲取餘額，交易機器人已停止運行 🛑", SYMBOL, api_key, secret_key)
            # 在退出前強制發送所有緩衝區中的消息
            flush_discord_messages()
            print("程序已終止運行")
            return # 直接退出main函數而不是繼續循環

        # 休眠1分鐘後再次執行策略
        # 休眠指定時間後再次執行策略
        next_strategy_time = time.strftime('%H:%M:%S', time.localtime(time.time() + LOOP_INTERVAL_SECONDS))
        print(f"休眠中，將在 {next_strategy_time} 再次執行交易策略 (間隔 {LOOP_INTERVAL_SECONDS} 秒)...")
        # 在休眠前強制發送所有緩衝區中的消息
        flush_discord_messages()
        time.sleep(LOOP_INTERVAL_SECONDS) # 休眠1分鐘  # 每 1 分鐘檢查一次


if __name__ == "__main__":
    try:
        main()
    finally:
        # 確保程序結束時發送所有緩衝區中的消息
        flush_discord_messages()


def send_profit_loss_to_discord(api_key, secret_key, symbol_param, message): # Renamed symbol to symbol_param
    position = get_current_position(api_key, secret_key, symbol_param)
    if position in ['long', 'short']:
        url = "https://fapi.bitunix.com/api/v1/futures/position/get_pending_positions"
        params = {"symbol": symbol_param} # Use symbol_param
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))
        digest_input = nonce + timestamp + api_key + "symbol" + symbol_param # Use symbol_param
        digest = hashlib.sha256(digest_input.encode('utf-8')).hexdigest()
        sign = hashlib.sha256((digest + secret_key).encode('utf-8')).hexdigest()
        headers = {
            "api-key": api_key,
            "sign": sign,
            "nonce": nonce,
            "timestamp": timestamp,
            "Content-Type": "application/json"
        }
        try:
            res = requests.get(url, headers=headers, params=params)
            data = res.json()
            if data.get("code") == 0 and data.get("data"):
                for pos in data["data"]:
                    if ((position == "long" and pos.get("side") == "BUY") or
                        (position == "short" and pos.get("side") == "SELL")):
                        pnl = float(pos.get("unrealizedPNL", 0))
                        margin = float(pos.get("margin", 0))
                        if margin:
                            profit_pct = (pnl / margin) * 100
                            message += f"\n💰 盈虧: {pnl:.4f} USDT｜收益率: {profit_pct:.2f}%"
                        else:
                            message += f"\n💰 盈虧: {pnl:.4f} USDT"
        except Exception as e:
            message += f"\n查詢盈虧失敗: {e}"
    
    # 根據需求，移除持倉和盈虧更新的 Discord 通知
    pass

