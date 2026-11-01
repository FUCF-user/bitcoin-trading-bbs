# Bitcoin Trading & Sharing BBS

A classic terminal bulletin board system for Bitcoin trading, market chatter, and community discussion.

Features:
- Telnet server for SynCTerm and standard telnet clients
- User registration and login
- Four boards: announcements, trading, market, and general
- Persistent JSON state storage
- Simulated BTC wallet and market pricing
- Buy and sell commands using BTC/USD pricing
- Background market tick update thread

## Run it

```bash
chmod +x start_bbs.sh
./start_bbs.sh
```

Or directly:

```bash
python3 bitcoin_bbs.py
```

Then connect with:

```bash
telnet localhost 2323
```

Or in SynCTerm:

```text
connect localhost 2323
```

## Configuration

The server supports environment variables:

```bash
BBS_HOST=127.0.0.1 BBS_PORT=9000 python3 bitcoin_bbs.py
```

Default values:
- Host: 0.0.0.0
- Port: 2323

## Commands

Guest commands:
- `register <username> <password>`
- `login <username> <password>`
- `help`

Authenticated commands:
- `boards`
- `read <board> [limit]`
- `post <board> <message>`
- `market`
- `quote`
- `wallet`
- `buy <btc_amount>`
- `sell <btc_amount>`
- `who`
- `help [command]`
- `logout`
- `quit`

## Notes

This is a lightweight local BBS for hobbyist use and terminal communities. It stores its state in `data/bbs_state.json` and simulates a BTC market for trading experiments.
