#!/usr/bin/env python3
import json
import math
import os
import random
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

STATE_PATH = Path("data/bbs_state.json")
MARKET_SEED = 65000.0
MARKET_TICK_SECONDS = 60.0


class BBSState:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.state = self.load_state()

    def default_state(self):
        return {
            "market_price": MARKET_SEED,
            "last_market_update": datetime.now(timezone.utc).isoformat(),
            "users": {},
            "boards": {
                "announcements": [],
                "trading": [],
                "market": [],
                "general": [],
            },
        }

    def load_state(self):
        if not self.path.exists():
            state = self.default_state()
            self.save_state(state)
            return state

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                if not isinstance(data.get("boards"), dict):
                    raise ValueError("invalid boards")
                return data
        except (json.JSONDecodeError, OSError, ValueError):
            state = self.default_state()
            self.save_state(state)
            return state

    def save_state(self, state=None):
        payload = state if state is not None else self.state
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")

    def hash_password(self, password: str) -> str:
        return sha256(password.encode("utf-8")).hexdigest()

    def create_user(self, username: str, password: str):
        username = (username or "").strip()
        if not username:
            raise ValueError("Username cannot be empty.")
        with self.lock:
            if username in self.state["users"]:
                raise ValueError("Username is already taken.")
            self.state["users"][username] = {
                "username": username,
                "password_hash": self.hash_password(password),
                "balance_usd": 10000.0,
                "balance_btc": 0.0,
                "joined": datetime.now(timezone.utc).isoformat(),
                "last_seen": datetime.now(timezone.utc).isoformat(),
            }
            self.save_state()

    def login_user(self, username: str, password: str):
        with self.lock:
            user = self.state["users"].get(username)
            if not user:
                return None
            if user["password_hash"] != self.hash_password(password):
                return None
            user["last_seen"] = datetime.now(timezone.utc).isoformat()
            self.save_state()
            return user

    def add_post(self, board: str, author: str, message: str):
        board_name = (board or "general").strip().lower()
        with self.lock:
            if board_name not in self.state["boards"]:
                raise ValueError(f"Unknown board: {board}")
            post = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "author": author,
                "message": message.strip(),
            }
            self.state["boards"][board_name].append(post)
            self.save_state()
            return post

    def list_boards(self):
        with self.lock:
            return list(self.state["boards"].keys())

    def get_posts(self, board: str, limit: int = 10):
        board_name = (board or "general").strip().lower()
        with self.lock:
            if board_name not in self.state["boards"]:
                raise ValueError(f"Unknown board: {board}")
            posts = list(self.state["boards"][board_name])
        if limit <= 0:
            limit = len(posts)
        return list(reversed(posts[-limit:]))

    def update_market(self):
        with self.lock:
            current = float(self.state["market_price"])
            drift = random.uniform(-900.0, 900.0)
            volatility = current * 0.003
            next_price = current + drift + random.uniform(-volatility, volatility)
            next_price = max(20000.0, next_price)
            self.state["market_price"] = round(next_price, 2)
            self.state["last_market_update"] = datetime.now(timezone.utc).isoformat()
            self.save_state()
            return self.state["market_price"]

    def get_market_price(self):
        with self.lock:
            last = self.state.get("last_market_update")
            now = datetime.now(timezone.utc)
            try:
                elapsed = (now - datetime.fromisoformat(last)).total_seconds() if last else 1e9
            except (TypeError, ValueError):
                elapsed = 1e9
            if elapsed >= MARKET_TICK_SECONDS:
                self.update_market()
            return float(self.state["market_price"])

    def trade(self, username: str, side: str, btc_amount: float):
        amount = float(btc_amount)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("Trade amount must be a positive finite number.")

        with self.lock:
            user = self.state["users"].get(username)
            if not user:
                raise ValueError("User not found.")

            market_price = self.get_market_price()
            trade_value = amount * market_price

            if side.lower() == "buy":
                if user["balance_usd"] < trade_value:
                    raise ValueError("Insufficient USD balance.")
                user["balance_usd"] -= trade_value
                user["balance_btc"] += amount
            elif side.lower() == "sell":
                if user["balance_btc"] < amount:
                    raise ValueError("Insufficient BTC balance.")
                user["balance_btc"] -= amount
                user["balance_usd"] += trade_value
            else:
                raise ValueError("Use BUY or SELL.")

            user["last_seen"] = datetime.now(timezone.utc).isoformat()
            self.save_state()
            return {
                "side": side.upper(),
                "btc_amount": round(amount, 8),
                "market_price": round(market_price, 2),
                "trade_value": round(trade_value, 2),
                "new_usd": round(user["balance_usd"], 2),
                "new_btc": round(user["balance_btc"], 8),
            }

    def wallet_for(self, username: str):
        with self.lock:
            user = self.state["users"].get(username)
            if not user:
                raise ValueError("User not found.")
            return {
                "username": user["username"],
                "usd": round(float(user["balance_usd"]), 2),
                "btc": round(float(user["balance_btc"]), 8),
            }


