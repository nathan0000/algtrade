# SPX 0DTE IBKR Bot — Setup Guide

## Prerequisites

### 1. Install IBKR API
Download the official IBKR Python API from:
https://interactivebrokers.github.io/tws-api/index.html

Then install it:
```bash
cd ~/ibkr_api/source/pythonclient
python setup.py install
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure TWS / IB Gateway
In TWS or IB Gateway:
- Go to: Edit → Global Configuration → API → Settings
- ✅ Enable ActiveX and Socket Clients
- ✅ Read-Only API: OFF (bot needs to place orders)
- Socket port: **7497** (paper) or **7496** (live)
- ✅ Allow connections from localhost (127.0.0.1)
- Trusted IP: 127.0.0.1

---

## Configuration (edit Config class in bot)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `HOST` | 127.0.0.1 | TWS/Gateway IP |
| `PORT` | 7497 | 7497=paper TWS, 7496=live TWS, 4002=paper GW |
| `ACCOUNT` | DU000000 | Your paper account ID |
| `MAX_ACCOUNT_RISK_PCT` | 2.5% | Daily max loss |
| `TRADE_RISK_PCT_IC` | 1.0% | Risk per Iron Condor |
| `TRADE_RISK_PCT_BWB` | 0.75% | Risk per BWB |
| `TRADE_RISK_PCT_VS` | 1.0% | Risk per Vertical Scalp |

---

## Running the Bot

### Paper trading (recommended first)
```bash
python spx_0dte_bot.py --port 7497 --account DU123456
```

### Live trading (CAUTION)
```bash
python spx_0dte_bot.py --port 7496 --account U123456
```

### With custom host (IB Gateway on another machine)
```bash
python spx_0dte_bot.py --host 192.168.1.100 --port 4002 --account DU123456
```

---

## Architecture Overview

```
SPX0DTEBot
├── IBKRWrapper        ← All TWS callbacks (prices, fills, account)
├── IBKRClient         ← Sends requests to TWS
├── RulesEngine        ← All pre-trade filters & kill switches
│   ├── VIX regime check
│   ├── Timing windows
│   ├── Kill switches
│   └── Setup scorer (0–100)
├── StrategyBuilder    ← Constructs leg structures
│   ├── build_iron_condor()
│   ├── build_bwb()
│   └── build_vertical()
├── PositionMonitor    ← Tracks open trades, fires exits
│   ├── Profit target exits
│   ├── Stop loss exits
│   └── Hard close at 2pm
└── VWAPCalculator     ← Session VWAP, trend/range detection
```

---

## Kill Switches (auto-triggers)
- VIX spike +2pts in 30 minutes → close ALL
- Daily loss > 2.5% account → done for day
- 2 consecutive losses → pause
- Past 2:00pm ET → close ALL

---

## Strategy Entry Logic

| Strategy | Trigger | Time Window |
|----------|---------|-------------|
| Iron Condor | is_range_bound() + VIX 13–20 | 10:30–11:30am |
| BWB | is_bullish/bearish_trend() + VIX 14–22 | 10:00–11:30am |
| Vertical | trend confirmation + score ≥ 70 | 10:30–12:30pm |

---

## Checklist Before Going Live
- [ ] Run on paper for minimum 20 sessions
- [ ] Verify fills match expected credits/debits
- [ ] Confirm VWAP bars load correctly at open
- [ ] Test kill switch by watching VIX spike behavior
- [ ] Validate hard close at 2:00pm fires correctly
- [ ] Review logs daily: `tail -f spx_0dte.log`

---

## Disclaimer
This software is for educational purposes. Options trading involves
substantial risk of loss. Always paper trade first. The author is not
responsible for any financial losses.
