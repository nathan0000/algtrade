from ibapi.contract import Contract
from config import logger

# Hardcoded fail-safe IDs for the current cycle (April/May 2026)
FALLBACK_CONIDS = {
    "VX_FRONT": 800674914,  # April 15, 2026 (VXJ6)
    "VX_BACK": 812239044    # May 20, 2026 (VXM6)
}

def get_es_contract():
    contract = Contract()
    contract.symbol, contract.secType, contract.exchange, contract.currency = "ES", "FUT", "CME", "USD"
    contract.lastTradeDateOrContractMonth = "202606" 
    return contract

def get_vx_contract(offset_months=0, resolved_conid=None):
    contract = Contract()
    contract.symbol, contract.secType, contract.exchange, contract.currency = "VX", "FUT", "CFE", "USD"
    
    if resolved_conid:
        contract.conId = resolved_conid
    else:
        key = "VX_FRONT" if offset_months == 0 else "VX_BACK"
        contract.conId = FALLBACK_CONIDS[key]
        logger.warning(f"⚠️ Falling back to hardcoded ConId for {key}: {contract.conId}")
    
    return contract