class BBSHandler(socketserver.StreamRequestHandler):
    prompt = "\r\nBBS> "

    HELP_TEXT = {
        "register": "Create a new account with a username and password.",
        "login": "Sign in to your account so you can post, trade, and read boards.",
        "boards": "List all available public bulletin boards.",
        "read": "Show recent posts in a board. Example: READ trading 10.",
        "post": "Post a new message to a board. Example: POST trading BTC is buying.",
        "market": "Display the current simulated BTC spot price and market state.",
        "quote": "Show the current BTC/USD price in one line.",
        "wallet": "Display your USD and BTC account balances.",
        "buy": "Purchase BTC with your USD balance at the current market price.",
        "sell": "Sell BTC from your wallet at the current market price.",
        "who": "List users currently connected to the BBS.",
        "help": "Display help text. Use HELP <command> for command-specific guidance.",
        "logout": "Log out of the current account without disconnecting the client.",
        "quit": "Disconnect from the BBS.",
    }

    def setup(self):
        super().setup()
        self.user = None
        self.write("\x1b[2J\x1b[H")
        self.write("\r\n")
        self.write("     .-.-.   .-.-.   .-.-.    .-.-.     \r\n")
        self.write("   /     \\ /     \\ /     \\  /     \\    \r\n")
        self.write("   | BTC | | BBS | | CHAT |  | BOARD |   \r\n")
        self.write("   \\     / \\     / \\     /  \\     /    \r\n")
        self.write("    `-.-'   `-.-'   `-.-'    `-.-'     \r\n")
        self.write("\r\n")
        self.write("    Bitcoin Trading & Sharing Board\r\n")
        self.write("    ==============================\r\n")
        self.write("\r\n")
        self.write("Welcome to the Bitcoin bell system. Type HELP for commands.\r\n")
        self.write(self.prompt)

    def log(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S")
        sys.stderr.write(f"[{timestamp}] {text}\n")

    def write(self, text):
        if not text:
            return
        try:
            self.wfile.write(text.encode("utf-8", errors="ignore"))
            self.wfile.flush()
        except BrokenPipeError:
            raise

    def strip_telnet_iac(self, raw):
        if isinstance(raw, str):
            return raw
        result = bytearray()
        i = 0
        while i < len(raw):
            b = raw[i]
            if b == 0xFF:
                if i + 1 < len(raw):
                    i += 2
                    continue
            result.append(b)
            i += 1
        return result.decode("utf-8", errors="ignore")

    def read_line(self):
        data = self.rfile.readline()
        if not data:
            return None
        return self.strip_telnet_iac(data).strip()

    def handle(self):
        while True:
            try:
                line = self.read_line()
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.log(f"Lost connection from {self.client_address}")
                self._remove_online_user()
                return

            if line is None or line == "":
                continue

            if line.lower() in {"quit", "exit"}:
                self._remove_online_user()
                self.write("\r\nGood night.\r\n")
                return

            if line.lower() == "logout":
                if self.user:
                    self._remove_online_user()
                    self.user = None
                    self.write("\r\nLogged out.\r\n")
                    self.write(self.prompt)
                    continue
                self.write("\r\nYou are not logged in.\r\n")
                self.write(self.prompt)
                continue

            if not self.user:
                self._handle_guest_command(line)
            else:
                self._handle_authenticated_command(line)

    def _add_online_user(self):
        if not self.user:
            return
        with self.server.online_lock:
            self.server.online_users.add(self.user)

    def _remove_online_user(self):
        if not self.user:
            return
        with self.server.online_lock:
            self.server.online_users.discard(self.user)

    def _handle_guest_command(self, line):
        parts = line.split()
        if not parts:
            self.write(self.prompt)
            return

        cmd = parts[0].lower()

        if cmd == "register" and len(parts) >= 3:
            _, username, password = line.split(maxsplit=2)
            try:
                self.server.bbs.create_user(username, password)
                self.write(f"\r\nAccount created for {username}. You may now login.\r\n")
            except ValueError as exc:
                self.write(f"\r\n{exc}\r\n")
            self.write(self.prompt)
            return

        if cmd == "login" and len(parts) >= 3:
            username, password = parts[1], parts[2]
            user = self.server.bbs.login_user(username, password)
            if not user:
                self.write("\r\nInvalid username or password.\r\n")
                self.write(self.prompt)
                return
            self.user = username
            self._add_online_user()
            self.write(f"\r\nWelcome back, {username}!\r\n")
            self._show_menu()
            self.write(self.prompt)
            return

        if cmd == "help":
            self._show_guest_help()
            self.write(self.prompt)
            return

        self.write("\r\nUnknown command. Try REGISTER <username> <password> or LOGIN <username> <password>.\r\n")
        self.write(self.prompt)

    def _handle_authenticated_command(self, line):
        parts = line.split()
        if not parts:
            self.write(self.prompt)
            return

        cmd = parts[0].lower()

        if cmd == "help":
            if len(parts) > 1:
                target = parts[1].lower()
                self.write(f"\r\n{target.upper()}: {self.HELP_TEXT.get(target, 'No such command.')}\r\n")
            else:
                self._show_menu()
            self.write(self.prompt)
            return

        if cmd == "boards":
            self.write("\r\nAvailable boards:\r\n")
            for board in self.server.bbs.list_boards():
                self.write(f"  - {board}\r\n")
            self.write(self.prompt)
            return

        if cmd == "read":
            if len(parts) < 2:
                self.write("\r\nUsage: READ <board> [limit]\r\n")
                self.write(self.prompt)
                return
            board = parts[1].lower()
            try:
                limit = int(parts[2]) if len(parts) > 2 else 5
            except ValueError:
                self.write("\r\nLimit must be an integer.\r\n")
                self.write(self.prompt)
                return
            try:
                posts = self.server.bbs.get_posts(board, limit)
            except ValueError as exc:
                self.write(f"\r\n{exc}\r\n")
                self.write(self.prompt)
                return

            if not posts:
                self.write(f"\r\nNo posts in {board}.\r\n")
                self.write(self.prompt)
                return

            self.write(f"\r\nRecent posts on {board}:\r\n")
            for post in posts:
                stamp = post["timestamp"][:19].replace("T", " ")
                self.write(f"  [{stamp}] {post['author']}: {post['message']}\r\n")
            self.write(self.prompt)
            return

        if cmd == "post":
            if len(parts) < 3:
                self.write("\r\nUsage: POST <board> <message>\r\n")
                self.write(self.prompt)
                return
            board = parts[1].lower()
            message = line.split(maxsplit=2)[2]
            try:
                self.server.bbs.add_post(board, self.user, message)
                self.write(f"\r\nPosted to {board}.\r\n")
            except ValueError as exc:
                self.write(f"\r\n{exc}\r\n")
            self.write(self.prompt)
            return

        if cmd == "market":
            price = self.server.bbs.get_market_price()
            self.write(f"\r\nBTC Market Quote: ${price:,.2f} USD\r\n")
            self.write("\r\nTrend: simulated by local BBS market engine\r\n")
            self.write(self.prompt)
            return

        if cmd == "quote":
            price = self.server.bbs.get_market_price()
            self.write(f"\r\n1 BTC = ${price:,.2f} USD\r\n")
            self.write(self.prompt)
            return

        if cmd == "buy":
            if len(parts) < 2:
                self.write("\r\nUsage: BUY <btc_amount>\r\n")
                self.write(self.prompt)
                return
            try:
                result = self.server.bbs.trade(self.user, "buy", float(parts[1]))
                self.write(f"\r\nBUY executed: {result['btc_amount']} BTC @ ${result['market_price']:,.2f} USD\r\n")
                self.write(f"USD balance: ${result['new_usd']:,.2f} | BTC balance: {result['new_btc']:.8f}\r\n")
            except ValueError as exc:
                self.write(f"\r\n{exc}\r\n")
            self.write(self.prompt)
            return

        if cmd == "sell":
            if len(parts) < 2:
                self.write("\r\nUsage: SELL <btc_amount>\r\n")
                self.write(self.prompt)
                return
            try:
                result = self.server.bbs.trade(self.user, "sell", float(parts[1]))
                self.write(f"\r\nSELL executed: {result['btc_amount']} BTC @ ${result['market_price']:,.2f} USD\r\n")
                self.write(f"USD balance: ${result['new_usd']:,.2f} | BTC balance: {result['new_btc']:.8f}\r\n")
            except ValueError as exc:
                self.write(f"\r\n{exc}\r\n")
            self.write(self.prompt)
            return

        if cmd == "wallet":
            try:
                wallet = self.server.bbs.wallet_for(self.user)
                self.write(f"\r\nWallet for {wallet['username']}:\r\n")
                self.write(f"  USD: ${wallet['usd']:,.2f}\r\n")
                self.write(f"  BTC: {wallet['btc']:.8f}\r\n")
            except ValueError as exc:
                self.write(f"\r\n{exc}\r\n")
            self.write(self.prompt)
            return

        if cmd == "who":
            with self.server.online_lock:
                online = sorted(self.server.online_users)
            if online:
                self.write("\r\nOnline users:\r\n")
                for user in online:
                    self.write(f"  - {user}\r\n")
            else:
                self.write("\r\nNo other users online.\r\n")
            self.write(self.prompt)
            return

        if cmd == "logout":
            self._remove_online_user()
            self.user = None
            self.write("\r\nLogged out.\r\n")
            self.write(self.prompt)
            return

        if cmd == "refresh":
            self.server.bbs.update_market()
            self.write("\r\nMarket refreshed.\r\n")
            self.write(self.prompt)
            return

        self.write("\r\nUnknown command. Type HELP for options.\r\n")
        self.write(self.prompt)

    def _show_menu(self):
        self.write("\r\nBBS commands:\r\n")
        self.write("  BOARDS\r\n")
        self.write("  READ <board> [limit]\r\n")
        self.write("  POST <board> <message>\r\n")
        self.write("  MARKET\r\n")
        self.write("  QUOTE\r\n")
        self.write("  WALLET\r\n")
        self.write("  BUY <btc_amount>\r\n")
        self.write("  SELL <btc_amount>\r\n")
        self.write("  WHO\r\n")
        self.write("  LOGOUT\r\n")
        self.write("  HELP\r\n")

    def _show_guest_help(self):
        self.write("\r\nGuest commands:\r\n")
        self.write("  REGISTER <username> <password>\r\n")
        self.write("  LOGIN <username> <password>\r\n")
        self.write("  HELP\r\n")


class ThreadedBBS(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, handler_class):
        self.bbs = BBSState(STATE_PATH)
        self.online_users = set()
        self.online_lock = threading.RLock()
        self.market_thread = threading.Thread(target=self._market_loop, name="BBSMarketLoop", daemon=True)
        super().__init__(server_address, handler_class)

    def _market_loop(self):
        while True:
            time.sleep(MARKET_TICK_SECONDS)
            try:
                self.bbs.update_market()
            except Exception as exc:
                sys.stderr.write(f"Market thread error: {exc}\n")


def main():
    host = os.environ.get("BBS_HOST", "0.0.0.0")
    port = int(os.environ.get("BBS_PORT", "2323"))
    server = ThreadedBBS((host, port), BBSHandler)
    server.market_thread.start()
    print(f"Starting Bitcoin BBS on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down BBS.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
