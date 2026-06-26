"""
spx_option_greeks.py
SPX 期权 Greeks 计算模块 (基于 Black-Scholes-Merton 模型)
支持 0DTE (零日到期) 期权
"""

import math
from scipy.stats import norm
from datetime import datetime, timedelta
from typing import Optional


class SPXOptionGreeks:
    """
    SPX 期权 Greeks 计算器
    
    使用 Black-Scholes-Merton 模型计算欧式期权的 Greeks。
    支持 0DTE (到期日当天) 期权。
    """
    
    # SPX 合约乘数
    MULTIPLIER = 100
    
    # 年度交易日数 (用于 Theta 计算)
    TRADING_DAYS_PER_YEAR = 252
    
    def __init__(
        self,
        spot: float,
        strike: float,
        expiry: datetime,
        right: str,  # 'C' 或 'P'
        risk_free_rate: float = 0.045,  # 当前无风险利率 (约 4.5%)
        dividend_yield: float = 0.012,  # SPX 股息收益率 (约 1.2%)
        implied_vol: Optional[float] = None,
        option_price: Optional[float] = None,
        valuation_date: Optional[datetime] = None,
    ):
        """
        初始化期权 Greeks 计算器
        
        Args:
            spot: SPX 当前点位
            strike: 行权价
            expiry: 到期日 (datetime)
            right: 'C' (看涨) 或 'P' (看跌)
            risk_free_rate: 无风险利率 (例如 0.045 表示 4.5%)
            dividend_yield: 股息收益率 (例如 0.012 表示 1.2%)
            implied_vol: 隐含波动率 (例如 0.20 表示 20%)
            option_price: 期权市场价格 (如果未提供 implied_vol，用于反推 IV)
            valuation_date: 估值日期 (默认为当前时间)
        """
        self.spot = spot
        self.strike = strike
        self.expiry = expiry
        self.right = right.upper()
        self.r = risk_free_rate
        self.q = dividend_yield
        self.valuation_date = valuation_date or datetime.now()
        
        # 计算剩余到期时间 (年)
        self.T = self._time_to_expiry()
        
        # 如果提供了期权价格但没有 IV，反推 IV
        if implied_vol is not None:
            self.sigma = implied_vol
        elif option_price is not None:
            self.sigma = self._implied_volatility(option_price)
        else:
            raise ValueError("必须提供 implied_vol 或 option_price")
        
        # 计算 d1 和 d2
        self.d1 = self._calculate_d1()
        self.d2 = self.d1 - self.sigma * math.sqrt(self.T)
        
        # 计算 Greeks
        self._compute_greeks()
    
    def _time_to_expiry(self) -> float:
        """计算剩余到期时间 (年)，支持 0DTE"""
        delta = self.expiry - self.valuation_date
        # 对于 0DTE，T 非常小但不为零
        # 使用交易日历天数 / 252 更精确
        seconds = delta.total_seconds()
        if seconds <= 0:
            # 如果已到期，使用极小值避免除零
            return 1e-8
        # 转换为年 (使用 365 天近似，或 252 交易日)
        return seconds / (365 * 24 * 3600)
    
    def _calculate_d1(self) -> float:
        """计算 d1"""
        if self.T <= 0:
            # 到期时 d1 趋向无穷
            return float('inf') if self.spot > self.strike else float('-inf')
        
        numerator = math.log(self.spot / self.strike) + (self.r - self.q + 0.5 * self.sigma ** 2) * self.T
        denominator = self.sigma * math.sqrt(self.T)
        return numerator / denominator
    
    def _compute_greeks(self):
        """计算所有 Greeks"""
        # 标准正态分布 PDF 和 CDF
        phi = norm.pdf(self.d1)  # PDF
        N_d1 = norm.cdf(self.d1)  # CDF
        
        if self.right == 'C':
            # 看涨期权 Delta
            self.delta = N_d1
        else:
            # 看跌期权 Delta
            self.delta = N_d1 - 1
        
        # Gamma (看涨和看跌相同)
        self.gamma = phi / (self.spot * self.sigma * math.sqrt(self.T)) if self.T > 0 else 0
        
        # Vega (每 1% IV 变化的价格变化)
        self.vega = self.spot * phi * math.sqrt(self.T) / 100 if self.T > 0 else 0
        
        # Theta (每日时间衰减)
        if self.T > 0:
            sqrt_T = math.sqrt(self.T)
            term1 = -self.spot * phi * self.sigma / (2 * sqrt_T)
            
            if self.right == 'C':
                term2 = -self.r * self.strike * math.exp(-self.r * self.T) * norm.cdf(self.d2)
                term3 = self.q * self.spot * math.exp(-self.q * self.T) * N_d1
            else:
                term2 = self.r * self.strike * math.exp(-self.r * self.T) * norm.cdf(-self.d2)
                term3 = -self.q * self.spot * math.exp(-self.q * self.T) * norm.cdf(-self.d1)
            
            # Theta (年化)
            self.theta_annual = term1 + term2 + term3
            # Theta (每日, 使用交易日)
            self.theta_daily = self.theta_annual / self.TRADING_DAYS_PER_YEAR
        else:
            self.theta_annual = 0
            self.theta_daily = 0
        
        # 期权理论价格 (BSM 价格)
        self._compute_price()
    
    def _compute_price(self):
        """计算 BSM 理论价格"""
        if self.T <= 0:
            # 到期时: 内在价值
            intrinsic = self.spot - self.strike if self.right == 'C' else self.strike - self.spot
            self.price = max(0, intrinsic)
            return
        
        sqrt_T = math.sqrt(self.T)
        d1 = self.d1
        d2 = self.d2
        
        if self.right == 'C':
            # 看涨期权价格
            self.price = (
                self.spot * math.exp(-self.q * self.T) * norm.cdf(d1)
                - self.strike * math.exp(-self.r * self.T) * norm.cdf(d2)
            )
        else:
            # 看跌期权价格
            self.price = (
                self.strike * math.exp(-self.r * self.T) * norm.cdf(-d2)
                - self.spot * math.exp(-self.q * self.T) * norm.cdf(-d1)
            )
    
    def _implied_volatility(self, market_price: float, tol: float = 1e-6, max_iter: int = 100) -> float:
        """
        使用二分法从市场价格反推隐含波动率
        """
        if self.T <= 0:
            intrinsic = max(0, self.spot - self.strike) if self.right == 'C' else max(0, self.strike - self.spot)
            if abs(market_price - intrinsic) < tol:
                return 0.0
            raise ValueError("已到期期权无法反推隐含波动率")
        
        # 二分法搜索
        low = 0.001
        high = 5.0
        
        for _ in range(max_iter):
            mid = (low + high) / 2
            # 临时计算价格
            d1 = (math.log(self.spot / self.strike) + (self.r - self.q + 0.5 * mid ** 2) * self.T) / (mid * math.sqrt(self.T))
            d2 = d1 - mid * math.sqrt(self.T)
            
            if self.right == 'C':
                price = self.spot * math.exp(-self.q * self.T) * norm.cdf(d1) - self.strike * math.exp(-self.r * self.T) * norm.cdf(d2)
            else:
                price = self.strike * math.exp(-self.r * self.T) * norm.cdf(-d2) - self.spot * math.exp(-self.q * self.T) * norm.cdf(-d1)
            
            if abs(price - market_price) < tol:
                return mid
            
            if price < market_price:
                low = mid
            else:
                high = mid
        
        return (low + high) / 2
    
    # ========== 合约级别 Delta (名义 Delta) ==========
    
    @property
    def contract_delta(self) -> float:
        """
        合约 Delta (名义 Delta)
        = 原始 Delta × SPX 乘数 (100)
        表示每 1 点 SPX 变动对应的美元盈亏
        """
        return self.delta * self.MULTIPLIER
    
    @property
    def contract_gamma(self) -> float:
        """合约 Gamma (每 1 点 SPX 变动的 Delta 变化 × 100)"""
        return self.gamma * self.MULTIPLIER
    
    @property
    def contract_vega(self) -> float:
        """合约 Vega (每 1% IV 变动的美元盈亏)"""
        return self.vega * self.MULTIPLIER
    
    @property
    def contract_theta(self) -> float:
        """合约 Theta (每日时间衰减的美元盈亏)"""
        return self.theta_daily * self.MULTIPLIER
    
    def summary(self) -> dict:
        """返回完整的 Greeks 摘要"""
        return {
            'spot': self.spot,
            'strike': self.strike,
            'right': self.right,
            'T': self.T,
            'sigma': self.sigma,
            'd1': self.d1,
            'd2': self.d2,
            'price': self.price,
            'delta': self.delta,
            'contract_delta': self.contract_delta,
            'gamma': self.gamma,
            'contract_gamma': self.contract_gamma,
            'vega': self.vega,
            'contract_vega': self.contract_vega,
            'theta_daily': self.theta_daily,
            'contract_theta': self.contract_theta,
            'theta_annual': self.theta_annual,
        }


# ========== 便捷函数 ==========

def calculate_spx_delta(
    spot: float,
    strike: float,
    expiry: datetime,
    right: str,
    implied_vol: float,
    risk_free_rate: float = 0.045,
    dividend_yield: float = 0.012,
) -> dict:
    """
    快速计算 SPX 期权 Delta
    
    Args:
        spot: SPX 当前点位
        strike: 行权价
        expiry: 到期日
        right: 'C' 或 'P'
        implied_vol: 隐含波动率
        risk_free_rate: 无风险利率
        dividend_yield: 股息收益率
    
    Returns:
        包含 Delta 和合约 Delta 的字典
    """
    greeks = SPXOptionGreeks(
        spot=spot,
        strike=strike,
        expiry=expiry,
        right=right,
        implied_vol=implied_vol,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
    )
    return {
        'delta': greeks.delta,
        'contract_delta': greeks.contract_delta,
        'gamma': greeks.gamma,
        'vega': greeks.vega,
        'theta': greeks.theta_daily,
        'price': greeks.price,
    }